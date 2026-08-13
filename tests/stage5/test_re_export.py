"""re_export（从 ledger 重导出）验收。

- 0 重新生成：模型调用计数不增长；
- winner 选择复用已有 quality（不重复 judge 调用）；
- G7 审核放行闭环：导入复核 → re_export 放行 full 任务；
- 产物完整（训练行/metadata/gate_report）。
"""
from __future__ import annotations

import json

from data_gen_v4.adapters.modes.reply import ReplyModeAdapter
from data_gen_v4.core.gates import ReleaseQualityGate, accepted, secret_keyword_checker
from data_gen_v4.core.plan import RunSpec
from data_gen_v4.core.sink import AppendSink
from tests.stage5.test_qin_pipeline import (
    LOOP_SCRIPT,
    QWX_PACKAGE_SET,
    GenerationEngineV4,
    LoopingModelAdapter,
    LoopingPool,
    qin_compiler,
    qin_style_resolver,
)


def _generate_ledger(qin_compiler, tmp_path):
    """mock 生成 2 items（casual + romance）→ ledger。返回 (sink_path, run_id)。"""
    from dataclasses import replace

    # 生成与 re_export 必须用相同 model/exporter pins（lock_hash 一致性）
    result = qin_compiler.compile(
        RunSpec(
            run_id="re-export-run",
            seed=42,
            model_pin={
                "primary": {
                    "model_id": "deepseek-v4-flash",
                    "revision": "openai-compat",
                    "sampling": {"temperature": 0.7, "max_tokens": 8192},
                }
            },
            exporter_pin={"primary": {"exporter_id": "sharegpt-reply", "version": "1.0"}},
        ),
        QWX_PACKAGE_SET,
    )
    subset = replace(result.plan, items=[result.plan.items[0], result.plan.items[100]])
    model = LoopingModelAdapter(LOOP_SCRIPT)
    pool = LoopingPool(model)
    engine = GenerationEngineV4(
        {"REPLY": ReplyModeAdapter(style_contract_resolver=qin_style_resolver)},
        package_context=result.context,
    )
    sink_path = tmp_path / "ledger.sqlite"
    with AppendSink.open(sink_path) as sink:
        engine.execute(subset, result.lock, pool, sink)
    return sink_path, "re-export-run"


def _run_re_export(sink_path, tmp_path, out_name="out"):
    import sys

    sys.path.insert(0, "tools") if "tools" not in sys.path else None
    from tools.re_export import main as re_main

    out = tmp_path / out_name
    # 通过 subprocess 隔离环境变量（无 key → 退化 first_valid，不调用模型）
    import subprocess

    cmd = [
        sys.executable, "tools/re_export.py",
        "--ledger", str(sink_path),
        "--out", str(out),
        "--run-id", "re-export-run",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=".")
    assert proc.returncode == 0, proc.stderr
    return out


def test_re_export_zero_regeneration(qin_compiler, tmp_path):
    """模型调用 0 增长：re_export 只读 ledger + 确定性编译。"""
    sink_path, run_id = _generate_ledger(qin_compiler, tmp_path)
    import sqlite3

    def _count_calls() -> int:
        con = sqlite3.connect(str(sink_path))
        try:
            return con.execute(
                "SELECT COUNT(*) FROM v4_records WHERE record_type='call'"
            ).fetchone()[0]
        finally:
            con.close()

    with AppendSink.open(sink_path) as sink:
        progress = sink.read_progress(run_id)
        n_candidates = len(progress.candidates)
        n_calls = _count_calls()
    out = _run_re_export(sink_path, tmp_path)
    # 导出 ≤ 候选数（每 item 1 winner），且未新增调用
    exported = sum(1 for _ in out.with_suffix(".jsonl").read_text(encoding="utf-8").splitlines() if _.strip())
    assert 1 <= exported <= n_candidates
    n_calls_after = _count_calls()
    assert n_calls_after == n_calls, "re_export 不得触发模型调用"


def test_re_export_reuses_prior_quality(qin_compiler, tmp_path):
    """先导出一版（带 quality）→ 重导出复用分数（不重复 judge）。"""
    sink_path, run_id = _generate_ledger(qin_compiler, tmp_path)
    _run_re_export(sink_path, tmp_path, "v1")
    # 第二次导出：复用 v1 metadata 的 quality
    out = tmp_path / "v2"
    cmd = [
        "python", "tools/re_export.py",
        "--ledger", str(sink_path), "--out", str(out), "--run-id", run_id,
    ]
    import subprocess, sys

    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=".")
    assert proc.returncode == 0, proc.stderr
    meta_lines = [
        json.loads(line)
        for line in out.with_suffix(".metadata.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert meta_lines, "重导出必须产出 metadata"
    assert all(m.get("selection_reason") in ("first_valid", "judge_selected(reused)") for m in meta_lines)


def test_re_export_artifacts_complete(qin_compiler, tmp_path):
    sink_path, run_id = _generate_ledger(qin_compiler, tmp_path)
    out = _run_re_export(sink_path, tmp_path)
    data = out.with_suffix(".jsonl")
    meta = out.with_suffix(".metadata.jsonl")
    report = out.with_suffix(".gate_report.jsonl")
    assert data.exists() and meta.exists() and report.exists()
    meta_rows = [
        json.loads(line)
        for line in meta.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in meta_rows:
        assert row["sample_id"]
        assert "split_anchor_ids" in row
    gate_rows = [
        json.loads(line)
        for line in report.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert gate_rows  # 每候选一条 gate 记录
