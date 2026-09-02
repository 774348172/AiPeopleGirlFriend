"""Build a 24-row V5 targeted supplement without touching the frozen pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_gen_v4.runtime_grounded import evaluate_runtime_grounded_candidate  # noqa: E402
from data_gen_v4.runtime_grounded.renderer import render_runtime_grounded_training_record  # noqa: E402
from tools.build_v5_pilot_dataset import Concept, _scenario, generate_grounded  # noqa: E402

SYSTEM_ANCHOR = "你是白未晞。只输出角色说出口的自然回复。"

UNKNOWN_CONCEPTS = (
    Concept("targeted.airpurifier.mode", "air_purifier", "空气净化器", "state", "自动模式运行", "已关闭"),
    Concept("targeted.gallery.ticket", "gallery_ticket", "展览票", "visit_time", "星期二下午三点", "星期一上午十点"),
    Concept("targeted.blanket.location", "spare_blanket", "备用毯子", "location", "客房柜下层", "沙发储物箱"),
    Concept("targeted.coffee.order", "coffee_order", "咖啡订单", "pickup_status", "等待取餐", "正在制作"),
    Concept("targeted.plant.watering", "balcony_plant", "阳台绿植", "watering_status", "已经浇水", "还没浇水"),
    Concept("targeted.projector.state", "projector", "投影仪", "state", "待机", "播放中"),
    Concept("targeted.bicycle.location", "bicycle", "自行车", "location", "东门车棚", "楼道口"),
    Concept("targeted.delivery.window", "protagonist", "你的送货时段", "delivery_time", "下午两点到四点", "上午九点到十一点", "protagonist_plan"),
)

SUBJECT_CONCEPTS = (
    Concept("targeted.door.lock", "front_door", "入户门", "state", "已经反锁", "没有反锁"),
    Concept("targeted.induction.state", "induction_cooker", "电磁炉", "state", "已断电", "还通着电"),
    Concept("targeted.milk.location", "fresh_milk", "鲜奶", "location", "冰箱中层", "厨房台面"),
    Concept("targeted.class.time", "protagonist", "你的线上课", "class_time", "晚上八点", "晚上七点", "protagonist_plan"),
    Concept("targeted.document.status", "application_document", "申请材料", "review_status", "审核中", "已退回"),
    Concept("targeted.window.state", "bedroom_window", "卧室窗户", "state", "已经关好", "还开着"),
    Concept("targeted.lamp.state", "desk_lamp", "书桌台灯", "state", "已经关闭", "还亮着"),
    Concept("targeted.clinic.time", "protagonist", "你的复诊", "appointment_time", "星期四上午九点", "星期三下午四点", "protagonist_plan"),
)

GRANULARITY_CONCEPTS = (
    Concept("targeted.backpack.location", "travel_backpack", "旅行背包", "location", "储物柜顶层", "卧室椅背"),
    Concept("targeted.shuttle.time", "protagonist", "这次接驳车", "departure_time", "晚上七点十五", "晚上六点五十", "protagonist_plan"),
    Concept("targeted.prescription.time", "protagonist", "这次处方药", "medicine_time", "午饭后", "睡前", "protagonist_plan"),
    Concept("targeted.sorting.status", "book_order", "这次图书订单", "delivery_status", "仓库分拣中", "已经发出"),
    Concept("targeted.dishwasher.mode", "dishwasher", "洗碗机", "operating_mode", "这次是漂洗", "烘干"),
    Concept("targeted.reservation.time", "protagonist", "这次餐厅预约", "reservation_time", "星期日下午六点", "星期六晚上七点", "protagonist_plan"),
    Concept("targeted.drawer.location", "camera_battery", "相机电池", "location", "书桌右侧顶层抽屉", "玄关柜下层"),
    Concept("targeted.pickup.status", "laundry_order", "这次洗衣订单", "pickup_status", "等待本次取件", "已经取走"),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def build_scenarios() -> list[dict]:
    specs = (
        *((concept, "reply_insufficient_information") for concept in UNKNOWN_CONCEPTS),
        *((concept, "reply_subject_attribution") for concept in SUBJECT_CONCEPTS),
        *((concept, "reply_accept_authoritative_update") for concept in GRANULARITY_CONCEPTS),
    )
    rows = []
    for offset, (concept, task) in enumerate(specs, 1):
        scenario = _scenario(concept, task, 3000 + offset, sealed=False)
        scenario["scenario_id"] = f"targeted.v1.{task}.{concept.key}"
        scenario["split_anchors"] = [f"task:{task}", f"family:targeted.v1.{concept.key}", f"predicate:{concept.predicate}"]
        if task == "reply_accept_authoritative_update":
            label = concept.label
            scenario["model_view"]["current_protagonist_utterance"] = (
                f"我只说这一次：{label}当前是{concept.current}，不是{concept.stale}。"
            )
        rows.append(scenario)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", default="qwen2.5:14b-instruct-q4_K_M")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    scenarios = build_scenarios()
    write_jsonl(args.output_dir / "scenarios.jsonl", scenarios)
    candidates = generate_grounded(
        scenarios,
        args.output_dir / "candidates.jsonl",
        base_url=args.base_url,
        model_name=args.model,
        workers=args.workers,
        max_attempts=4,
    )
    records = []
    gate_counts = Counter()
    for candidate in candidates:
        report = evaluate_runtime_grounded_candidate(candidate["scenario"], candidate["teacher_target"], candidate["semantic_audit"])
        if not report.approved:
            raise RuntimeError(f"candidate failed final hard gate: {candidate['sample_id']}: {report.to_dict()}")
        gate_counts.update(gate for gate, passed in report.verdicts.items() if passed)
        record = render_runtime_grounded_training_record(candidate["scenario"], candidate["teacher_target"], system_anchor=SYSTEM_ANCHOR, sample_id=candidate["sample_id"], protocol_snapshot_id="runtime-grounded-targeted-v1")
        records.append({"sample_id": candidate["sample_id"], "task_type": candidate["scenario"]["task_type"], "conversations": [{"from": "system", "value": SYSTEM_ANCHOR}, *[{"from": "human" if message["role"] == "human" else "gpt", "value": message["content"]} for message in record.messages]]})
    write_jsonl(args.output_dir / "train_supplement.jsonl", records)
    manifest = {
        "schema_version": 1,
        "dataset_id": "baiweixi_v5_targeted_supplement_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": len(records),
        "task_counts": dict(Counter(row["task_type"] for row in records)),
        "response_contract": {"unknown_acknowledgement": "checked", "subject_proposition_binding": "checked", "unsupported_expansion_policy": "reject"},
        "rg0_rg10_all_passed": all(gate_counts[f"RG{i}"] == len(records) for i in range(11)),
        "files": {name: sha256(args.output_dir / name) for name in ("scenarios.jsonl", "candidates.jsonl", "train_supplement.jsonl")},
        "sealed_or_diagnostic_family_reuse": False,
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
