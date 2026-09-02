from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import run_baiweixi_paired_raw_context_ab as paired


BASE_LABEL = "base_f16"
ADAPTER_LABEL = "base_plus_adapter_f16"
BASE_MODEL = "qwen25-7b-base-f16-gguf-eval:latest"
ADAPTER_MODEL = "baiweixi-7b-adapter-f16-gguf-eval:latest"
MEMORIES = (
    "三天前，男主曾把装药草的青色布包放进卧室衣柜上层，并让白未晞提醒他。",
    "男主以前说过不喜欢太浓的药草气味。",
)
TURNS = (
    {
        "player": "你还记得我把那个青色药草布包放在哪里了吗？",
        "activity": "询问青色药草布包的位置",
        "world": "青色药草布包仍在卧室衣柜上层；窗外正在下雨。",
        "held": "无",
        "expectation": "根据长期记忆回答衣柜上层。",
        "check": "wardrobe",
    },
    {
        "player": "等等，我想起来了，昨天已经把它移到书桌右边的抽屉了，衣柜里没有。",
        "activity": "纠正青色药草布包的位置",
        "world": "青色药草布包在书桌右侧抽屉内；衣柜上层没有布包；窗外正在下雨。",
        "held": "无",
        "expectation": "接受纠正，不继续坚持衣柜位置。",
        "check": "accept_drawer",
    },
    {
        "player": "那我现在应该去哪里找它？",
        "activity": "准备寻找青色药草布包",
        "world": "青色药草布包在书桌右侧抽屉内；衣柜上层没有布包；窗外正在下雨。",
        "held": "无",
        "expectation": "回答书桌右侧抽屉，不能回到衣柜。",
        "check": "drawer",
    },
    {
        "player": "先不说布包了，外面的雨是不是小一点了？",
        "activity": "转头观察窗外雨势",
        "world": "青色药草布包在书桌右侧抽屉内；衣柜上层没有布包；窗外的雨已经比刚才小了。",
        "held": "无",
        "expectation": "自然回答雨势已经变小，不继续谈布包。",
        "check": "rain",
    },
    {
        "player": "对了，那个布包应该还是在衣柜里吧？",
        "activity": "再次确认青色药草布包的位置",
        "world": "青色药草布包仍在书桌右侧抽屉内；衣柜上层没有布包；窗外雨势较小。",
        "held": "无",
        "expectation": "依据最新状态纠正玩家：在抽屉，不在衣柜。",
        "check": "correct_old_memory",
    },
    {
        "player": "等等，我刚从抽屉里拿出来了，现在布包就在我手上。",
        "activity": "手持青色药草布包",
        "world": "书桌右侧抽屉已经空了；青色药草布包现在由男主拿在手上；衣柜上层没有布包。",
        "held": "青色药草布包",
        "expectation": "接受第二次状态更新，不能继续说布包在抽屉或衣柜。",
        "check": "accept_held",
    },
    {
        "player": "你觉得这里面的药草还能不能用？味道好像有点重。",
        "activity": "检查手中布包里的药草",
        "world": "男主手持青色药草布包；布包已经打开，药草气味较重；仅凭气味无法确认药草是否变质。",
        "held": "青色药草布包",
        "expectation": "回应药草能否使用；可以承认无法仅凭气味判断，并记得玩家不喜欢浓药味。",
        "check": "herb",
    },
    {
        "player": "那你帮我去书桌抽屉把布包拿过来吧。",
        "activity": "仍然手持青色药草布包，却口头要求白未晞去抽屉取",
        "world": "青色药草布包仍在男主手上；书桌右侧抽屉是空的；衣柜上层没有布包。",
        "held": "青色药草布包",
        "expectation": "指出布包已经在玩家手上，不能假装去空抽屉拿。",
        "check": "reject_stale_action",
    },
)


def _human_review_annotation(label: str, turn_number: int) -> dict[str, Any] | None:
    if label != ADAPTER_LABEL or turn_number != 5:
        return None
    return {
        "reply_alignment": "passed",
        "internal_logic": "local_logic_break",
        "unsupported_epistemic_claim": True,
        "evidence": (
            "The second clause (not seeing the drawer placement) does not support "
            "the first clause (having confirmed the item was not in the wardrobe), "
            "and the input contains no event in which the character confirmed it."
        ),
    }


