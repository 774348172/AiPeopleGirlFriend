"""Reversible V2 presentation of the existing GAME_REPLY_CONTEXT_V1 text.

The codec factors repeated provenance and disclosure annotations within a
single prompt. Its inverse reconstructs the exact V1 system message; neither
function selects facts or infers values. V1 remains the active runtime format.
"""
from __future__ import annotations

import json
import re


PROFILE_HEADING = "[稳定角色知识；知道不等于主动透露]\n"
FACT_HEADING = "[当前权威事实]\n"
UNKNOWN_HEADING = "[明确未知]\n"
NEXT_PROFILE = "[角色锚]\n"
NEXT_FACT = "[本轮玩家事实报告]\n"
NEXT_UNKNOWN = "[当前世界]\n"

FACT_NOTE = "固定设定、当前状态按来源分层；固定设定不代表本轮发生了事件。\n"
UNKNOWN_NOTE = "以下各字段的值均未知；共同来源与原因由分组标注。\n"

PROFILE_MARKER = "【共同披露规则】"
KNOWN_FIXED_MARKER = "【固定设定；共同来源】"
KNOWN_OTHER_MARKER = "【已知事实；共同来源】"
UNKNOWN_MARKER = "【未知字段；共同来源与原因】"
SINCE_MARKER = "【获知时间分界】"
RAW_MARKER = "【原样】\n"
MARKERS = (
    PROFILE_MARKER, KNOWN_FIXED_MARKER, KNOWN_OTHER_MARKER,
    UNKNOWN_MARKER, SINCE_MARKER, RAW_MARKER.rstrip("\n"),
)
FIXED_SOURCES = frozenset({"家庭正典", "家庭正典生活资料", "外出场景正典", "公开经营资料"})
PROFILE_POLICIES = (
    "可直接回答", "只自然暗示，不主动直说", "这是未知边界，不补造答案",
    "自身偏好；不证明具体经历，也不要求每轮复述",
)
UNKNOWN = re.compile(
    r"^- (?P<body>.*：未知)（来源：(?P<source>[^（）\r\n]+)；"
    r"原因：(?P<reason>[^（）\r\n]+)）\n$"
)


def _split(system: str, heading: str, next_heading: str) -> tuple[str, str, str]:
    if system.count(heading) != 1 or system.count(next_heading) != 1:
        raise ValueError(f"Missing or duplicated context boundary: {heading}")
    start = system.index(heading) + len(heading)
    end = system.index(next_heading, start)
    return system[:start], system[start:end], system[end:]


def _object(value: str) -> object:
    obj = json.loads(value)
    if json.dumps(obj, ensure_ascii=False, separators=(",", ":")) != value:
        raise ValueError("Noncanonical compact group header")
    return obj


