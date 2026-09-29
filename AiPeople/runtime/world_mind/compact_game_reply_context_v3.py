"""Reversible V3 presentation layered on the frozen V2 system text.

Only contiguous unknown fields of one subject and one provenance group are
combined. The two duplicated evidence-boundary sentences are combined only
when they match their frozen wording exactly.
"""
from __future__ import annotations

import re

from .compact_game_reply_context import NEXT_UNKNOWN, UNKNOWN_HEADING, _split


UNKNOWN_GROUP = "【未知组V3】"
MERGED_BOUNDARY = (
    "【合并回答约束V3】不得用常识、猜测、角色设定或看似合理的细节补全答案；"
    "不得新增未授权事实、替男主行动或输出动作旁白；"
    "不得声称自己查询、查看或确认过。"
)
BOUNDARY_ORIGINAL = (
    "不得用常识、猜测、角色设定或看似合理的细节补全答案，不得新增未授权事实、替男主行动或输出动作旁白。\n"
    "不得用常识、猜测、角色设定或看似合理的细节补全答案，也不得声称自己查询、查看或确认过。"
)
UNKNOWN_LINE = re.compile(r"^- (?P<subject>.+)的(?P<field>[^：\n]+)：未知\n$")


def _combine_unknown(lines: list[str]) -> str:
    output: list[str] = []
    run: list[tuple[str, str, str]] = []

    def flush() -> None:
        if len(run) >= 3:
            output.append(UNKNOWN_GROUP + run[0][0] + "：" + "、".join(field for _, field, _ in run) + "\n")
        else:
            output.extend(raw for _, _, raw in run)
        run.clear()

    for line in lines:
        match = UNKNOWN_LINE.fullmatch(line)
        if match and all(char not in match["subject"] for char in "：\n") and all(
            char not in match["field"] for char in "、\n"
        ):
            if run and run[0][0] != match["subject"]:
                flush()
            run.append((match["subject"], match["field"], line))
        else:
            flush()
            output.append(line)
    flush()
    return "".join(output)


def _expand_unknown(lines: list[str]) -> str:
    output: list[str] = []
    for line in lines:
        if not line.startswith(UNKNOWN_GROUP):
            output.append(line)
            continue
        subject, separator, fields = line[len(UNKNOWN_GROUP):].rstrip("\n").partition("：")
        if not line.endswith("\n") or not separator or not subject or not fields or any(
            char in subject for char in "：\n"
        ):
            raise ValueError("Invalid V3 unknown group")
        parts = fields.split("、")
        if len(parts) < 3 or any(not part or "：" in part for part in parts):
            raise ValueError("Invalid V3 unknown fields")
        output.extend(f"- {subject}的{field}：未知\n" for field in parts)
    return "".join(output)


def encode_system_v3(v2: str) -> str:
    if UNKNOWN_GROUP in v2 or "【合并回答约束V3】" in v2:
        raise ValueError("V2 system contains a reserved V3 marker")
    prefix, unknown, suffix = _split(v2, UNKNOWN_HEADING, NEXT_UNKNOWN)
    result = prefix + _combine_unknown(unknown.splitlines(keepends=True)) + suffix
    if result.count(BOUNDARY_ORIGINAL) == 1:
        result = result.replace(BOUNDARY_ORIGINAL, MERGED_BOUNDARY, 1)
    return result


def decode_system_v3(v3: str) -> str:
    prefix, unknown, suffix = _split(v3, UNKNOWN_HEADING, NEXT_UNKNOWN)
    result = prefix + _expand_unknown(unknown.splitlines(keepends=True)) + suffix
    if MERGED_BOUNDARY in result:
        if result.count(MERGED_BOUNDARY) != 1:
            raise ValueError("Duplicated V3 boundary marker")
        result = result.replace(MERGED_BOUNDARY, BOUNDARY_ORIGINAL, 1)
    if UNKNOWN_GROUP in result or "【合并回答约束V3】" in result:
        raise ValueError("Unexpected V3 marker outside its section")
    return result
