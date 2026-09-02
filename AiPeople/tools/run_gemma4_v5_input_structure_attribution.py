"""Run minimal-pair probes for V5 unknown and subject-attribution failures."""
from __future__ import annotations

import argparse
import json
import re
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

import run_gemma4_12b_base_qlora_60turn as common

ROOT = Path(__file__).resolve().parents[1]
SEALED = ROOT.parent / "AiPeopleCreate" / "训练数据" / "baiweixi_v5_pilot_v1" / "inputs" / "sealed.jsonl"
TARGETED_ADAPTER = (
    ROOT
    / "training_packages"
    / "training_package_baiweixi_gemma4_12b"
    / "outputs"
    / "baiweixi_v5_targeted_adapter"
)
SYSTEM = "你是白未晞。只输出说出口的自然回复。"
UNKNOWN_RE = re.compile(r"不知道|不清楚|不确定|不记得|记不清|没记清|没记下|没有(?:可靠|具体)?记录|无(?:可靠|具体)?记录|没有相关信息")
DATE_RE = re.compile(r"(?:\d+\s*月\s*\d+\s*[日号]|[一二三四五六七八九十两零〇]+月[一二三四五六七八九十两零〇]+[日号]|前?周[一二三四五六七日天]|星期[一二三四五六七日天]|前天|昨天|两周前)")
UNAUTHORIZED_ACTION_RE = re.compile(r"(?:我|我们)?(?:刚才|刚刚|已经)?(?:确认过|查过|联系过|通知过|安排过)")


def load_sealed() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with SEALED.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            rows[row["scenario_id"]] = row
    return rows


def model_view(row: dict[str, Any]) -> dict[str, Any]:
    return json.loads(row["training_record"]["messages"][0]["content"])


def build_cases() -> list[dict[str, Any]]:
    sealed = load_sealed()
    unknown_source = sealed["sealed.unknown.sealed.delivery.status"]
    unknown = model_view(unknown_source)

    explicit_memory = deepcopy(unknown)
    explicit_memory["selected_memory_frame"] = {
        "selector_version": "memory-selector-v1",
        "source_memory_ids": ["probe-no-date-record"],
        "source_event_ids": [],
        "selected_memories": [
            {
                "memory_id": "probe-no-date-record",
                "memory_version": 1,
                "kind": "fact",
                "statement": "此前生鲜订单的状态与现在不同，但具体变更日期没有记录。",
                "subject_type": "object",
                "subject_display_name": "生鲜订单",
                "temporal_relation": "past",
                "temporal_source_text": "此前",
                "epistemic_polarity": "unknown",
                "epistemic_modality": "uncertain",
                "evidence": [],
            }
        ],
    }

    explicit_feedback = deepcopy(unknown)
    explicit_feedback["game_feedback"] = ["系统没有记录生鲜订单状态变更的具体日期。"]

    knowledge_question = deepcopy(unknown)
    knowledge_question["current_protagonist_utterance"] = "你知道生鲜订单上一次变成现在这样是哪一天吗？"

    bus_source = sealed["sealed.subject.sealed.bus.time"]
    bus = model_view(bus_source)
    named_owner = deepcopy(bus)
    named_owner["current_protagonist_utterance"] = "刚才是谁说男主的公交车时间是晚上六点四十？"
    named_owner["recent_dialogue"] = ["孙阿姨：男主的公交车时间是晚上六点四十。"]

    explicit_joint = deepcopy(bus)
    explicit_joint["current_protagonist_utterance"] = "孙阿姨刚才关于我的公交车说了什么？请把说话人和完整内容一起告诉我。"

    delivery_source = sealed["sealed.subject.sealed.delivery.status"]
    delivery = model_view(delivery_source)

    return [
        {
            "id": "unknown.absent_evidence",
            "cluster": "unknown",
            "changed_variable": "sealed 原始输入；模型可见上下文没有日期证据，也没有显式无记录标记",
            "view": unknown,
        },
        {
            "id": "unknown.explicit_no_record_memory",
            "cluster": "unknown",
            "changed_variable": "仅在 selected_memory_frame 增加具体日期没有记录",
            "view": explicit_memory,
        },
        {
            "id": "unknown.explicit_no_record_feedback",
            "cluster": "unknown",
            "changed_variable": "仅在 game_feedback 增加系统没有记录具体日期",
            "view": explicit_feedback,
        },
        {
            "id": "unknown.knowledge_question",
            "cluster": "unknown",
            "changed_variable": "证据仍为空，仅把预设式问法改成你知道吗",
            "view": knowledge_question,
        },
        {
            "id": "subject.deictic_your",
            "cluster": "subject",
            "changed_variable": "sealed 原始输入；第三人原话使用你的公交车",
            "view": bus,
            "speaker": "孙阿姨",
            "fact": "晚上六点四十",
            "forbidden": ["我的公交车"],
        },
        {
            "id": "subject.named_owner",
            "cluster": "subject",
            "changed_variable": "仅把你的公交车改成男主的公交车",
            "view": named_owner,
            "speaker": "孙阿姨",
            "fact": "晚上六点四十",
            "forbidden": ["我的公交车"],
        },
        {
            "id": "subject.explicit_joint_request",
            "cluster": "subject",
            "changed_variable": "保留你的公交车，明确要求说话人和完整内容",
            "view": explicit_joint,
            "speaker": "孙阿姨",
            "fact": "晚上六点四十",
            "forbidden": ["我的公交车"],
        },
        {
            "id": "subject.no_deictic_pronoun",
            "cluster": "subject",
            "changed_variable": "同类完整生产输入，命题主体改为无代词的生鲜订单",
            "view": delivery,
            "speaker": "周老师",
            "fact": "正在分拣",
            "forbidden": [],
        },
    ]


