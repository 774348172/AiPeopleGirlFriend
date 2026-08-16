from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, TypeAlias
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PlanAction: TypeAlias = Literal["create", "confirm", "cancel", "complete"]
PlanKind: TypeAlias = Literal["promise", "reminder", "shared_plan"]
ParseDisposition: TypeAlias = Literal[
    "no_command", "executable", "needs_confirmation"
]


@dataclass(frozen=True, slots=True)
class ProspectiveCommand:
    action: PlanAction
    kind: PlanKind
    description: str | None
    target_phrase: str | None
    due_at: datetime | None
    timezone: str
    initial_state: Literal["proposed", "confirmed"] | None

    def __post_init__(self) -> None:
        if self.action == "create":
            if not self.description or self.target_phrase is not None:
                raise ValueError("create command requires only a description")
            if self.initial_state not in ("proposed", "confirmed"):
                raise ValueError("create command requires an initial state")
        else:
            if not self.target_phrase or self.description is not None:
                raise ValueError("transition command requires only a target phrase")
            if self.initial_state is not None:
                raise ValueError("transition command cannot set an initial state")
        if self.due_at is not None and self.due_at.utcoffset() is None:
            raise ValueError("due_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ProspectiveParseResult:
    disposition: ParseDisposition
    command: ProspectiveCommand | None
    reason: str

    def __post_init__(self) -> None:
        if self.disposition == "no_command":
            if self.command is not None:
                raise ValueError("no_command result cannot contain a command")
        elif self.command is None:
            raise ValueError("command result must contain a command")


@dataclass(frozen=True, slots=True)
class _DueResolution:
    due_at: datetime | None
    has_time_signal: bool
    reason: str


_KIND_LABELS: dict[str, PlanKind] = {
    "提醒": "reminder",
    "计划": "shared_plan",
    "承诺": "promise",
}
_GENERIC_TARGETS = frozenset(
    {"这个", "那个", "这个提醒", "那个提醒", "刚才的", "之前的", "它"}
)
_UNCERTAIN_TIME = re.compile(
    r"周末|下周|以后|过几天|改天|有空时|待会儿?|晚点|稍后|"
    r"某天|哪天|早些时候|下午|晚上|上午|早上"
)
_UNCERTAINTY_MARKER = re.compile(
    r"左右|大概|差不多|前后|任选|或者|或是|"
    r"\d{1,2}\s*点\s*(?:到|至|或)|(?:到|至|或)\s*\d{1,2}\s*点"
)
_QUESTION_END = re.compile(r"(?:吗|么|嘛|是不是|行不行)[？?。！!]*$")
_CONDITIONAL_START = re.compile(r"^(?:如果|要是|假如|万一|听说|她说|他说)")
_NEGATED_REMINDER = re.compile(r"^(?:请)?(?:不要|不用|别)(?:再)?提醒我")

_ISO_DATETIME = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
    r"[ T](?P<hour>\d{1,2}):(?P<minute>\d{2})"
)
_ZH_DATETIME = re.compile(
    r"(?P<date>今天|明天|后天|\d{4}年\d{1,2}月\d{1,2}日)\s*"
    r"(?P<period>凌晨|早上|上午|中午|下午|晚上)?\s*"
    r"(?P<hour>\d{1,2})(?:"
    r"(?::(?P<colon_minute>\d{2}))|"
    r"(?:点|时)(?:(?P<minute>\d{1,2})分?|(?P<half>半))?"
    r")"
)
_TIME_SIGNAL = re.compile(
    r"今天|明天|后天|周末|下周|以后|过几天|改天|待会儿?|晚点|稍后|"
    r"\d{4}[-年]\d{1,2}(?:[-月]\d{1,2})?|"
    r"\d{1,2}\s*(?:点|时)|\d{1,2}:\d{2}|凌晨|早上|上午|中午|下午|晚上"
)
_BARE_TIME = re.compile(
    r"(?:凌晨|早上|上午|中午|下午|晚上)?\s*\d{1,2}"
    r"(?::\d{2}|(?:点|时)(?:\d{1,2}分?|半)?)"
)

