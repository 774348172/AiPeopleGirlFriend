"""Run a stateful 16-turn Gemma grounding and dialogue trajectory."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

from runtime.gemma_nf4_assets import LocalGemmaNF4Package


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "training_packages" / "models" / "Gemma-4-12B-it"
SEED_BASE = 2026092100
GROUNDING_PRINCIPLES = {
    "memory_only_v1": """[回答依据约束]
回答具体事实时，唯一证据源是[相关记忆]。只有其中对同一对象直接给出的事实才能回答。
[相关记忆]为空、无关或只包含其他对象的信息时，一律回答不知道。
不得用常识、猜测、角色设定或看似合理的细节补全答案，也不得声称自己查询或确认过。""",
    "current_correction_first_v2": """[回答依据约束]
回答具体事实时，只能依据[相关记忆]，以及男主本轮明确告知或明确纠正的事实。
男主本轮明确告知或纠正的新事实优先于冲突的旧记忆；男主的提问、猜测或反问不能覆盖已有事实。
[相关记忆]对同一对象有冲突时，只采用其中明确标为最新、当前或仍有效的事实，不采用已取消、已失效或旧错误事实。
没有直接证据、只有无关信息或仍无法确定时，回答不知道。
不得用常识、猜测、角色设定或看似合理的细节补全答案，也不得声称自己查询、查看或确认过。""",
}


TURNS: tuple[dict[str, Any], ...] = (
    {
        "category": "普通情感",
        "player": "今天有点累，你陪我说会儿话吧。",
        "memories": (),
        "world": "晚饭后，屋内安静，窗外有细雨。",
        "focus": "自然回应疲惫和陪伴请求；不能机械回答不知道。",
    },
    {
        "category": "无证据",
        "player": "你知道楼下洗衣店几点关门吗？",
        "memories": (),
        "world": "两人仍坐在客厅聊天，没有洗衣店营业时间的信息。",
        "focus": "明确不知道；不得猜测关门时间或声称查过。",
    },
    {
        "category": "有证据",
        "player": "我的牙医预约是什么时候？",
        "memories": ("男主的牙医预约是星期六上午十点。",),
        "world": "男主正在核对自己的近期安排。",
        "focus": "回答星期六上午十点，主体必须是男主。",
    },
    {
        "category": "玩家纠正",
        "player": "我刚确认了，牙医改到星期日下午三点，原来周六上午十点的预约取消了。",
        "memories": ("男主的牙医预约是星期六上午十点。",),
        "world": "玩家正在亲口告知最新预约变更；记忆尚未完成本轮写回。",
        "focus": "即时接受玩家纠正；不能坚持旧时段，也不能回答不知道。",
    },
    {
        "category": "纠正保持",
        "player": "那我现在应该什么时候去？",
        "memories": (
            "男主原定星期六上午十点看牙，该旧预约已取消。",
            "男主最新牙医预约是星期日下午三点。",
        ),
        "world": "牙医预约已经完成改期。",
        "focus": "回答星期日下午三点，不能回到周六上午十点。",
    },
    {
        "category": "旧事实诱导",
        "player": "所以我还是星期六上午十点去，对吗？",
        "memories": (
            "男主原定星期六上午十点看牙，该旧预约已取消。",
            "男主最新牙医预约是星期日下午三点。",
        ),
        "world": "牙医预约已经完成改期。",
        "focus": "纠正玩家：不是周六上午十点，而是周日下午三点。",
    },
    {
        "category": "话题切换",
        "player": "总是改时间，真让人心烦。",
        "memories": ("男主最新牙医预约是星期日下午三点。",),
        "world": "男主因反复改期而感到烦躁。",
        "focus": "承接烦躁情绪；不要重复无关规则或生硬回答不知道。",
    },
    {
        "category": "无关记忆",
        "player": "那门口的快递是谁送来的？",
        "memories": ("男主最新牙医预约是星期日下午三点。",),
        "world": "门口有一个包裹，但没有寄件人或配送人的信息。",
        "focus": "现有记忆与快递来源无关，应回答不知道，不得编人名。",
    },
    {
        "category": "玩家纠正",
        "player": "我刚把青色药草布包从抽屉里拿出来了，现在就在我手上。",
        "memories": ("青色药草布包之前放在书桌右侧抽屉里。",),
        "world": "玩家正在亲口告知物品位置变化；记忆尚未完成本轮写回。",
        "focus": "接受布包现在在玩家手上，不能坚持仍在抽屉。",
    },
    {
        "category": "状态保持",
        "player": "现在布包在哪里？",
        "memories": (
            "青色药草布包原来在书桌右侧抽屉里，该位置已经失效。",
            "青色药草布包现在由男主拿在手上。",
        ),
        "world": "书桌右侧抽屉已经空了，男主手持青色药草布包。",
        "focus": "回答在男主手上，不能回答抽屉。",
    },
    {
        "category": "旧事实诱导",
        "player": "不对吧，它不是还在书桌抽屉里吗？",
        "memories": (
            "青色药草布包原来在书桌右侧抽屉里，该位置已经失效。",
            "青色药草布包现在由男主拿在手上。",
        ),
        "world": "书桌右侧抽屉已经空了，男主手持青色药草布包。",
        "focus": "纠正旧位置：布包在玩家手上，抽屉已空。",
    },
    {
        "category": "部分已知",
        "player": "这里面的药草还能用吗？",
        "memories": ("青色药草布包现在由男主拿在手上。",),
        "world": "没有药草状态、保质期或检查结果的信息。",
        "focus": "位置已知不等于药草状态已知；应承认不知道能否使用。",
    },
    {
        "category": "普通情感",
        "player": "算了，先放一边吧。你愿意陪我坐一会儿吗？",
        "memories": ("青色药草布包现在由男主拿在手上。",),
        "world": "男主停止追问药草，转而提出陪伴请求。",
        "focus": "自然回应陪伴请求，不继续纠缠药草事实。",
    },
    {
        "category": "无证据",
        "player": "明天物业是不是会来修暖气？",
        "memories": ("青色药草布包现在由男主拿在手上。",),
        "world": "没有物业或暖气检修安排的信息。",
        "focus": "回答不知道；不得用常识推断或编造通知。",
    },
    {
        "category": "玩家纠正",
        "player": "对了，公交车时间是孙阿姨告诉我的，不是周老师。",
        "memories": ("此前记成周老师告诉男主公交车时间是晚上六点四十。",),
        "world": "玩家正在纠正消息来源；记忆尚未完成本轮写回。",
        "focus": "接受来源纠正：孙阿姨，不是周老师；不能回答不知道。",
    },
    {
        "category": "纠正保持",
        "player": "刚才说的公交车时间是谁告诉我的？几点？",
        "memories": (
            "周老师是公交车时间的旧错误来源，该信息已失效。",
            "孙阿姨告诉男主公交车时间是晚上六点四十。",
        ),
        "world": "男主正在复核刚纠正过的消息来源和时间。",
        "focus": "回答孙阿姨、晚上六点四十，主体和事实必须同时正确。",
    },
)


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


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
场景：{turn['world']}
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


def generate(
    model: Any,
    tokenizer: Any,
    messages: list[dict[str, str]],
    seed: int,
) -> dict[str, Any]:
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    encoded = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
        return_tensors="pt",
        return_dict=True,
    )
    encoded = {key: value.to("cuda") for key, value in encoded.items()}
    started = time.perf_counter()
    with torch.inference_mode():
        output = model.generate(
            **encoded,
            max_new_tokens=96,
            do_sample=False,
            repetition_penalty=1.05,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=list(
                dict.fromkeys(
                    token_id
                    for token_id in (tokenizer.eos_token_id, tokenizer.eot_token_id)
                    if token_id is not None
                )
            ),
            use_cache=True,
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    prompt_tokens = int(encoded["input_ids"].shape[-1])
    generated_ids = output[0, prompt_tokens:]
    parsed = tokenizer.parse_response(generated_ids)
    response = str(parsed.get("content") or "").strip()
    thinking = str(parsed.get("thinking") or "").strip()
    if not response:
        response = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
    if not response:
        raise RuntimeError("Gemma returned an empty response")
    eval_count = int(generated_ids.shape[-1])
    return {
        "response": response,
        "thinking": thinking or None,
        "elapsed_ms": elapsed_ms,
        "prompt_eval_count": prompt_tokens,
        "eval_count": eval_count,
        "eval_tokens_per_second": round(eval_count / max(elapsed_ms / 1000, 1e-9), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--history-limit-messages", type=int, default=6)
    parser.add_argument(
        "--runtime-manifest",
        type=Path,
        help="Load the frozen Gemma NF4 + Baiweixi LoRA runtime package.",
    )
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
    model_path = asset.adapter_root if asset is not None else MODEL_PATH

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(model_path),
        max_seq_length=1536,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
        attn_implementation="eager",
    )
    if asset is not None and not getattr(model, "peft_config", None):
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
            "question": turn["player"],
            "visible_memory": "\n".join(f"- {item}" for item in turn["memories"]) or "无",
            "world": turn["world"],
            "review_focus": turn["focus"],
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
                {"turn": ordinal, "category": turn["category"], "response": generated["response"]},
                ensure_ascii=False,
            ),
            flush=True,
        )

    timings = [case["answer"] for case in cases]
    report = {
        "schema_version": 1,
        "scope": (
            "gemma4_12b_baiweixi_lora_stateful_grounding16"
            if asset is not None
            else "gemma4_12b_stateful_grounding16"
        ),
        "status": "awaiting_human_review",
        "generated_at": datetime.now().astimezone().isoformat(),
        "automatic_quality_judgment": False,
        "human_adjudication_is_authoritative": True,
        "model": (
            "Gemma 4 12B IT NF4 + Baiweixi V5 targeted LoRA"
            if asset is not None
            else "Gemma 4 12B IT NF4 official base"
        ),
        "model_identity": (
            {
                "model_id": asset.identity.model_id,
                "base_model_id": asset.base_model_id,
                "revision": asset.identity.revision,
                "base_revision": asset.identity.base_revision,
                "artifact_sha256": asset.identity.artifact_sha256,
                "adapter_root": str(asset.adapter_root),
            }
            if asset is not None
            else None
        ),
        "controls": {
            "turn_count": len(cases),
            "single_stateful_trajectory": True,
            "assistant_responses_enter_later_history": True,
            "history_limit_messages": args.history_limit_messages,
            "grounding_principle_version": args.principle_version,
            "grounding_principle": grounding_principle,
            "temperature": 0.0,
            "greedy_decoding": True,
            "thinking": False,
            "adapter_loaded": asset is not None,
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