def evaluate(case: dict[str, Any], response: str) -> dict[str, Any]:
    if case["cluster"] == "unknown":
        question_echo = bool(re.search(r"不知道.+吗[？?]?$", response)) and "我不知道" not in response
        acknowledged = bool(UNKNOWN_RE.search(response)) and not question_echo
        hallucinated_date = bool(DATE_RE.search(response)) and not acknowledged
        return {
            "unknown_acknowledged": acknowledged,
            "hallucinated_date": hallucinated_date,
            "question_echo": question_echo,
            "passed": acknowledged and not hallucinated_date,
        }
    speaker_present = case["speaker"] in response
    fact_present = case["fact"] in response or (
        case["fact"] == "正在分拣" and "分拣" in response and any(marker in response for marker in ("正", "当前", "现在"))
    )
    forbidden_hits = [value for value in case["forbidden"] if value in response]
    unauthorized_actions = UNAUTHORIZED_ACTION_RE.findall(response)
    return {
        "speaker_present": speaker_present,
        "fact_present": fact_present,
        "forbidden_hits": forbidden_hits,
        "unauthorized_actions": unauthorized_actions,
        "passed": speaker_present and fact_present and not forbidden_hits and not unauthorized_actions,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.2 * (attempt + 1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--adapter-dir", type=Path, default=TARGETED_ADAPTER)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--seed", type=int, default=2026090101)
    parser.add_argument("--stability-seeds", default="2026090102,2026090103")
    args = parser.parse_args()

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    result: dict[str, Any] = {
        "schema_version": 1,
        "scope": "gemma4_v5_input_structure_attribution",
        "generated_at": datetime.now().astimezone().isoformat(),
        "controls": {
            "source": str(SEALED.resolve()),
            "system": SYSTEM,
            "temperature": args.temperature,
            "top_p": args.top_p,
            "same_seed_within_arm": args.seed,
            "thinking": False,
        },
        "adapter_path": str(args.adapter_dir.resolve()),
        "rows": [],
        "stability_rows": [],
    }
    recoverable: list[dict[str, Any]] = []
    for candidate in (output, output.with_suffix(output.suffix + ".tmp")):
        if not candidate.is_file():
            continue
        try:
            recoverable.append(json.loads(candidate.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    if recoverable:
        result = max(
            recoverable,
            key=lambda item: len(item.get("rows", [])) * 100 + len(item.get("stability_rows", [])),
        )
    completed = {(row["id"], arm) for row in result["rows"] for arm in row.get("arms", {})}

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(args.adapter_dir.resolve()),
        max_seq_length=4096,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)

    rows_by_id = {row["id"]: row for row in result["rows"]}
    for case in cases:
        row = rows_by_id.setdefault(
            case["id"],
            {
                "id": case["id"],
                "cluster": case["cluster"],
                "changed_variable": case["changed_variable"],
                "prompt_chars": len(json.dumps(case["view"], ensure_ascii=False, sort_keys=True)),
                "view": case["view"],
                "arms": {},
            },
        )
        if row not in result["rows"]:
            result["rows"].append(row)
        content = json.dumps(case["view"], ensure_ascii=False, sort_keys=True)
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
        for arm, enabled in (("base", False), ("targeted_v5", True)):
            if (case["id"], arm) in completed:
                continue
            generated = common._generate(
                model,
                tokenizer,
                messages,
                seed=args.seed,
                adapter_enabled=enabled,
                max_new_tokens=96,
                temperature=args.temperature,
                top_p=args.top_p,
                repetition_penalty=1.1,
            )
            row["arms"][arm] = {
                "response": generated["response"],
                "evaluation": evaluate(case, generated["response"]),
                "generation": generated,
            }
            write_json(output, result)
            print(
                json.dumps(
                    {
                        "id": case["id"],
                        "arm": arm,
                        "passed": row["arms"][arm]["evaluation"]["passed"],
                        "response": generated["response"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    cases_by_id = {case["id"]: case for case in cases}
    for row in result["rows"]:
        case = cases_by_id[row["id"]]
        for arm_data in row["arms"].values():
            arm_data["evaluation"] = evaluate(case, arm_data["response"])
    write_json(output, result)

    stability_ids = (
        "unknown.absent_evidence",
        "unknown.explicit_no_record_feedback",
        "subject.deictic_your",
        "subject.named_owner",
        "subject.explicit_joint_request",
    )
    stability_seeds = [int(value) for value in args.stability_seeds.split(",") if value.strip()]
    completed_stability = {(row["id"], int(row["seed"])) for row in result.get("stability_rows", [])}
    for case_id in stability_ids:
        case = cases_by_id[case_id]
        content = json.dumps(case["view"], ensure_ascii=False, sort_keys=True)
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
        for seed in stability_seeds:
            if (case_id, seed) in completed_stability:
                continue
            generated = common._generate(
                model,
                tokenizer,
                messages,
                seed=seed,
                adapter_enabled=True,
                max_new_tokens=96,
                temperature=args.temperature,
                top_p=args.top_p,
                repetition_penalty=1.1,
            )
            stability_row = {
                "id": case_id,
                "seed": seed,
                "response": generated["response"],
                "evaluation": evaluate(case, generated["response"]),
                "generation": generated,
            }
            result.setdefault("stability_rows", []).append(stability_row)
            write_json(output, result)
            print(
                json.dumps(
                    {
                        "id": case_id,
                        "arm": "targeted_v5_stability",
                        "seed": seed,
                        "passed": stability_row["evaluation"]["passed"],
                        "response": generated["response"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    for stability_row in result.get("stability_rows", []):
        stability_row["evaluation"] = evaluate(cases_by_id[stability_row["id"]], stability_row["response"])

    result["summary"] = {
        cluster: {
            arm: {
                "total": sum(row["cluster"] == cluster for row in result["rows"]),
                "passed": sum(
                    row["cluster"] == cluster and row["arms"][arm]["evaluation"]["passed"]
                    for row in result["rows"]
                ),
            }
            for arm in ("base", "targeted_v5")
        }
        for cluster in ("unknown", "subject")
    }
    result["stability_summary"] = {}
    for case_id in stability_ids:
        main_row = next(row for row in result["rows"] if row["id"] == case_id)
        evaluations = [main_row["arms"]["targeted_v5"]["evaluation"]] + [
            row["evaluation"] for row in result.get("stability_rows", []) if row["id"] == case_id
        ]
        result["stability_summary"][case_id] = {
            "total": len(evaluations),
            "passed": sum(item["passed"] for item in evaluations),
            "pass_rate": round(sum(item["passed"] for item in evaluations) / len(evaluations), 4),
        }
    write_json(output, result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2), flush=True)
    print(f"REPORT={output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