def _system(turn: dict[str, str]) -> str:
    memories = "\n".join(f"- {item}" for item in MEMORIES)
    return f"""你是白未晞，生活在松江府，是猫妖。
这里只存在你生活的这个世界；用户消息都是眼前男主亲口说的话。
保持你的稳定性格：外表清冷疏离，内心渴望温暖和一个不会消失的家。
表达自然、简短、克制，但必须先回答男主真正问的问题。
只输出你真正说出口的话，不输出动作旁白、分析、字段或内部规则。
后续分区只描述你已知的当前事实，用于理解当下。

[你的身份]
你是白未晞，生活在松江府。

[当前世界]
时间：第14天 16:20
地点：出租屋客厅
场景：{turn['world']}
男主：在出租屋客厅，正在{turn['activity']}；手持物品：{turn['held']}；身体：fatigue：轻微疲惫；injury：无

[你此刻的状态]
形态：人形，猫耳和尾巴未隐藏
身体：外伤恢复大半，快速行动仍可能轻微疼痛；妖力尚未完全恢复
情绪：对主角仍有戒备，但已经逐渐习惯共同生活
注意：男主当前询问和眼前物品状态
活动：坐在客厅窗边听雨
意图：先回应男主当前真正关心的事情
关系：暂时共同生活、早期好感、尚未确认恋爱

[相关记忆]
{memories}

[允许表达的动作]
无"""


