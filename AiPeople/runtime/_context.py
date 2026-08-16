from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal, TypeAlias

from ._ledger import EpochSnapshot, EventLedger, RecallEpisode
from ._selected_memory import SelectedMemoryFrame


ContextRole: TypeAlias = Literal["user", "assistant"]
RecallActor: TypeAlias = Literal["user", "character"]
RecallUncertainty: TypeAlias = Literal["none", "no_query", "no_match", "ambiguous"]
RecallQueryKind: TypeAlias = Literal[
    "explicit_phrase", "explicit_time", "explicit_event"
]
RecallCueKind: TypeAlias = Literal["intent", "time", "phrase"]
PromptMeasurer: TypeAlias = Callable[["ReplyContext"], Awaitable[int]]


class ContextBudgetExceeded(RuntimeError):
    def __init__(self, prompt_tokens: int, maximum_prompt_tokens: int) -> None:
        super().__init__("final prompt exceeds the configured context budget")
        self.prompt_tokens = prompt_tokens
        self.maximum_prompt_tokens = maximum_prompt_tokens


@dataclass(frozen=True, slots=True)
class ContextMessage:
    event_id: str
    role: ContextRole
    text: str
    sequence_no: int
    occurred_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.event_id, "event_id")
        if self.role not in ("user", "assistant"):
            raise ValueError("role must be user or assistant")
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if self.sequence_no <= 0:
            raise ValueError("sequence_no must be positive")
        _require_aware_datetime(self.occurred_at, "occurred_at")


@dataclass(frozen=True, slots=True)
class RecallCue:
    kind: RecallCueKind
    source_text: str
    normalized_value: str
    range_start: datetime | None = None
    range_end: datetime | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("intent", "time", "phrase"):
            raise ValueError("invalid recall cue kind")
        _require_text(self.source_text, "source_text")
        _require_text(self.normalized_value, "normalized_value")
        if self.kind == "time":
            _require_aware_datetime(self.range_start, "range_start")
            _require_aware_datetime(self.range_end, "range_end")
            if self.range_start >= self.range_end:
                raise ValueError("recall time range must be increasing")
        elif self.range_start is not None or self.range_end is not None:
            raise ValueError("only time cues may contain a time range")


@dataclass(frozen=True, slots=True)
class WorkingActivation:
    epoch_id: str
    recent_verbatim_event_ids: tuple[str, ...]
    last_completed_user_event_id: str | None
    last_completed_character_event_id: str | None
    unresolved_user_event_ids: tuple[str, ...]
    explicit_recall_cues: tuple[RecallCue, ...]
    current_time: datetime
    current_timezone: str

    def __post_init__(self) -> None:
        _require_text(self.epoch_id, "epoch_id")
        _require_unique_ids(
            self.recent_verbatim_event_ids,
            "recent_verbatim_event_ids",
            allow_empty=True,
        )
        _require_optional_id(
            self.last_completed_user_event_id, "last_completed_user_event_id"
        )
        _require_optional_id(
            self.last_completed_character_event_id,
            "last_completed_character_event_id",
        )
        _require_unique_ids(
            self.unresolved_user_event_ids,
            "unresolved_user_event_ids",
            allow_empty=True,
        )
        if not isinstance(self.explicit_recall_cues, tuple):
            raise TypeError("explicit_recall_cues must be a tuple")
        if len(self.explicit_recall_cues) > 8:
            raise ValueError("explicit_recall_cues exceeds the bounded limit")
        if any(not isinstance(cue, RecallCue) for cue in self.explicit_recall_cues):
            raise TypeError("explicit_recall_cues must contain RecallCue values")
        _require_aware_datetime(self.current_time, "current_time")
        _require_text(self.current_timezone, "current_timezone")


@dataclass(frozen=True, slots=True)
class RecallEvidence:
    anchor_event_id: str
    event_ids: tuple[str, ...]
    occurred_at: datetime
    actor: RecallActor
    verbatim_excerpt: str
    rank_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.anchor_event_id, "anchor_event_id")
        _require_unique_ids(self.event_ids, "event_ids")
        if self.anchor_event_id not in self.event_ids:
            raise ValueError("anchor_event_id must be included in event_ids")
        _require_aware_datetime(self.occurred_at, "occurred_at")
        if self.actor not in ("user", "character"):
            raise ValueError("actor must be user or character")
        if not isinstance(self.verbatim_excerpt, str) or not self.verbatim_excerpt:
            raise ValueError("verbatim_excerpt cannot be empty")
        for reason in self.rank_reasons:
            _require_text(reason, "rank_reasons item")