_TRANSITION_PATTERNS: tuple[tuple[PlanAction, re.Pattern[str]], ...] = (
    (
        "cancel",
        re.compile(r"^(?:请)?取消(?P<label>提醒|计划|承诺)[：:\s]+(?P<body>.+)$"),
    ),
    (
        "complete",
        re.compile(
            r"^(?P<label>提醒|计划|承诺)[：:\s]+(?P<body>.+?)"
            r"(?:已经|已)?完成(?:了)?[。！!]*$"
        ),
    ),
    (
        "complete",
        re.compile(
            r"^(?:我)?(?:已经|已)?完成(?:了)?(?P<label>提醒|计划|承诺)"
            r"[：:\s]+(?P<body>.+)$"
        ),
    ),
    (
        "confirm",
        re.compile(r"^(?:请)?确认(?P<label>提醒|计划|承诺)[：:\s]+(?P<body>.+)$"),
    ),
)


def parse_prospective_command(
    text: str,
    *,
    occurred_at: datetime,
    timezone_name: str,
) -> ProspectiveParseResult:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if occurred_at.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")
    zone = _zone(timezone_name)
    normalized = " ".join(text.split()).strip()
    if not normalized or len(normalized) > 1000:
        return _none("empty_or_too_long")
    if _CONDITIONAL_START.search(normalized):
        return _none("conditional_or_reported_speech")

    due = _resolve_due(normalized, occurred_at, zone)
    transition = _parse_transition(normalized, timezone_name, due)
    if transition is not None:
        return transition
    return _parse_create(normalized, timezone_name, due)


def _parse_transition(
    text: str, timezone_name: str, due: _DueResolution
) -> ProspectiveParseResult | None:
    for action, pattern in _TRANSITION_PATTERNS:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        if _QUESTION_END.search(text):
            return _none("question_not_command")
        kind = _KIND_LABELS[match.group("label")]
        target = _target_phrase(match.group("body"))
        if target is None:
            return _none("ambiguous_target")
        command = ProspectiveCommand(
            action=action,
            kind=kind,
            description=None,
            target_phrase=target,
            due_at=due.due_at if action == "confirm" else None,
            timezone=timezone_name,
            initial_state=None,
        )
        if action == "confirm" and kind in ("reminder", "shared_plan"):
            if due.due_at is None:
                return ProspectiveParseResult(
                    "needs_confirmation", command, due.reason or "exact_time_required"
                )
        return ProspectiveParseResult("executable", command, "explicit_transition")
    return None


def _parse_create(
    text: str, timezone_name: str, due: _DueResolution
) -> ProspectiveParseResult:
    if _NEGATED_REMINDER.search(text) or _QUESTION_END.search(text):
        return _none("negated_or_question")

    reminder = re.fullmatch(r"(?P<prefix>.*?)提醒我(?P<body>.+)", text)
    if reminder is not None and _valid_reminder_prefix(reminder.group("prefix")):
        description = _description(reminder.group("body"))
        if description is None:
            return _none("missing_description")
        return _create_result(
            "reminder", description, timezone_name, due, exact_time_required=True
        )

    shared = re.fullmatch(r"(?:我们)?(?:约好|计划|决定)(?:在)?(?P<body>.+)", text)
    if shared is not None:
        description = _description(shared.group("body"))
        if description is None:
            return _none("missing_description")
        return _create_result(
            "shared_plan", description, timezone_name, due, exact_time_required=True
        )

    promise = re.fullmatch(r"(?:我)?答应你(?P<body>.+)", text)
    if promise is None:
        promise = re.fullmatch(
            r"(?:我)?保证(?:我)?(?P<body>(?:会|要|不再|以后).+)", text
        )
    if promise is not None:
        description = _description(promise.group("body"))
        if description is None:
            return _none("missing_description")
        return _create_result(
            "promise", description, timezone_name, due, exact_time_required=False
        )
    return _none("no_explicit_plan_command")


def _create_result(
    kind: PlanKind,
    description: str,
    timezone_name: str,
    due: _DueResolution,
    *,
    exact_time_required: bool,
) -> ProspectiveParseResult:
    needs_confirmation = due.due_at is None and (
        exact_time_required or due.has_time_signal
    )
    initial_state: Literal["proposed", "confirmed"] = (
        "proposed" if needs_confirmation else "confirmed"
    )
    command = ProspectiveCommand(
        action="create",
        kind=kind,
        description=description,
        target_phrase=None,
        due_at=due.due_at,
        timezone=timezone_name,
        initial_state=initial_state,
    )
    return ProspectiveParseResult(
        "needs_confirmation" if needs_confirmation else "executable",
        command,
        due.reason if needs_confirmation else "explicit_create",
    )