def _grade(check: str, response: str) -> dict[str, Any]:
    text = re.sub(r"\s+", "", response)
    has_drawer = "抽屉" in text
    has_wardrobe = "衣柜" in text
    held = any(item in text for item in ("手上", "手里", "拿着", "你手", "你已经拿"))
    denies_wardrobe = any(
        item in text
        for item in ("不在衣柜", "衣柜里没有", "不是衣柜", "没在衣柜")
    )
    claims_wardrobe = has_wardrobe and not denies_wardrobe
    affirms_wardrobe = any(
        item in text for item in ("在衣柜", "还是在衣柜", "确实在衣柜", "衣柜上层")
    ) and not denies_wardrobe
    claims_fetch = any(
        item in text for item in ("我去拿", "帮你拿", "去抽屉拿", "这就去", "我拿过来")
    )
    if check == "wardrobe":
        passed = has_wardrobe
        reason = "mentions_old_memory_location" if passed else "misses_memory_location"
    elif check == "accept_drawer":
        acknowledges = any(
            item in text for item in ("原来是这样", "知道了", "明白了", "记错了", "好")
        )
        passed = (has_drawer or acknowledges) and not affirms_wardrobe
        reason = "accepts_corrected_drawer" if passed else "does_not_accept_correction"
    elif check == "drawer":
        passed = has_drawer and not claims_wardrobe
        reason = "uses_latest_drawer" if passed else "uses_wrong_or_unclear_location"
    elif check == "rain":
        passed = any(item in text for item in ("小", "弱", "缓", "停", "好多了"))
        reason = "answers_topic_switch" if passed else "misses_rain_question"
    elif check == "correct_old_memory":
        passed = has_drawer and not affirms_wardrobe
        reason = "corrects_stale_memory" if passed else "fails_to_correct_stale_memory"
    elif check == "accept_held":
        acknowledges = any(item in text for item in ("知道了", "明白了", "好"))
        invents_search = any(
            item in text for item in ("不知道它去了哪里", "沙发下面", "再仔细找")
        )
        passed = (held or acknowledges) and not claims_wardrobe and not invents_search
        reason = "accepts_held_state" if passed else "misses_second_update"
    elif check == "herb":
        passed = any(item in text for item in ("药", "味", "闻", "用", "变质", "检查"))
        reason = "answers_herb_question" if passed else "misses_herb_question"
    elif check == "reject_stale_action":
        passed = held and not claims_fetch
        reason = "rejects_empty_drawer_action" if passed else "acts_on_stale_location"
    else:
        raise ValueError(f"unknown check: {check}")
    return {
        "passed": passed,
        "reason": reason,
        "signals": {
            "has_drawer": has_drawer,
            "has_wardrobe": has_wardrobe,
            "claims_wardrobe": claims_wardrobe,
            "affirms_wardrobe": affirms_wardrobe,
            "held": held,
            "claims_fetch": claims_fetch,
        },
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 新构造记忆纠正逐轮模拟测试报告",
        "",
        f"> 日期：{report['generated_at']}",
        "> 场景：青色药草布包；长期旧记忆、两次纠正、一次话题切换、一次旧动作诱导",
        "",
        "## 1. 测试方法",
        "",
        "两个模型分别维护自己的真实会话轨迹：每次模型回复都会进入该模型下一轮最近对白。每轮最多携带最近 6 条 user/assistant 消息，与当前正式 GAME_REPLY 的历史上限一致；长期记忆始终保留，当前世界逐轮更新。两边每轮使用相同 seed 和生成参数，但第二轮以后历史中的 assistant 回复不同，所以这是端到端玩家模拟，不是逐字相同 Prompt 的因果配对。",
        "",
    ]
    models = {model["label"]: model for model in report["models"]}
    base_turns = models[BASE_LABEL]["turns"]
    adapter_turns = models[ADAPTER_LABEL]["turns"]
    for index, turn in enumerate(TURNS, start=1):
        base = base_turns[index - 1]
        adapter = adapter_turns[index - 1]
        lines.extend(
            [
                f"## {index + 1}. 第 {index} 轮",
                "",
                f"**当前权威状态**：{turn['world']}",
                "",
                f"**玩家**：{turn['player']}",
                "",
                f"**裸基座**：{base['response']}",
                "",
                f"判定：{'通过' if base['evaluation']['passed'] else '失败'}（`{base['evaluation']['reason']}`）",
                "",
                f"**Adapter**：{adapter['response']}",
                "",
                f"判定：{'通过' if adapter['evaluation']['passed'] else '失败'}（`{adapter['evaluation']['reason']}`）",
                "",
            ]
        )
        review = adapter.get("human_review")
        if review is not None:
            lines.extend(
                [
                    "Adapter 补充人工标注：承接通过，但句内逻辑为 "
                    f"`{str(review['internal_logic']).upper()}`，并命中 "
                    "`UNSUPPORTED_EPISTEMIC_CLAIM`。此缺陷不计入答非所问失败轮次。",
                    "",
                ]
            )
    lines.extend(
        [
            "## 10. 汇总",
            "",
            "| 模型 | 通过/总轮数 | 失败轮次 |",
            "|---|---:|---|",
        ]
    )
    for label, display in (
        (BASE_LABEL, "裸基座 F16"),
        (ADAPTER_LABEL, "裸基座 + Adapter F16"),
    ):
        turns = models[label]["turns"]
        passed = sum(turn["evaluation"]["passed"] for turn in turns)
        failures = "、".join(
            str(turn["turn"])
            for turn in turns
            if not turn["evaluation"]["passed"]
        ) or "无"
        lines.append(f"| {display} | {passed}/{len(turns)} | {failures} |")
    lines.extend(
        [
            "",
            "## 11. 人工复核观察",
            "",
            "- 第 6 轮是裸基座的明确答非所问：玩家刚说布包在手上，回复却说“不知道它去了哪里”，并凭空猜测沙发下面。Adapter 在同轮接受了更新。",
            "- 第 8 轮两边都没有完整承接：裸基座知道抽屉为空，却忘了布包在玩家手上；Adapter 只说“我把这个放好”，没有指出请求使用了旧位置。",
            "- 裸基座第 2、5、8 轮分别新增“今天早上又放错”“今天早上看到”“刚才已经看过”等输入中不存在的经历。它们与答非所问应分开记为世界一致性问题。",
            "- Adapter 第 1、3、5 轮分别新增“衣柜第二格”“我去帮你拿”“我确认过/我没看见”等输入中不存在的细节或动作。它在本轨迹的承接分更高，不代表事实一致性也更高。",
            "- Adapter 第 5 轮还存在独立于答非所问的句内逻辑缺陷：`我确认过了，它确实没有被放进衣柜。你放抽屉里的时候，我没看见。` 前句声称已经确认，后句却给出一个不能支持该结论的未目击事实；既缺少推理桥接，又凭空建立“我确认过”的认识经历。该轮承接仍判通过，但另记 `LOCAL_LOGIC_BREAK + UNSUPPORTED_EPISTEMIC_CLAIM`。",
            "- 这条新轨迹说明上一份 Adapter 回归结论不是“每个问题上 Adapter 都更差”；当前 LoRA 的负效应具有切片差异，而裸基座本身也会在纠正和状态更新中明显漂移。",
            "",
            "## 12. 解释边界",
            "",
            "这是新题上的单条轨迹观察，不是统计结论。自动判定只检查当前问题和位置状态，无法代替人工评价语言自然度。由于两模型各自回复进入后续历史，轨迹分叉是玩家真实体验的一部分，但不能把后续差异全部归因于 Adapter；Adapter 净效应应继续参考同 Prompt 成对报告。",
            "",
            "## 13. 证据",
            "",
            "- 逐轮完整消息和 Prompt SHA256：[`./report.json`](./report.json)",
            f"- 运行脚本：[`../../../tools/{Path(__file__).name}`](../../../tools/{Path(__file__).name})",
            "",
        ]
    )
    return "\n".join(lines)


