# -*- coding: utf-8 -*-
"""原子发布工具（阶段 4 D-4，替代 gen_qin_1000 的无事务合并）。

流程：合并批次（校验行数/哈希对齐）→ 去重报告 → 整组切分 → contamination check
→ manifest 生成 → release 落盘（事务：manifest 成功才产生 release_created）。

用法:
  python tools/publish_dataset.py --metadata 训练数据/qin_v4_1000_final.metadata.jsonl \
      --data 训练数据/qin_v4_1000_final.jsonl \
      --ledger 训练数据/qin_v4_1000_final.sqlite \
      --out manifest 训练数据/qin_v4_1000_release.manifest.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_gen_v4.core.contamination_check import (  # noqa: E402
    ContaminationContract,
    check_contamination,
)
from data_gen_v4.core.dedup import DedupConfig, dedup_report  # noqa: E402
from data_gen_v4.core.release import ReleaseInput, build_manifest, publish_release  # noqa: E402
from data_gen_v4.core.splitter import SplitPolicy, split_dataset  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metadata", required=True, help="metadata.jsonl（含 split_anchor_ids）")
    ap.add_argument("--data", required=True, help="训练 jsonl（与 metadata 行对齐）")
    ap.add_argument("--ledger", required=True, help="ledger sqlite（release_created 事件落盘）")
    ap.add_argument("--out", required=True, help="manifest 输出路径")
    ap.add_argument("--dataset-id", default="qinweixi-v4-1000")
    ap.add_argument("--blocklist", help="eval_blocklist json（可选；缺省跳过 contamination）")
    # 2026-08-09：多角色/family/mode 参数化（默认保持历史 qinweixi/visible_reply/REPLY）
    ap.add_argument("--character-id", default="qinweixi")
    ap.add_argument("--profile-id", default="qinweixi")
    ap.add_argument("--dataset-family", default="visible_reply",
                    help="数据集 family（visible_reply | memory_reranker）")
    ap.add_argument("--mode", default="REPLY",
                    help="模式（REPLY | MEMORY_RERANK）；影响样本 text 提取与 lineage")
    args = ap.parse_args()

    metas = [
        json.loads(line)
        for line in Path(args.metadata).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    data_lines = [
        line
        for line in Path(args.data).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(metas) != len(data_lines):
        print(f"[error] metadata {len(metas)} 行 ≠ data {len(data_lines)} 行（拒绝发布）")
        return 1

    samples = []
    for meta, data in zip(metas, data_lines):
        row = json.loads(data)
        if args.mode == "MEMORY_RERANK":
            # reranker 记录：text = query_text + candidate_memory_text（去重/污染检查用）
            text = f"{row.get('query_text', '')} {row.get('candidate_memory_text', '')}"
        else:
            text = " ".join(
                str(m.get("value", ""))  # ShareGPT 字段是 value（2026-08-08 修复）
                for m in row.get("conversations", [])
                if m.get("from") == "gpt"
            )
        samples.append(
            {
                "sample_id": meta["sample_id"],
                "split_anchor_ids": meta.get("split_anchor_ids")
                or [f"family:{meta.get('family_id', '')}"],
                "family_id": meta.get("family_id", ""),
                "task_type": meta.get("task_type", ""),
                "evidence_state": meta.get("evidence_state", ""),
                "desired_policy": meta.get("desired_policy", ""),
                "text": text,
            }
        )

    # 1) 去重报告
    dedup = dedup_report(
        [(s["sample_id"], s["text"]) for s in samples],
        DedupConfig(minhash_threshold=0.82),
    )
    # 2) 整组切分（写回 _split）
    split_policy = SplitPolicy()
    result = split_dataset(samples, split_policy)
    for sample in samples:
        sample["_split"] = result.assignments[sample["sample_id"]]
    split_report = {
        "split_counts": result.split_counts,
        "components": len(result.components),
        "max_component_ratio": result.max_component_ratio,
    }
    # 3) contamination（可选）
    contamination = {"status": "skipped", "summary": {}, "findings": []}
    if args.blocklist:
        from data_gen_v4.core.contamination_check import load_blocklist

        contract = ContaminationContract(blocklist_hashes=load_blocklist(args.blocklist))
        contamination = check_contamination(
            [(s["sample_id"], s["text"]) for s in samples], contract
        )
        if contamination["status"] == "blocked":
            print(f"[blocked] sealed contamination 命中 {len(contamination['findings'])} 条——拒绝发布")
            return 1
    # 4) manifest + 事务发布（package_lock_hash 从 ledger run_started 读取）
    import sqlite3

    con = sqlite3.connect(str(args.ledger))
    lock_hash = ""
    try:
        # run_started 事件（event_name 在 payload JSON 内）
        for (payload,) in con.execute(
            "SELECT payload_json FROM v4_records WHERE record_type='run' ORDER BY id DESC"
        ):
            data = json.loads(payload)
            if data.get("event_name") == "run_started":
                lock_hash = data.get("package_lock_hash", "")
                break
    finally:
        con.close()
    if not lock_hash:
        print("[error] ledger 无 run_started 记录（无法取 lock hash）——拒绝发布")
        return 1
    files_by_split: dict[str, list[Path]] = {"train": [Path(args.data)]}
    input_ = ReleaseInput(
        dataset_id=args.dataset_id,
        character_id=args.character_id,
        dataset_family=args.dataset_family,
        split_policy={
            "train": 0.8, "dev": 0.1, "test": 0.1,
        },
        samples=samples,
        files_by_split=files_by_split,
        package_lock_hash=lock_hash,
        profile_id=args.profile_id,
        mode=args.mode,
        dedup_report=dedup,
        split_report=split_report,
        contamination_report=contamination,
        gate_summary={"accepted": len(samples)},
    )
    manifest = build_manifest(input_)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 事务发布：manifest 原子写盘成功 → release_created 事件落 ledger
    from data_gen_v4.core.records import LineageHeaderV4
    from data_gen_v4.core.sink import AppendSink

    header = LineageHeaderV4(
        record_type="run",
        run_id=f"publish-{args.dataset_id}",
        plan_id=args.dataset_id,
        package_lock_hash=manifest["package_lock_hash"],
        profile_id=args.profile_id,
        profile_snapshot_id="",
        protocol_bundle_id="relationship-runtime-v1",
        recipe_id="",
        mode=args.mode,
        task_type="",
        family_id="",
        generator_version="0.1.0",
    )
    with AppendSink.open(args.ledger) as sink:
        publish_release(manifest, out, sink, run_id="publish", header=header)
    print(f"发布完成: {out}（samples={len(samples)}，split={result.split_counts}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