@dataclass(frozen=True, slots=True)
class RecallFrame:
    evidence: tuple[RecallEvidence, ...]
    source_event_ids: tuple[str, ...]
    uncertainty: RecallUncertainty
    query_kind: RecallQueryKind | None

    def __post_init__(self) -> None:
        _require_unique_ids(self.source_event_ids, "source_event_ids", allow_empty=True)
        if self.uncertainty not in ("none", "no_query", "no_match", "ambiguous"):
            raise ValueError("invalid recall uncertainty")
        if self.query_kind not in (
            None,
            "explicit_phrase",
            "explicit_time",
            "explicit_event",
        ):
            raise ValueError("invalid recall query_kind")
        if self.uncertainty == "no_query":
            if self.query_kind is not None or self.evidence or self.source_event_ids:
                raise ValueError("no_query recall frame cannot contain a query or evidence")
            return
        if self.query_kind is None:
            raise ValueError("queried recall frame requires query_kind")
        if self.evidence and self.uncertainty != "none":
            raise ValueError("evidence requires uncertainty=none")
        expected_ids = tuple(
            dict.fromkeys(
                event_id
                for item in self.evidence
                for event_id in item.event_ids
            )
        )
        if self.source_event_ids != expected_ids:
            raise ValueError("source_event_ids must match evidence injection order")


