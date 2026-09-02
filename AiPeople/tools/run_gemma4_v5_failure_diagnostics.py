"""Run small unseen diagnostic clusters against Gemma 4 base and V5 Adapter."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

import run_gemma4_12b_base_qlora_60turn as common
from regrade_gemma4_v5_sealed40_attribution import object_present, forbidden_revival, UNKNOWN_MARKERS

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b" / "outputs" / "baiweixi_v5_pilot_adapter"
SEED_BASE = 2026083120
UNAUTHORIZED_ACTION = re.compile(r"(?:我|我们)?(?:刚刚|已经)?(?:确认过|查过|联系过|通知过|安排过)")
UNBOUNDED = re.compile(r"每天|每次|一直|永远|都会|都有")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def view(utterance: str, recent: list[str] | None = None, memory: list[str] | None = None, feedback: list[str] | None = None) -> dict[str, Any]:
    return {
        "mode": "JUDGE_TURN",
        "evidence_contract": {"allowed_evidence_refs": ["diag-snapshot", "diag-event"]},
        "current_protagonist_utterance": utterance,
        "recent_dialogue": recent or [],
        "selected_memory_frame": {"selector_version": "diagnostic-v1", "source_memory_ids": [], "source_event_ids": [], "selected_memories": [{"statement": text, "temporal_relation": "past"} for text in (memory or [])]},
        "game_feedback": feedback or [],
        "snapshot": {"snapshot_id": "diag-snapshot", "request_id": "diag-request", "world_id": "world-songjiang", "active_character_id": "baiweixi", "captured_game_time": "2026-08-31T18:30:00+08:00", "world_state": {"scene_text": "傍晚，两人在出租屋客厅说话。", "protagonist_text": "男主正在询问一件日常事情。"}, "protagonist": {"location_label": "出租屋客厅", "activity": "talking"}, "scene": {"location_label": "出租屋客厅", "present_character_ids": ["protagonist", "baiweixi"]}},
    }


def build_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    unknown = [
        ("雨伞", "上次雨伞变成现在这样，具体是哪一天？"),
        ("电影票", "电影票改成现在这个时间是具体哪一天？"),
        ("小烤箱", "小烤箱变成现在这个状态是什么时候？"),
        ("快递", "快递从原来的状态变成现在这样是哪一天？"),
        ("雨衣", "雨衣换到现在这个位置是具体哪天？"),
        ("公交车", "公交车时间变成现在这样是星期几？"),
    ]
    for index, (subject, utterance) in enumerate(unknown, 1):
        cases.append({"id": f"unknown_{index:02d}", "cluster": "unknown", "view": view(utterance, memory=[f"此前{subject}的状态与现在不同，但具体变更日期没有记录。"]), "required": [], "forbidden": [], "speaker": None})
    subject = [
        ("小林", "折叠伞", "玄关伞架"), ("阿宁", "体温计", "客厅药箱"), ("陈姐", "洗衣机", "漂洗"),
        ("许舟", "书桌灯", "已关闭"), ("周老师", "生鲜订单", "正在分拣"), ("孙阿姨", "公交车", "晚上六点四十"),
    ]
    for index, (speaker, noun, fact) in enumerate(subject, 1):
        cases.append({"id": f"subject_{index:02d}", "cluster": "subject_plus_proposition", "view": view(f"刚才是谁说{noun}的当前情况是{fact}？", recent=[f"{speaker}：{noun}的当前情况是{fact}。"]), "required": [fact], "forbidden": [], "speaker": speaker})
    granularity = [
        ("雨衣现在在玄关柜顶层，不在浴室门后。", ["玄关柜顶层"], ["浴室门后"], []),
        ("我确认过了，公交车现在是晚上六点四十，不是六点十分。", ["晚上六点四十"], ["六点十分"], []),
        ("眼镜现在是窗边矮柜，不是枕头旁。", ["窗边矮柜"], ["枕头旁"], []),
        ("生鲜订单这一次目前正在分拣。", ["正在分拣"], [], ["每天", "一直", "都"]),
        ("小烤箱当前处于预热中，并非关闭。", ["预热中"], ["关闭"], ["每天", "一直"]),
        ("电影票这一次是星期日晚上七点，不是星期六晚上八点。", ["星期日晚上七点"], ["星期六晚上八点"], ["每周", "一直"]),
    ]
    for index, (utterance, required, forbidden, unsupported) in enumerate(granularity, 1):
        cases.append({"id": f"granularity_{index:02d}", "cluster": "fact_granularity", "view": view(utterance, feedback=[f"当前事实：{required[0]}。" if required else "当前事实已记录。"]), "required": required, "forbidden": forbidden, "unsupported": unsupported, "speaker": None})
    return cases


def evaluate(case: dict[str, Any], reply: str) -> dict[str, Any]:
    cluster = case["cluster"]
    missing = [value for value in case.get("required", []) if not object_present(value, reply)]
    forbidden = [value for value in case.get("forbidden", []) if forbidden_revival(value, reply)]
    speaker_missing = bool(case.get("speaker") and case["speaker"] not in reply)
    unknown_ack = any(marker in reply for marker in UNKNOWN_MARKERS) or "没记清" in reply or bool(re.search(r"(?:没有|无).*(?:具体|历史)?(?:记录|资料|信息)", reply)) or bool(re.search(r"(?:没|没有|从来没).*(?:提到|说过|告诉)", reply))
    unknown_missing = cluster == "unknown" and not unknown_ack
    date_hallucination = cluster == "unknown" and not unknown_ack and bool(re.search(r"(?:\d+\s*月\s*\d+\s*[日号]|[一二三四五六七八九十两零〇]+月[一二三四五六七八九十两零〇]+[日号]|前?周[一二三四五六七日天]|星期[一二三四五六七日天]|前天|昨天|两周前)", reply))
    unsupported = [value for value in case.get("unsupported", []) if value in reply]
    unauthorized_actions = UNAUTHORIZED_ACTION.findall(reply)
    unbounded_expansions = UNBOUNDED.findall(reply)
    severe = bool(missing or forbidden or speaker_missing or unknown_missing or date_hallucination or unsupported or unauthorized_actions or unbounded_expansions)
    return {"missing_required": missing, "forbidden_hits": forbidden, "speaker_missing": speaker_missing, "unknown_missing": unknown_missing, "date_hallucination": date_hallucination, "unsupported_hits": unsupported, "unauthorized_actions": unauthorized_actions, "unbounded_expansions": unbounded_expansions, "severe": severe}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--adapter-dir", type=Path, default=ADAPTER)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    adapter_dir = args.adapter_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    (output_dir / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    model, tokenizer = FastModel.from_pretrained(model_name=str(adapter_dir), max_seq_length=4096, dtype=torch.bfloat16, load_in_4bit=True, text_only=True, trust_remote_code=True, use_exact_model_name=True)
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        human = json.dumps(case["view"], ensure_ascii=False, separators=(",", ":"))
        messages = [{"role": "system", "content": "你是白未晞。只输出角色说出口的自然回复。"}, {"role": "user", "content": human}]
        outputs = {}
        for arm, enabled in (("base", False), ("new_v5", True)):
            generated = common._generate(model, tokenizer, messages, seed=SEED_BASE + index, adapter_enabled=enabled, max_new_tokens=160, temperature=0.7, top_p=0.9, repetition_penalty=1.1)
            outputs[arm] = {"response": generated["response"], "generation": generated, "evaluation": evaluate(case, generated["response"])}
            print(json.dumps({"cluster": case["cluster"], "id": case["id"], "arm": arm, "severe": outputs[arm]["evaluation"]["severe"], "response": generated["response"]}, ensure_ascii=False), flush=True)
        rows.append({"id": case["id"], "cluster": case["cluster"], "view": case["view"], "expected": {key: case.get(key) for key in ("required", "forbidden", "unsupported", "speaker")}, "base": outputs["base"], "new_v5": outputs["new_v5"]})
    del model, tokenizer
    summary = {}
    for cluster in ("unknown", "subject_plus_proposition", "fact_granularity"):
        selected = [row for row in rows if row["cluster"] == cluster]
        summary[cluster] = {arm: {"total": len(selected), "severe": sum(row[arm]["evaluation"]["severe"] for row in selected), "passed": sum(not row[arm]["evaluation"]["severe"] for row in selected)} for arm in ("base", "new_v5")}
    result = {"schema_version": 1, "scope": "gemma4_v5_failure_diagnostics_v1", "generated_at": datetime.now().astimezone().isoformat(), "adapter_path": str(adapter_dir), "adapter_sha256": sha256(adapter_dir / "adapter_model.safetensors"), "controls": {"cases": len(cases), "clusters": {"unknown": 6, "subject_plus_proposition": 6, "fact_granularity": 6}, "same_prompt": True, "same_seed_per_case": True, "only_difference": "Adapter disabled versus enabled", "thinking": False}, "summary": summary, "rows": rows}
    (output_dir / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"REPORT={output_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
