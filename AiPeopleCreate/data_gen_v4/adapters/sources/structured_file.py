"""StructuredFileSourceAdapter：JSON/YAML 来源 → 规范化 SourceSnapshot。

来源文件约定（阶段 2 冻结）：
- identity/canon：顶层为对象，叶子值可以是字符串或结构；单元 = 每个带稳定
  `id` 字段的事实条目。若为纯键值映射（如 {"hometown": "..."}），以
  "键" 为 source_id（命名空间前缀由调用方给出）。
- timeline/memory：顶层含 `events` 数组，每个事件必须带稳定 `id` 字段
  （日期/下标不能当 ID，§6.2）；缺失即 source_snapshot_failed。

字段指针：field_pointer 为 JSON Pointer；value 为规范化的可校验文本。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from data_gen_v4.core.errors import SourceSnapshotFailedError
from data_gen_v4.core.resolver import canonical_json

_DEFAULT_VISIBILITY = "profile_public"
_DEFAULT_DISCLOSURE = "direct_allowed"


class StructuredFileSourceAdapter:
    """按 source ref 解析 JSON/YAML 文件为 SourceSnapshot。

    source ref 形如 `file:<相对路径>`；相对根目录由构造参数给定。
    """

    def __init__(self, root: Path, *, source_kind: str = "custom") -> None:
        self._root = Path(root)
        self._source_kind = source_kind
        self._cache: dict[str, dict[str, Any]] = {}

    def load_snapshot(self, source_ref: str, snapshot_policy: str = "freeze") -> dict[str, Any]:
        if source_ref in self._cache:
            return self._cache[source_ref]
        if not source_ref.startswith("file:"):
            raise SourceSnapshotFailedError(f"不支持的 source ref: {source_ref!r}")
        rel = source_ref[len("file:") :]
        path = self._root / rel
        if not path.exists():
            raise SourceSnapshotFailedError(f"来源文件不存在: {path}")
        try:
            if path.suffix.lower() in (".yaml", ".yml"):
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            elif path.suffix.lower() == ".json":
                doc = json.loads(path.read_text(encoding="utf-8"))
            else:
                raise SourceSnapshotFailedError(f"不支持的文件类型: {path.suffix}")
        except (yaml.YAMLError, json.JSONDecodeError) as error:
            raise SourceSnapshotFailedError(f"来源文件解析失败: {path}: {error}") from error
        if not isinstance(doc, dict):
            raise SourceSnapshotFailedError(f"来源文件顶层必须是对象: {path}")

        units = self._normalize(doc, path)
        snapshot_id = f"snap:{source_ref}"
        for unit in units:
            unit["snapshot_id"] = snapshot_id
        body = {
            "snapshot_id": snapshot_id,
            "source_adapter_id": "structured-file",
            "loaded_at": _utc_now(),
            "units": units,
        }
        # content_hash 只覆盖内容（排除 loaded_at 时间戳）
        body["content_hash"] = _hash_of(
            {k: v for k, v in body.items() if k not in ("content_hash", "loaded_at")}
        )
        if snapshot_policy == "freeze":
            self._cache[source_ref] = body
        return body

    # ───────────────────────── 规范化 ─────────────────────────

    def _normalize(self, doc: dict[str, Any], path: Path) -> list[dict[str, Any]]:
        units: list[dict[str, Any]] = []
        profile_id = str(doc.get("profile_id") or doc.get("profile") or "")
        events = doc.get("events")
        if isinstance(events, list):
            units.extend(self._normalize_events(events, profile_id, path))
        for key in ("identity", "canon", "facts", "memory"):
            section = doc.get(key)
            if isinstance(section, dict):
                units.extend(self._normalize_section(key, section, profile_id, path))
        # 三观字段（values/fears/goals）：通用数据生成器的能力修复（2026-08-06）。
        # 此前 bible 的价值观/恐惧/目标从未渲染进生成 prompt——所有角色都丢三观。
        # big_five 是数值维度，不在此收集（留给 profile 提示片段，避免数值噪音）。
        for key in ("values", "fears", "goals"):
            section = doc.get(key)
            if section is None:
                continue
            units.extend(self._normalize_beliefs(key, section, profile_id, path))
        if not units:
            raise SourceSnapshotFailedError(f"来源文件未产出任何证据单元: {path}")
        return units

    def _normalize_beliefs(
        self, key: str, section: Any, profile_id: str, path: Path
    ) -> list[dict[str, Any]]:
        """values/fears/goals → belief_fact 单元。

        克制原则：只收原始文本（渲染层负责蒸馏成信念短句）；
        secret 键（如 goals.secret）自动标 profile_secret，不进公开锚。
        """
        units: list[dict[str, Any]] = []
        if isinstance(section, dict):
            for sub_key, value in section.items():
                if isinstance(value, list):
                    for text in value:
                        units.append(
                            _unit(
                                source_kind="belief_fact",
                                source_id=f"{key}:{sub_key}:{text}",
                                profile_id=profile_id,
                                value=str(text),
                                visibility=(
                                    "profile_secret" if "secret" in sub_key else _DEFAULT_VISIBILITY
                                ),
                                disclosure=(
                                    "hint_only" if "secret" in sub_key else _DEFAULT_DISCLOSURE
                                ),
                            )
                        )
        elif isinstance(section, list):
            for text in section:
                units.append(
                    _unit(
                        source_kind="belief_fact",
                        source_id=f"{key}:{text}",
                        profile_id=profile_id,
                        value=str(text),
                        visibility=_DEFAULT_VISIBILITY,
                        disclosure=_DEFAULT_DISCLOSURE,
                    )
                )
        return units

    def _normalize_events(
        self, events: list[Any], profile_id: str, path: Path
    ) -> list[dict[str, Any]]:
        units: list[dict[str, Any]] = []
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                raise SourceSnapshotFailedError(f"{path} events[{index}] 不是对象")
            source_id = event.get("id") or event.get("event_id")
            if not isinstance(source_id, str) or not source_id:
                raise SourceSnapshotFailedError(
                    f"{path} events[{index}] 缺少稳定 id（下标不能当 ID）"
                )
            text = _event_text(event)
            units.append(
                _unit(
                    source_kind="timeline_event",
                    source_id=source_id,
                    profile_id=profile_id,
                    value=text,
                    visibility=event.get("visibility", _DEFAULT_VISIBILITY),
                    disclosure=event.get("disclosure_policy", _DEFAULT_DISCLOSURE),
                    tags=event.get("tags", []),
                    occurred_at=event.get("date") or event.get("occurred_at"),
                )
            )
        return units

    def _normalize_section(
        self, kind: str, section: dict[str, Any], profile_id: str, path: Path
    ) -> list[dict[str, Any]]:
        source_kind = "identity_fact" if kind == "identity" else "canon_fact"
        units: list[dict[str, Any]] = []
        for key, value in section.items():
            if isinstance(value, dict):
                source_id = value.get("id") or key
                text = _fact_text(value)
                visibility = value.get("visibility", _DEFAULT_VISIBILITY)
                disclosure = value.get("disclosure_policy", _DEFAULT_DISCLOSURE)
            else:
                source_id = key
                text = str(value)
                visibility = _DEFAULT_VISIBILITY
                disclosure = _DEFAULT_DISCLOSURE
            units.append(
                _unit(
                    source_kind=source_kind,
                    source_id=f"{kind}:{source_id}",
                    profile_id=profile_id,
                    value=text,
                    visibility=visibility,
                    disclosure=disclosure,
                )
            )
        return units


def _unit(
    *,
    source_kind: str,
    source_id: str,
    profile_id: str,
    value: str,
    visibility: str,
    disclosure: str,
    tags: list[str] | None = None,
    occurred_at: Any = None,
) -> dict[str, Any]:
    return {
        "source_kind": source_kind,
        "source_id": source_id,
        "profile_id": profile_id,
        "snapshot_id": "",
        "field_pointer": "",
        "value": value,
        "occurred_at": occurred_at,
        "valid_from": None,
        "valid_to": None,
        "visibility_scope": visibility,
        "knowledge_scope": None,
        "disclosure_policy": disclosure,
        "supersedes_source_id": None,
        "tags": tags or [],
        "metadata": {},
    }


def _event_text(event: dict[str, Any]) -> str:
    summary = event.get("summary")
    if isinstance(summary, str):
        return summary
    raise SourceSnapshotFailedError(f"事件 {event.get('id')} 缺少 summary 文本")


def _fact_text(value: dict[str, Any]) -> str:
    text = value.get("value") or value.get("text")
    if isinstance(text, str) and text:
        return text
    return str(value)


def _hash_of(body: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