@dataclass(frozen=True, slots=True)
class ContextBudget:
    context_size: int
    fixed_prompt_tokens: int
    history_tokens: int
    activation_tokens: int
    recall_tokens: int
    current_input_tokens: int
    reply_reserve_tokens: int
    safety_margin_tokens: int
    total_prompt_tokens: int

    def __post_init__(self) -> None:
        if self.context_size <= 0:
            raise ValueError("context_size must be positive")
        counts = (
            self.fixed_prompt_tokens,
            self.history_tokens,
            self.activation_tokens,
            self.recall_tokens,
            self.current_input_tokens,
            self.reply_reserve_tokens,
            self.safety_margin_tokens,
            self.total_prompt_tokens,
        )
        if any(not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("token counts must be non-negative integers")
        measured_total = sum(
            (
                self.fixed_prompt_tokens,
                self.history_tokens,
                self.activation_tokens,
                self.recall_tokens,
                self.current_input_tokens,
            )
        )
        if self.total_prompt_tokens != measured_total:
            raise ValueError("total_prompt_tokens must equal its measured parts")
        if self.reply_reserve_tokens + self.safety_margin_tokens >= self.context_size:
            raise ValueError("reply reserve and safety margin must leave prompt capacity")

    @property
    def maximum_prompt_tokens(self) -> int:
        return self.context_size - self.reply_reserve_tokens - self.safety_margin_tokens

    @property
    def fits(self) -> bool:
        return self.total_prompt_tokens <= self.maximum_prompt_tokens


@dataclass(frozen=True, slots=True)
class ReplyContext:
    epoch_id: str
    history_messages: tuple[ContextMessage, ...]
    working_activation: WorkingActivation
    recall_frame: RecallFrame
    current_user_event_id: str
    current_user_text: str
    budget: ContextBudget
    dynamic_context_enabled: bool = True
    selected_memory_frame: SelectedMemoryFrame | None = None

    def __post_init__(self) -> None:
        _require_text(self.epoch_id, "epoch_id")
        _require_text(self.current_user_event_id, "current_user_event_id")
        if not isinstance(self.current_user_text, str):
            raise TypeError("current_user_text must be a string")
        if not isinstance(self.dynamic_context_enabled, bool):
            raise TypeError("dynamic_context_enabled must be a bool")
        if self.selected_memory_frame is not None and not isinstance(
            self.selected_memory_frame, SelectedMemoryFrame
        ):
            raise TypeError("selected_memory_frame has the wrong type")
        if self.working_activation.epoch_id != self.epoch_id:
            raise ValueError("working activation must belong to the reply epoch")
        history_ids = tuple(message.event_id for message in self.history_messages)
        _require_unique_ids(history_ids, "history event IDs", allow_empty=True)
        if self.current_user_event_id in history_ids:
            raise ValueError("current user event cannot also appear in history")
        if tuple(message.sequence_no for message in self.history_messages) != tuple(
            sorted(message.sequence_no for message in self.history_messages)
        ):
            raise ValueError("history_messages must be ordered by sequence_no")


class ContextAssembler:
    def __init__(
        self,
        *,
        ledger: EventLedger,
        context_size: int,
        reply_reserve_tokens: int,
        safety_margin_tokens: int,
        recall_candidate_limit: int,
        recall_evidence_limit: int,
        recall_excerpt_chars: int,
        recall_target_tokens: int,
    ) -> None:
        self._ledger = ledger
        self._context_size = context_size
        self._reply_reserve_tokens = reply_reserve_tokens
        self._safety_margin_tokens = safety_margin_tokens
        self._recall_candidate_limit = recall_candidate_limit
        self._recall_evidence_limit = recall_evidence_limit
        self._recall_excerpt_chars = recall_excerpt_chars
        self._recall_target_tokens = recall_target_tokens

    def assemble(
        self,
        snapshot: EpochSnapshot,
        *,
        current_user_event_id: str,
        current_user_text: str,
        current_time: datetime,
        current_timezone: str,
        resolve_recall: bool = True,
    ) -> ReplyContext:
        current = next(
            (
                event
                for event in snapshot.message_events
                if event.event_id == current_user_event_id
            ),
            None,
        )
        if current is None or current.actor != "user":
            raise ValueError("current user event is missing from the epoch snapshot")
        if current.text != current_user_text:
            raise ValueError("current user text differs from the committed event")
        if any(
            event.conversation_id != snapshot.conversation_id
            for event in snapshot.message_events
        ):
            raise ValueError("epoch snapshot mixes conversations")

        character_events = tuple(
            event for event in snapshot.message_events if event.actor == "character"
        )
        user_event_ids = {
            event.event_id
            for event in snapshot.message_events
            if event.actor == "user"
        }
        if any(
            event.causation_event_id not in user_event_ids
            for event in character_events
        ):
            raise ValueError(
                "completed character event lacks a user cause in the same epoch"
            )
        resolved_user_ids = {
            event.causation_event_id
            for event in character_events
            if event.causation_event_id is not None
        }
        unresolved_user_ids = tuple(
            event.event_id
            for event in snapshot.message_events
            if event.actor == "user" and event.event_id not in resolved_user_ids
        )
        last_character = character_events[-1] if character_events else None
        recall_cues = _parse_recall_cues(current_user_text, current_time)
        working_activation = WorkingActivation(
            epoch_id=snapshot.epoch_id,
            recent_verbatim_event_ids=tuple(
                event.event_id for event in snapshot.message_events
            ),
            last_completed_user_event_id=(
                last_character.causation_event_id if last_character is not None else None
            ),
            last_completed_character_event_id=(
                last_character.event_id if last_character is not None else None
            ),
            unresolved_user_event_ids=unresolved_user_ids,
            explicit_recall_cues=recall_cues,
            current_time=current_time,
            current_timezone=current_timezone,
        )
        history_messages = tuple(
            ContextMessage(
                event_id=event.event_id,
                role="user" if event.actor == "user" else "assistant",
                text=event.text,
                sequence_no=event.sequence_no,
                occurred_at=_parse_datetime(event.occurred_at),
            )
            for event in snapshot.history_events
        )
        recall_frame = (
            self._build_recall_frame(snapshot, recall_cues)
            if resolve_recall
            else RecallFrame((), (), "no_query", None)
        )
        return epoch_reply_context(
            epoch_id=snapshot.epoch_id,
            history_messages=history_messages,
            user_event_id=current_user_event_id,
            text=current_user_text,
            occurred_at=current_time,
            timezone=current_timezone,
            context_size=self._context_size,
            reply_reserve_tokens=self._reply_reserve_tokens,
            safety_margin_tokens=self._safety_margin_tokens,
            working_activation=working_activation,
            recall_frame=recall_frame,
        )

    async def measure_base_prompt(
        self, context: ReplyContext, measure_prompt: PromptMeasurer
    ) -> int:
        return await _measure_prompt(measure_prompt, _base_context(context))

    async def fit_to_budget(
        self, context: ReplyContext, measure_prompt: PromptMeasurer
    ) -> ReplyContext:
        maximum = context.budget.maximum_prompt_tokens
        base = _base_context(context)
        base_tokens = await _measure_prompt(measure_prompt, base)
        if base_tokens > maximum:
            raise ContextBudgetExceeded(base_tokens, maximum)

        activation = replace(
            context,
            recall_frame=RecallFrame((), (), "no_query", None),
            dynamic_context_enabled=True,
        )
        activation_tokens = await _measure_prompt(measure_prompt, activation)
        fitted = context
        final_tokens = await _measure_prompt(measure_prompt, fitted)
        while fitted.recall_frame.evidence and (
            final_tokens > maximum
            or final_tokens - activation_tokens > self._recall_target_tokens
        ):
            fitted = replace(
                fitted,
                recall_frame=_take_recall_evidence(
                    fitted.recall_frame, len(fitted.recall_frame.evidence) - 1
                ),
            )
            final_tokens = await _measure_prompt(measure_prompt, fitted)

        if final_tokens > maximum:
            raise ContextBudgetExceeded(final_tokens, maximum)

        fixed = replace(base, history_messages=(), current_user_text="")
        history = replace(base, current_user_text="")
        fixed_tokens = await _measure_prompt(measure_prompt, fixed)
        history_total = await _measure_prompt(measure_prompt, history)
        stages = (
            fixed_tokens,
            history_total,
            base_tokens,
            activation_tokens,
            final_tokens,
        )
        if stages != tuple(sorted(stages)):
            raise ValueError("prompt token stages must be monotonic")
        return replace(
            fitted,
            budget=ContextBudget(
                context_size=context.budget.context_size,
                fixed_prompt_tokens=fixed_tokens,
                history_tokens=history_total - fixed_tokens,
                activation_tokens=activation_tokens - base_tokens,
                recall_tokens=final_tokens - activation_tokens,
                current_input_tokens=base_tokens - history_total,
                reply_reserve_tokens=context.budget.reply_reserve_tokens,
                safety_margin_tokens=context.budget.safety_margin_tokens,
                total_prompt_tokens=final_tokens,
            ),
        )

    def _build_recall_frame(
        self, snapshot: EpochSnapshot, cues: tuple[RecallCue, ...]
    ) -> RecallFrame:
        if not cues:
            return RecallFrame((), (), "no_query", None)

        phrases = tuple(
            cue.normalized_value
            for cue in cues
            if cue.kind == "phrase" and len(cue.normalized_value) >= 3
        )
        time_ranges = tuple(
            (cue.range_start, cue.range_end)
            for cue in cues
            if cue.kind == "time"
            and cue.range_start is not None
            and cue.range_end is not None
        )
        query_kind: RecallQueryKind
        if phrases and time_ranges:
            query_kind = "explicit_event"
        elif phrases:
            query_kind = "explicit_phrase"
        elif time_ranges:
            query_kind = "explicit_time"
        else:
            return RecallFrame((), (), "ambiguous", "explicit_event")

        episodes = self._ledger.find_recall_episodes(
            conversation_id=snapshot.conversation_id,
            excluded_epoch_id=snapshot.epoch_id,
            phrases=phrases,
            time_ranges=time_ranges,
            candidate_limit=self._recall_candidate_limit,
        )
        if not episodes:
            return RecallFrame((), (), "no_match", query_kind)

        evidence: list[RecallEvidence] = []
        injected_ids: set[str] = set()
        for episode in episodes:
            if episode.anchor.event_id in injected_ids:
                continue
            item = _recall_evidence(
                episode,
                excluded_event_ids=injected_ids,
                excerpt_chars=self._recall_excerpt_chars,
            )
            evidence.append(item)
            injected_ids.update(item.event_ids)
            if len(evidence) == self._recall_evidence_limit:
                break

        if not evidence:
            return RecallFrame((), (), "no_match", query_kind)
        source_ids = tuple(
            dict.fromkeys(
                event_id for item in evidence for event_id in item.event_ids
            )
        )
        return RecallFrame(tuple(evidence), source_ids, "none", query_kind)


def _base_context(context: ReplyContext) -> ReplyContext:
    return replace(
        context,
        recall_frame=RecallFrame((), (), "no_query", None),
        dynamic_context_enabled=False,
    )


async def _measure_prompt(
    measure_prompt: PromptMeasurer, context: ReplyContext
) -> int:
    value = await measure_prompt(context)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TypeError("prompt measurement must be a non-negative integer")
    return value


def _take_recall_evidence(frame: RecallFrame, count: int) -> RecallFrame:
    if count < 0 or count > len(frame.evidence):
        raise ValueError("recall evidence count is out of range")
    evidence = frame.evidence[:count]
    source_ids = tuple(
        dict.fromkeys(event_id for item in evidence for event_id in item.event_ids)
    )
    return RecallFrame(evidence, source_ids, "none", frame.query_kind)


def initial_reply_context(
    *,
    user_event_id: str,
    text: str,
    occurred_at: datetime,
    timezone: str,
    context_size: int,
    reply_reserve_tokens: int,
    safety_margin_tokens: int,
) -> ReplyContext:
    return epoch_reply_context(
        epoch_id=user_event_id,
        history_messages=(),
        user_event_id=user_event_id,
        text=text,
        occurred_at=occurred_at,
        timezone=timezone,
        context_size=context_size,
        reply_reserve_tokens=reply_reserve_tokens,
        safety_margin_tokens=safety_margin_tokens,
    )


def epoch_reply_context(
    *,
    epoch_id: str,
    history_messages: tuple[ContextMessage, ...],
    user_event_id: str,
    text: str,
    occurred_at: datetime,
    timezone: str,
    context_size: int,
    reply_reserve_tokens: int,
    safety_margin_tokens: int,
    working_activation: WorkingActivation | None = None,
    recall_frame: RecallFrame | None = None,
    dynamic_context_enabled: bool = True,
) -> ReplyContext:
    activation = working_activation or WorkingActivation(
        epoch_id=epoch_id,
        recent_verbatim_event_ids=(),
        last_completed_user_event_id=None,
        last_completed_character_event_id=None,
        unresolved_user_event_ids=(user_event_id,),
        explicit_recall_cues=(),
        current_time=occurred_at,
        current_timezone=timezone,
    )
    return ReplyContext(
        epoch_id=epoch_id,
        history_messages=history_messages,
        working_activation=activation,
        recall_frame=recall_frame or RecallFrame((), (), "no_query", None),
        current_user_event_id=user_event_id,
        current_user_text=text,
        budget=ContextBudget(
            context_size=context_size,
            fixed_prompt_tokens=0,
            history_tokens=0,
            activation_tokens=0,
            recall_tokens=0,
            current_input_tokens=0,
            reply_reserve_tokens=reply_reserve_tokens,
            safety_margin_tokens=safety_margin_tokens,
            total_prompt_tokens=0,
        ),
        dynamic_context_enabled=dynamic_context_enabled,
    )


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")


def _require_optional_id(value: object, name: str) -> None:
    if value is not None:
        _require_text(value, name)


def _require_unique_ids(
    values: tuple[str, ...], name: str, *, allow_empty: bool = False
) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be a tuple")
    if not values and not allow_empty:
        raise ValueError(f"{name} cannot be empty")
    for value in values:
        _require_text(value, f"{name} item")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} cannot contain duplicates")


