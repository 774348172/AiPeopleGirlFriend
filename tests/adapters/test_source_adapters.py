"""SourceAdapter：JSON/YAML 与 Markdown 的加载与规范化。"""
from __future__ import annotations

import pytest

from data_gen_v4.adapters.sources.markdown_canon import MarkdownCanonSourceAdapter
from data_gen_v4.adapters.sources.structured_file import StructuredFileSourceAdapter
from data_gen_v4.core.errors import SourceSnapshotFailedError


def test_structured_file_loads_identity_and_canon(source_dir):
    adapter = StructuredFileSourceAdapter(source_dir)
    identity = adapter.load_snapshot("file:identity.yaml")
    canon = adapter.load_snapshot("file:canon.json")
    assert identity["source_adapter_id"] == "structured-file"
    assert identity["content_hash"].startswith("sha256:")
    kinds = {u["source_kind"] for u in identity["units"]}
    assert kinds == {"identity_fact"}
    assert {u["source_kind"] for u in canon["units"]} == {"canon_fact"}
    # 稳定 source_id：来自文件的显式 id 字段
    names = {u["source_id"] for u in identity["units"]}
    assert names == {"identity:alpha-name", "identity:alpha-town"}
    assert identity["units"][0]["profile_id"] == "fixture.alpha"


def test_structured_file_loads_timeline_events(source_dir):
    adapter = StructuredFileSourceAdapter(source_dir)
    timeline = adapter.load_snapshot("file:timeline.yaml")
    units = timeline["units"]
    assert {u["source_kind"] for u in units} == {"timeline_event"}
    ids = {u["source_id"] for u in units}
    assert ids == {"ev-move", "ev-cat"}
    assert units[0]["occurred_at"] == "5岁"
    # 秘密事件的可见性来自源文件
    secret = next(u for u in units if u["source_id"] == "ev-cat")
    assert secret["visibility_scope"] == "profile_secret"
    assert secret["disclosure_policy"] == "direct_allowed"


def test_structured_file_requires_stable_event_id(source_dir):
    (source_dir / "bad.yaml").write_text(
        "profile_id: fixture.alpha\nevents:\n  - date: '5岁'\n    summary: 没有 id\n",
        encoding="utf-8",
    )
    adapter = StructuredFileSourceAdapter(source_dir)
    with pytest.raises(SourceSnapshotFailedError, match="稳定 id"):
        adapter.load_snapshot("file:bad.yaml")


def test_structured_file_missing_file_fails(source_dir):
    adapter = StructuredFileSourceAdapter(source_dir)
    with pytest.raises(SourceSnapshotFailedError):
        adapter.load_snapshot("file:nope.yaml")


def test_structured_file_snapshot_is_frozen_per_instance(source_dir):
    adapter = StructuredFileSourceAdapter(source_dir)
    first = adapter.load_snapshot("file:identity.yaml")
    # 文件修改后，同一实例（freeze 策略）仍返回缓存快照
    (source_dir / "identity.yaml").write_text(
        "profile_id: fixture.alpha\nidentity:\n  name: { id: alpha-name, value: 改了 }\n",
        encoding="utf-8",
    )
    second = adapter.load_snapshot("file:identity.yaml")
    assert second == first


def test_markdown_canon_loads_id_paragraphs(markdown_canon):
    adapter = MarkdownCanonSourceAdapter(markdown_canon)
    snapshot = adapter.load_snapshot("file:canon.md")
    units = snapshot["units"]
    assert len(units) == 2
    assert {u["source_id"] for u in units} == {"md-library", "md-closed"}
    assert units[0]["value"] == "贝塔是一名图书管理员。"
    assert units[0]["metadata"]["heading"] == "图书馆"
    assert units[0]["profile_id"] == "fixture.beta"


def test_markdown_canon_without_ids_fails(tmp_path):
    (tmp_path / "no_id.md").write_text("# 标题\n没有 id 注释的正文。\n", encoding="utf-8")
    adapter = MarkdownCanonSourceAdapter(tmp_path)
    with pytest.raises(SourceSnapshotFailedError):
        adapter.load_snapshot("file:no_id.md")


def test_markdown_snapshot_hash_changes_with_content(markdown_canon, tmp_path):
    adapter = MarkdownCanonSourceAdapter(markdown_canon)
    first = adapter.load_snapshot("file:canon.md")
    # 新实例（不共享缓存）重新加载后 hash 应一致（确定性）
    second = MarkdownCanonSourceAdapter(markdown_canon).load_snapshot("file:canon.md")
    assert first["content_hash"] == second["content_hash"]