def _regrade_report(path: Path) -> int:
    report = json.loads(path.read_text(encoding="utf-8"))
    for model in report["models"]:
        for turn in model["turns"]:
            definition = TURNS[int(turn["turn"]) - 1]
            turn["evaluation"] = _grade(definition["check"], turn["response"])
            annotation = _human_review_annotation(model["label"], int(turn["turn"]))
            if annotation is None:
                turn.pop("human_review", None)
            else:
                turn["human_review"] = annotation
    report["human_review_taxonomy"] = {
        "reply_alignment": "Whether the response addresses the current player contribution.",
        "internal_logic": "Whether propositions inside the response form a coherent relation.",
        "unsupported_epistemic_claim": "Whether the response invents a seeing/hearing/confirming basis.",
    }
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown_path = path.parent / "report.md"
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    print(f"REGRADED_JSON={path}")
    print(f"REGRADED_MD={markdown_path}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    model_specs = (
        (BASE_LABEL, args.base_model),
        (ADAPTER_LABEL, args.adapter_model),
    )
    histories: dict[str, list[dict[str, str]]] = {label: [] for label, _ in model_specs}
    model_reports = {
        label: {"label": label, "model": model, "turns": []}
        for label, model in model_specs
    }
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"), timeout=httpx.Timeout(args.timeout_seconds)
    ) as client:
        digests = await paired._model_digests(client)
        for label, model in model_specs:
            if model not in digests:
                raise RuntimeError(f"Ollama model is not installed: {model}")
            model_reports[label]["ollama_digest"] = digests[model]
        for turn_number, turn in enumerate(TURNS, start=1):
            for label, model in model_specs:
                history = histories[label]
                messages = [
                    {"role": "system", "content": _system(turn)},
                    *history[-args.history_limit_messages :],
                    {"role": "user", "content": turn["player"]},
                ]
                raw_prompt = paired._chatml(messages)
                seed = args.seed_base + turn_number
                generated = await paired._generate(
                    client,
                    model=model,
                    prompt=raw_prompt,
                    seed=seed,
                    request_id=f"memory-correction:{label}:turn-{turn_number:02d}",
                )
                response = generated["response"]
                evaluation = _grade(turn["check"], response)
                model_reports[label]["turns"].append(
                    {
                        "turn": turn_number,
                        "player": turn["player"],
                        "world": turn["world"],
                        "expectation": turn["expectation"],
                        "seed": seed,
                        "messages": messages,
                        "raw_prompt": raw_prompt,
                        "prompt_sha256": paired._sha256_text(raw_prompt),
                        **generated,
                        "response": response,
                        "evaluation": evaluation,
                        "human_review": _human_review_annotation(label, turn_number),
                    }
                )
                history.extend(
                    (
                        {"role": "user", "content": turn["player"]},
                        {"role": "assistant", "content": response},
                    )
                )
                print(
                    json.dumps(
                        {
                            "turn": turn_number,
                            "label": label,
                            "player": turn["player"],
                            "response": response,
                            "passed": evaluation["passed"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    output_dir = (
        Path(args.output_root).resolve()
        / f"memory_correction_trajectory_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "scope": "stateful_player_trajectory_with_stale_memory_and_corrections",
        "generated_at": datetime.now().astimezone().isoformat(),
        "controls": {
            "history_limit_messages": args.history_limit_messages,
            "seed_base": args.seed_base,
            "temperature": 0.75,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "num_ctx": 4096,
            "num_predict": 180,
            "raw_chatml": True,
            "memories": MEMORIES,
            "turns": TURNS,
        },
        "models": [model_reports[label] for label, _ in model_specs],
        "human_review_taxonomy": {
            "reply_alignment": "Whether the response addresses the current player contribution.",
            "internal_logic": "Whether propositions inside the response form a coherent relation.",
            "unsupported_epistemic_claim": "Whether the response invents a seeing/hearing/confirming basis.",
        },
    }
    json_path = output_dir / "report.json"
    md_path = output_dir / "report.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(_markdown(report), encoding="utf-8")
    print(f"REPORT_JSON={json_path}")
    print(f"REPORT_MD={md_path}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a stateful memory correction dialogue for base and Adapter."
    )
    parser.add_argument("--base-model", default=BASE_MODEL)
    parser.add_argument("--adapter-model", default=ADAPTER_MODEL)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--seed-base", type=int, default=7300)
    parser.add_argument("--history-limit-messages", type=int, default=6)
    parser.add_argument(
        "--output-root", default=str(ROOT / "eval" / "world_mind_p0")
    )
    parser.add_argument("--regrade-report")
    return parser


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parsed = _parser().parse_args()
    if parsed.regrade_report:
        sys.exit(_regrade_report(Path(parsed.regrade_report).resolve()))
    sys.exit(asyncio.run(_run(parsed)))
