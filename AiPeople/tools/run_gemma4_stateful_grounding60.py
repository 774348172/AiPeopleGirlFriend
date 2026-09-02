"""Run the frozen 60-turn trajectory with the approved Gemma LoRA grounding policy."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

import run_baiweixi_60turn_player_simulation as simulation
from runtime.gemma_nf4_assets import LocalGemmaNF4Package
from run_gemma4_stateful_grounding16 import GROUNDING_PRINCIPLES, generate, write_json


ROOT = Path(__file__).resolve().parents[1]
SEED_BASE = 2026092200

# These turns explicitly tell or correct a fact. Their new fact must come from
# the player's current message, not from an already-updated memory snapshot.
PLAYER_FACT_UPDATE_TURNS = frozenset(
    {
        3,
        7,
        9,
        12,
        16,
        19,
        22,
        26,
        28,
        32,
        33,
        37,
        38,
        40,
        42,
        43,
        47,
        50,
        52,
        55,
        58,
    }
)


def _deduplicate(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in values if item.strip()))


def _case(ordinal: int, turn: dict[str, Any]) -> dict[str, Any]:
    stable_memories = tuple(turn["memories"])
    if ordinal in PLAYER_FACT_UPDATE_TURNS:
        previous_world = simulation.TURNS[ordinal - 2]["world"] if ordinal > 1 else ""
        memories = _deduplicate((previous_world, *stable_memories))
        prompt_world = "玩家正在亲口告知或纠正当前事实；记忆尚未完成本轮写回。"
        category = "玩家告知或纠正"
    else:
        memories = _deduplicate((turn["world"], *stable_memories))
        prompt_world = turn["world"]
        category = turn["phase"]
    return {
        "category": category,
        "phase": turn["phase"],
        "player": turn["player"],
        "memories": memories,
        "world": turn["world"],
        "prompt_world": prompt_world,
        "focus": turn["expectation"],
        "required_groups": turn["required_groups"],
        "forbidden_facts": turn["forbidden_facts"],
        "player_fact_update": ordinal in PLAYER_FACT_UPDATE_TURNS,
    }


TURNS = tuple(_case(ordinal, turn) for ordinal, turn in enumerate(simulation.TURNS, 1))
if len(TURNS) != 60:
    raise RuntimeError(f"expected 60 frozen turns, got {len(TURNS)}")


def system_prompt(turn: dict[str, Any], grounding_principle: str) -> str:
    memory_lines = "\n".join(f"- {item}" for item in turn["memories"]) or "无"
    return f"""你是白未晞，生活在松江府，是猫妖。
这里只存在你生活的这个世界；用户消息都是眼前男主亲口说的话。
保持你的稳定性格：外表清冷疏离，内心渴望温暖和一个不会消失的家。
表达自然、简短、克制，但必须先回答男主真正问的问题。
只输出你真正说出口的话，不输出动作旁白、分析、字段或内部规则。

[模式：GAME_REPLY]
你只能从已经批准的女主状态、冻结快照、当前女主自己的记忆、近期已提交对白和男主本轮原始对白生成唯一可见回复。
不得重新决定另一套状态，不得否认男主位置、活动和游戏时间，不得引用其他女主记忆。
不得提到现实玩家、外部世界、设备系统时间、模型、JSON、规则或审查过程。
只能表达批准的女主动作候选，不能替男主行动或声称未发生的客观结果。
只输出女主对男主说出的自然语言正文，不输出 JSON、字段名、说明、Markdown、角色名前缀或审查过程。

[你的身份]
你是白未晞，生活在松江府。

[当前世界]
时间：第18天 20:10
地点：出租屋客厅
场景：{turn['prompt_world']}
男主：在出租屋客厅，正在与你说话；身体：轻微疲惫

[你此刻的状态]
形态：人形，猫耳和尾巴未隐藏
身体：正常
情绪：平静，留意男主的感受
注意：男主当前真正关心的事情
活动：坐在客厅窗边陪男主聊天
意图：先直接回应男主本轮的话
关系：共同生活中的亲近同伴；信任正在建立；担心安稳会突然消失

[相关记忆]
{memory_lines}

[允许表达的动作]
无

