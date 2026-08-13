"""大块 A（2026-08-07）lock 全覆盖验收：prompt/validator/model/exporter 全部进 lock。

v2 施工项："lock 覆盖 prompt、validator、真实模型、rubric、sampling、schema、exporter"。
- 编译 lock 含 6 个渲染模板 prompt_refs + release 声明的 validator_refs + RunSpec 注入的
  model_refs/exporter_refs；
- lock_v4.schema.json 对 prompt_refs 强制 minProperties≥1（防空）。
"""
from __future__ import annotations

import pytest

from data_gen_v4.core.plan import RunSpec
from data_gen_v4.core.schemas import SchemaValidationError, validate_lock
from tests.stage5.test_qin_pipeline import QWX_PACKAGE_SET, qin_compiler


def test_compile_lock_covers_all_references(qin_compiler):
    result = qin_compiler.compile(
        RunSpec(
            run_id="lock-coverage",
            seed=42,
            model_pin={
                "primary": {
                    "model_id": "qwen3.5-4b",
                    "revision": "openai-compat",
                    "sampling": {"temperature": 0.7},
                }
            },
            exporter_pin={"primary": {"exporter_id": "sharegpt-reply", "version": "1.0"}},
        ),
        QWX_PACKAGE_SET,
    )
    lock = result.lock
    # 1) prompt_refs：渲染模板全部锁定（含 reply_merge）
    assert lock["prompt_refs"], "prompt_refs 为空"
    assert "reply_merge.txt" in lock["prompt_refs"]
    assert all("content_hash" in v for v in lock["prompt_refs"].values())
    # 2) validator_refs：release 声明的 validator 全部落盘
    assert lock["validator_refs"], "validator_refs 为空"
    assert "validator:qinweixi-canon-consistency-v1" in lock["validator_refs"]
    assert all(v["status"] == "declared" for v in lock["validator_refs"].values())
    # 3) model_refs / exporter_refs：RunSpec 注入
    assert lock["model_refs"]["primary"]["model_id"] == "qwen3.5-4b"
    assert lock["model_refs"]["primary"]["sampling"]["temperature"] == 0.7
    assert lock["exporter_refs"]["primary"]["exporter_id"] == "sharegpt-reply"
    # 4) schema 校验通过
    validate_lock(lock)


def test_empty_prompt_refs_rejected_by_schema():
    # lock schema 防空：prompt_refs 必须 ≥1（模板总是存在）
    lock = {
        "lock_hash": "sha256:" + "a" * 64,
        "packages": {},
        "source_snapshots": {},
        "schema_refs": {},
        "prompt_refs": {},
        "validator_refs": {},
        "adapter_revisions": {"core": {"revision": "0.1.0"}},
        "created_at": "2026-08-07T00:00:00Z",
    }
    with pytest.raises(SchemaValidationError):
        validate_lock(lock)
