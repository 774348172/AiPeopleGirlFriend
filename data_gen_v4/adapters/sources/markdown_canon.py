"""MarkdownCanonSourceAdapter：带稳定段落 ID 的 Markdown 正典 → SourceSnapshot。

段落 ID 约定：每个条目以 `<!-- id: <stable-id> -->` 注释行开头，后跟
`### <标题>` 与正文；ID 用于字段指针与支持范围（§6.2 稳定 ID 规则）。
无 ID 的段落跳过（不构成证据单元），完全无 ID 时加载失败。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from data_gen_v4.core.errors import SourceSnapshotFailedError
from data_gen_v4.core.resolver import canonical_json

_ID_RE = re.compile(r"<!--\s*id:\s*([^\s]+)\s*-->")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")
_DEFAULT_VISIBILITY = "profile_public"
_DEFAULT_DISCLOSURE = "direct_allowed"


class MarkdownCanonSourceAdapter:
    def __init__(self, root: Path, *, source_kind: str = "canon_fact") -> None:
        self._root = Path(root)
        self._source_kind = source_kind
        self._cache: dict[str, dict[str, Any]] = {}

    def load_snapshot(self, source_ref: str, snapshot_policy: str = "freeze") -> dict[str, Any]:
        if source_ref in self._cache:
            return self._cache[source_ref]
        if not source_ref.startswith("file:"):
            raise SourceSnapshotFailedError(f"不支持的 source ref: {source_ref!r}")
        path = self._root / source_ref[len("file:") :]
        if not path.exists():
            raise SourceSnapshotFailedError(f"来源文件不存在: {path}")
        text = path.read_text(encoding="utf-8")

        units: list[dict[str, Any]] = []
        current_id: str | None = None
        current_heading = ""
        current_lines: list[str] = []
        profile_id = _find_profile(text)

        def flush() -> None:
            nonlocal current_id, current_lines
            if current_id and current_lines:
                units.append(
                    {
                        "source_kind": self._source_kind,
                        "source_id": current_id,
                        "profile_id": profile_id,
                        "snapshot_id": "",
                        "field_pointer": "",
                        "value": "\n".join(current_lines).strip(),
                        "occurred_at": None,
                        "valid_from": None,
                        "valid_to": None,
                        "visibility_scope": _DEFAULT_VISIBILITY,
                        "knowledge_scope": None,
                        "disclosure_policy": _DEFAULT_DISCLOSURE,
                        "supersedes_source_id": None,
                        "tags": [current_heading] if current_heading else [],
                        "metadata": {"heading": current_heading},
                    }
                )
            current_lines = []

        for line in text.splitlines():
            id_match = _ID_RE.search(line)
            if id_match:
                flush()
                current_id = id_match.group(1)
                continue
            heading = _HEADING_RE.match(line)
            if heading and current_id is not None:
                current_heading = heading.group(1).strip()
                continue
            if current_id is not None and line.strip():
                current_lines.append(line.rstrip())
        flush()

        if not units:
            raise SourceSnapshotFailedError(f"Markdown 中未找到任何带 id 的段落: {path}")

        snapshot_id = f"snap:{source_ref}"
        for unit in units:
            unit["snapshot_id"] = snapshot_id
        body = {
            "snapshot_id": snapshot_id,
            "source_adapter_id": "markdown-canon",
            "loaded_at": _utc_now(),
            "units": units,
        }
        # content_hash 只覆盖内容（排除 loaded_at 时间戳）
        body["content_hash"] = "sha256:" + hashlib.sha256(
            canonical_json(
                {k: v for k, v in body.items() if k not in ("content_hash", "loaded_at")}
            ).encode("utf-8")
        ).hexdigest()
        if snapshot_policy == "freeze":
            self._cache[source_ref] = body
        return body


def _find_profile(text: str) -> str:
    match = re.search(r"^profile_id:\s*(\S+)", text, re.MULTILINE)
    return match.group(1) if match else ""


def _utc_now() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