def _require_aware_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")


_INTENT_PATTERN = re.compile(
    r"你还记得|还记得|我(?:之前|上次|以前)(?:说过|提过)|"
    r"(?:之前|上次|以前)我(?:说过|提过)|那件事|以前的事"
)
_TIME_PATTERN = re.compile(r"上个月|前几天|几天前|昨天|前天|上周")
_PHRASE_PATTERNS = (
    re.compile(r"(?:你)?还记得(?P<phrase>[^？?。！!]{1,80}?)(?:吗|么)?[？?。！!]*$"),
    re.compile(
        r"我(?:之前|上次|以前)(?:说过|提过)(?:的)?(?P<phrase>[^？?。！!]{1,80})"
    ),
    re.compile(
        r"(?:之前|上次|以前)我(?:说过|提过)(?:的)?(?P<phrase>[^？?。！!]{1,80})"
    ),
)
_GENERIC_PHRASES = frozenset(
    {"那个", "那件事", "以前的事", "之前的事", "上次的事", "什么", "啥"}
)


def _parse_recall_cues(text: str, current_time: datetime) -> tuple[RecallCue, ...]:
    _require_aware_datetime(current_time, "current_time")
    located: list[tuple[int, int, RecallCue]] = []
    intent_matches = tuple(_INTENT_PATTERN.finditer(text))
    if not intent_matches:
        return ()
    for match in intent_matches:
        located.append(
            (
                match.start(),
                0,
                RecallCue("intent", match.group(0), "explicit_recall"),
            )
        )
    for match in _TIME_PATTERN.finditer(text):
        start, end = _time_range(match.group(0), current_time)
        located.append(
            (
                match.start(),
                1,
                RecallCue("time", match.group(0), match.group(0), start, end),
            )
        )
    for pattern in _PHRASE_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        phrase = _normalize_phrase(match.group("phrase"))
        if phrase is None:
            continue
        located.append(
            (
                match.start("phrase"),
                2,
                RecallCue("phrase", match.group("phrase"), phrase),
            )
        )

    cues: list[RecallCue] = []
    seen: set[tuple[object, ...]] = set()
    for _position, _priority, cue in sorted(located, key=lambda item: (item[0], item[1])):
        key = (
            cue.kind,
            cue.normalized_value,
            cue.range_start,
            cue.range_end,
        )
        if key in seen:
            continue
        seen.add(key)
        cues.append(cue)
        if len(cues) == 8:
            break
    return tuple(cues)