def _resolve_due(text: str, occurred_at: datetime, zone: ZoneInfo) -> _DueResolution:
    has_signal = _TIME_SIGNAL.search(text) is not None
    if _UNCERTAINTY_MARKER.search(text):
        return _DueResolution(None, has_signal, "ambiguous_time")
    matches: list[tuple[re.Match[str], str]] = [
        *((match, "iso") for match in _ISO_DATETIME.finditer(text)),
        *((match, "zh") for match in _ZH_DATETIME.finditer(text)),
    ]
    if len(matches) != 1:
        reason = "multiple_times" if len(matches) > 1 else "exact_time_required"
        return _DueResolution(None, has_signal, reason)
    match, style = matches[0]
    local_now = occurred_at.astimezone(zone)
    try:
        if style == "iso":
            year = int(match.group("year"))
            month = int(match.group("month"))
            day = int(match.group("day"))
            hour = int(match.group("hour"))
            minute = int(match.group("minute"))
        else:
            date_label = match.group("date")
            if date_label in ("今天", "明天", "后天"):
                offset = {"今天": 0, "明天": 1, "后天": 2}[date_label]
                date_value = local_now.date() + timedelta(days=offset)
                year, month, day = date_value.year, date_value.month, date_value.day
            else:
                date_match = re.fullmatch(
                    r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_label
                )
                if date_match is None:
                    return _DueResolution(None, has_signal, "invalid_time")
                year, month, day = map(int, date_match.groups())
            hour = int(match.group("hour"))
            minute = int(
                match.group("colon_minute")
                or match.group("minute")
                or (30 if match.group("half") else 0)
            )
            hour = _convert_hour(hour, match.group("period"))
        local_due = datetime(year, month, day, hour, minute, tzinfo=zone)
    except (TypeError, ValueError):
        return _DueResolution(None, has_signal, "invalid_time")
    round_trip = local_due.astimezone(timezone.utc).astimezone(zone)
    if round_trip.replace(fold=local_due.fold) != local_due:
        return _DueResolution(None, has_signal, "nonexistent_local_time")
    if local_due <= local_now:
        return _DueResolution(None, True, "past_time")
    return _DueResolution(local_due.astimezone(timezone.utc), True, "exact_time")


def _convert_hour(hour: int, period: str | None) -> int:
    if not 0 <= hour <= 23:
        raise ValueError("hour out of range")
    if period in ("下午", "晚上", "中午") and hour < 12:
        return hour + 12
    if period in ("凌晨", "早上", "上午") and hour == 12:
        return 0
    return hour


def _valid_reminder_prefix(value: str) -> bool:
    cleaned = _strip_time(value)
    cleaned = re.sub(r"[，,：:\s]", "", cleaned)
    cleaned = re.sub(r"^(?:或|到|至)$", "", cleaned)
    return cleaned in {"", "请", "在", "于", "请在", "请于", "麻烦你", "麻烦你在", "麻烦你于"}


def _description(value: str) -> str | None:
    cleaned = _strip_time(value)
    cleaned = re.sub(r"^(?:时间(?:是|定在)?|定在|在|于)", "", cleaned)
    cleaned = " ".join(cleaned.split()).strip(" ，,。？?！!：:；;")
    if not cleaned or len(cleaned) > 500 or _QUESTION_END.search(cleaned):
        return None
    return cleaned


def _target_phrase(value: str) -> str | None:
    value = re.split(
        r"[，,；;]\s*(?:时间(?:是|定在)?|定在|改为)", value, maxsplit=1
    )[0]
    target = " ".join(value.split()).strip(" ，,。？?！!：:；;")
    if target is None or target in _GENERIC_TARGETS or len(target) < 2:
        return None
    return target


def _strip_time(value: str) -> str:
    cleaned = _ISO_DATETIME.sub("", value)
    cleaned = _ZH_DATETIME.sub("", cleaned)
    cleaned = _BARE_TIME.sub("", cleaned)
    cleaned = _UNCERTAIN_TIME.sub("", cleaned)
    cleaned = _UNCERTAINTY_MARKER.sub("", cleaned)
    return cleaned


def _zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("timezone must be a valid IANA timezone") from error


def _none(reason: str) -> ProspectiveParseResult:
    return ProspectiveParseResult("no_command", None, reason)