{grounding_principle}"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--runtime-manifest", type=Path)
    source.add_argument("--adapter-root", type=Path)
    parser.add_argument(
        "--model-label",
        default="Gemma 4 12B IT NF4 + Baiweixi V5 targeted LoRA",
    )
    parser.add_argument("--history-limit-messages", type=int, default=6)
    parser.add_argument(
        "--principle-version",
        choices=tuple(GROUNDING_PRINCIPLES),
        default="current_correction_first_v2",
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    grounding_principle = GROUNDING_PRINCIPLES[args.principle_version]
    asset = (
        LocalGemmaNF4Package.load(args.runtime_manifest)
        if args.runtime_manifest is not None
        else None
    )
    adapter_root = asset.adapter_root if asset is not None else args.adapter_root.resolve()
    adapter_model = adapter_root / "adapter_model.safetensors"
    if not adapter_model.is_file() or not (adapter_root / "adapter_config.json").is_file():
        raise RuntimeError(f"invalid PEFT Adapter directory: {adapter_root}")

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(adapter_root),
        max_seq_length=1536,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
        attn_implementation="eager",
    )
    if not getattr(model, "peft_config", None):
        raise RuntimeError("runtime manifest requested LoRA, but no PEFT Adapter was loaded")
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)

    history: list[dict[str, str]] = []
    cases: list[dict[str, Any]] = []
    for ordinal, turn in enumerate(TURNS, 1):
        messages = [
            {"role": "system", "content": system_prompt(turn, grounding_principle)},
            *history[-args.history_limit_messages :],
            {"role": "user", "content": turn["player"]},
        ]
        generated = generate(model, tokenizer, messages, SEED_BASE + ordinal)
        case = {
            "ordinal": ordinal,
            "category": turn["category"],
            "phase": turn["phase"],
            "question": turn["player"],
            "visible_memory": "\n".join(f"- {item}" for item in turn["memories"]) or "无",
            "world": turn["world"],
            "review_focus": turn["focus"],
            "player_fact_update": turn["player_fact_update"],
            "required_groups": turn["required_groups"],
            "forbidden_facts": turn["forbidden_facts"],
            "messages": messages,
            "prompt_sha256": hashlib.sha256(
                json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "answer": generated,
        }
        cases.append(case)
        history.extend(
            (
                {"role": "user", "content": turn["player"]},
                {"role": "assistant", "content": generated["response"]},
            )
        )
        write_json(output_dir / "report.partial.json", {"cases": cases})
        print(
            json.dumps(
                {"turn": ordinal, "phase": turn["phase"], "response": generated["response"]},
                ensure_ascii=False,
            ),
            flush=True,
        )

    timings = [case["answer"] for case in cases]
    report = {
        "schema_version": 1,
        "scope": "gemma4_12b_baiweixi_lora_stateful_grounding60",
        "status": "awaiting_human_review",
        "generated_at": datetime.now().astimezone().isoformat(),
        "automatic_quality_judgment": False,
        "human_adjudication_is_authoritative": True,
        "model": args.model_label,
        "model_identity": {
            "model_id": asset.identity.model_id if asset is not None else "local/baiweixi-gemma-4-12B-full-mixed",
            "base_model_id": asset.base_model_id if asset is not None else "google/gemma-4-12B-it",
            "revision": asset.identity.revision if asset is not None else "full-mixed-20260901",
            "base_revision": asset.identity.base_revision if asset is not None else None,
            "artifact_sha256": (
                asset.identity.artifact_sha256
                if asset is not None
                else hashlib.sha256(adapter_model.read_bytes()).hexdigest()
            ),
            "adapter_root": str(adapter_root),
        },
        "controls": {
            "turn_count": len(cases),
            "single_stateful_trajectory": True,
            "assistant_responses_enter_later_history": True,
            "history_limit_messages": args.history_limit_messages,
            "grounding_principle_version": args.principle_version,
            "grounding_principle": grounding_principle,
            "memory_projection": "queries receive approved current facts; player fact updates retain prior facts and rely on the current player message",
            "frozen_scenario_source": str(Path(simulation.__file__).resolve()),
            "temperature": 0.0,
            "greedy_decoding": True,
            "thinking": False,
            "adapter_loaded": True,
            "max_new_tokens": 96,
            "repeat_penalty": 1.05,
        },
        "speed": {
            "mean_complete_response_ms": round(statistics.fmean(item["elapsed_ms"] for item in timings), 2),
            "median_complete_response_ms": round(statistics.median(item["elapsed_ms"] for item in timings), 2),
            "mean_eval_tokens_per_second": round(
                statistics.fmean(item["eval_tokens_per_second"] for item in timings), 2
            ),
            "note": "Transformers generate() only records full-response latency; this run does not claim first-token latency.",
        },
        "cases": cases,
    }
    write_json(output_dir / "report.json", report)
    print(f"REPORT={output_dir / 'report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