def _normalize_phrase(value: str) -> str | None:
    normalized = " ".join(value.split()).strip(" ，,。？?！!：:的")
    normalized = _TIME_PATTERN.sub("", normalized).strip(" ，,。？?！!：:的")
    normalized = re.sub(
        r"^(?:我)?(?:之前|上次|以前)?(?:说过|提过)(?:的)?",
        "",
        normalized,
    ).strip(" ，,。？?！!：:的")
    normalized = normalized.removesuffix("吗").removesuffix("么").strip()
    if not normalized or normalized in _GENERIC_PHRASES:
        return None
    return normalized[:80]


def _time_range(label: str, current_time: datetime) -> tuple[datetime, datetime]:
    today = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    if label == "昨天":
        return today - timedelta(days=1), today
    if label == "前天":
        return today - timedelta(days=2), today - timedelta(days=1)
    if label == "上周":
        this_week = today - timedelta(days=today.weekday())
        return this_week - timedelta(days=7), this_week
    if label == "上个月":
        this_month = today.replace(day=1)
        previous_month = (this_month - timedelta(days=1)).replace(day=1)
        return previous_month, this_month
    return today - timedelta(days=7), today


def _recall_evidence(
    episode: RecallEpisode,
    *,
    excluded_event_ids: set[str],
    excerpt_chars: int,
) -> RecallEvidence:
    available = tuple(
        event
        for event in episode.events
        if event.event_id not in excluded_event_ids
    )
    anchor = episode.anchor
    if not any(event.event_id == anchor.event_id for event in available):
        raise ValueError("recall evidence must retain its anchor")

    def line(event) -> str:
        speaker = "玩家" if event.actor == "user" else "秦未晞"
        return f"{speaker}：{event.text}"

    anchor_line = line(anchor)
    if len(anchor_line) > excerpt_chars:
        anchor_line = (
            anchor_line[:excerpt_chars]
            if excerpt_chars <= 3
            else anchor_line[: excerpt_chars - 3] + "..."
        )
    selected = [anchor]
    selected_lines = {anchor.event_id: anchor_line}
    used_chars = len(anchor_line)
    neighbors = sorted(
        (event for event in available if event.event_id != anchor.event_id),
        key=lambda event: (abs(event.sequence_no - anchor.sequence_no), event.sequence_no),
    )
    for event in neighbors:
        value = line(event)
        if used_chars + 1 + len(value) > excerpt_chars:
            continue
        selected.append(event)
        selected_lines[event.event_id] = value
        used_chars += 1 + len(value)
    selected.sort(key=lambda event: (event.sequence_no, event.event_id))

    reasons: list[str] = []
    if episode.matched_time:
        reasons.append("time_range")
    if episode.matched_phrase:
        reasons.append("exact_phrase")
    if anchor.actor == "user":
        reasons.append("user_anchor")
    if {event.actor for event in episode.events} >= {"user", "character"}:
        reasons.append("both_roles")
    reasons.append("stable_sequence")
    return RecallEvidence(
        anchor_event_id=anchor.event_id,
        event_ids=tuple(event.event_id for event in selected),
        occurred_at=_parse_datetime(anchor.occurred_at),
        actor=anchor.actor,
        verbatim_excerpt="\n".join(selected_lines[event.event_id] for event in selected),
        rank_reasons=tuple(reasons),
    )


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require_aware_datetime(parsed, "event occurred_at")
    return parsed