def _grouped(lines: list[str], kind: str) -> str:
    output: list[str] = []
    active: tuple[str, ...] | None = None
    for line in lines:
        marker: str | None = None
        values: tuple[str, ...] | None = None
        body = ""
        if kind == "profile" and line.startswith("- ") and line.endswith("\n"):
            for policy in PROFILE_POLICIES:
                suffix = f"（{policy}）\n"
                if line.endswith(suffix):
                    marker, values = PROFILE_MARKER, (policy,)
                    body = line[: -len(suffix)] + "\n"
                    break
        elif kind == "facts":
            suffix = "（当前有效，来源：游戏反馈）\n"
            if line.startswith("- ") and line.endswith(suffix) and "（来源：" in line:
                head, rest = line[:-len(suffix)].rsplit("（来源：", 1)
                source, separator, since = rest.partition("）")
                if separator and source and "（" not in source and "）" not in source and (
                    not since or since.startswith("；获知时间：")
                ):
                    marker = KNOWN_FIXED_MARKER if source in FIXED_SOURCES else KNOWN_OTHER_MARKER
                    values = (source,)
                    body = f"{head}{SINCE_MARKER + since if since else ''}\n"
        elif kind == "unknown":
            match = UNKNOWN.fullmatch(line)
            if match:
                marker, values = UNKNOWN_MARKER, (match["source"], match["reason"])
                body = f"- {match['body']}\n"
        if marker is None or values is None:
            if active is not None:
                output.append(RAW_MARKER)
                active = None
            output.append(line)
            continue
        state = (marker, *values)
        if state != active:
            payload: object = values[0] if len(values) == 1 else {"来源": values[0], "原因": values[1]}
            output.append(marker + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            active = state
        output.append(body)
    return "".join(output)


def _restore(lines: list[str], kind: str) -> str:
    output: list[str] = []
    active: tuple[str, object] | None = None
    for line in lines:
        if line == RAW_MARKER:
            active = None
            continue
        markers = (PROFILE_MARKER,) if kind == "profile" else (
            (KNOWN_FIXED_MARKER, KNOWN_OTHER_MARKER) if kind == "facts" else (UNKNOWN_MARKER,)
        )
        found = next((value for value in markers if line.startswith(value)), None)
        if found:
            active = (found, _object(line[len(found):].rstrip("\n")))
            if kind == "unknown" and (
                not isinstance(active[1], dict)
                or set(active[1]) != {"来源", "原因"}
                or any(not isinstance(value, str) for value in active[1].values())
            ):
                raise ValueError("Invalid unknown provenance group")
            if kind != "unknown" and not isinstance(active[1], str):
                raise ValueError("Invalid group label")
            if kind == "profile" and active[1] not in PROFILE_POLICIES:
                raise ValueError("Unrecognized disclosure policy")
            if kind == "facts" and (active[0] == KNOWN_FIXED_MARKER) != (active[1] in FIXED_SOURCES):
                raise ValueError("Incorrect fixed/current source classification")
            continue
        if any(line.startswith(marker) for marker in MARKERS):
            raise ValueError("Unexpected compact marker")
        if active is None:
            output.append(line)
        elif kind == "profile":
            if not line.startswith("- "):
                raise ValueError("Invalid grouped profile line")
            output.append(line.rstrip("\n") + f"（{active[1]}）\n")
        elif kind == "facts":
            if not line.startswith("- "):
                raise ValueError("Invalid grouped fact line")
            body = line.rstrip("\n")
            since = ""
            if SINCE_MARKER in body:
                body, since = body.rsplit(SINCE_MARKER, 1)
                if not since.startswith("；获知时间："):
                    raise ValueError("Invalid known-since suffix")
            output.append(f"{body}（来源：{active[1]}）{since}（当前有效，来源：游戏反馈）\n")
        else:
            if not line.startswith("- ") or not line.rstrip("\n").endswith("：未知"):
                raise ValueError("Invalid grouped unknown field")
            payload = active[1]
            output.append(line.rstrip("\n") + f"（来源：{payload['来源']}；原因：{payload['原因']}）\n")
    return "".join(output)


def encode_system_v2(system: str) -> str:
    if any(marker in system for marker in MARKERS):
        raise ValueError("V1 system text contains a reserved compact marker")
    prefix, profile, tail = _split(system, PROFILE_HEADING, NEXT_PROFILE)
    prefix2, facts, tail = _split(tail, FACT_HEADING, NEXT_FACT)
    prefix3, unknown, suffix = _split(tail, UNKNOWN_HEADING, NEXT_UNKNOWN)
    return (
        prefix + _grouped(profile.splitlines(keepends=True), "profile")
        + prefix2 + FACT_NOTE + _grouped(facts.splitlines(keepends=True), "facts")
        + prefix3 + UNKNOWN_NOTE + _grouped(unknown.splitlines(keepends=True), "unknown")
        + suffix
    )


def decode_system_v2(system: str) -> str:
    prefix, profile, tail = _split(system, PROFILE_HEADING, NEXT_PROFILE)
    prefix2, facts, tail = _split(tail, FACT_HEADING, NEXT_FACT)
    prefix3, unknown, suffix = _split(tail, UNKNOWN_HEADING, NEXT_UNKNOWN)
    if not facts.startswith(FACT_NOTE) or not unknown.startswith(UNKNOWN_NOTE):
        raise ValueError("Missing compact context semantics")
    return (
        prefix + _restore(profile.splitlines(keepends=True), "profile")
        + prefix2 + _restore(facts[len(FACT_NOTE):].splitlines(keepends=True), "facts")
        + prefix3 + _restore(unknown[len(UNKNOWN_NOTE):].splitlines(keepends=True), "unknown")
        + suffix
    )
