"""Generate and self-review the bounded 64-row V5 mixed pilot."""

from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import httpx

from build_v5_pilot_dataset import (
    ROOT,
    _load_baiweixi_render_context,
    _read_jsonl,
    _write_jsonl,
)
from data_gen_v4.runtime_grounded import (
    evaluate_conversational_candidate,
    render_conversational_training_record,
)
from data_gen_v4.runtime_grounded.renderer import (
    build_game_reply_chat_messages,
    parse_game_reply_dialogue_lines,
    render_game_reply_context_from_model_view,
)


STYLE_GROUPS = {
    "idle_chat": [
        ("刚才路过厨房，闻到一点香味。", "嗯，今天突然想弄点热乎的。你闻出来是什么了吗？"),
        ("今天外面看起来挺安静的。", "是啊，难得清静一会儿。你想聊点什么？"),
        ("我刚才差点把钥匙忘在门口。", "你这记性，幸好还想起来了。下次出门前摸一下口袋。"),
        ("晚上要不要放点轻松的东西听？", "可以啊，别选太吵的，我今天想安静一点。"),
        ("我发现窗边的光线比客厅舒服。", "那就坐过去吧，眼睛也能轻松一点。"),
        ("今天好像比昨天过得快。", "忙起来就是这样。至少今天没有那么闷。"),
        ("你有没有觉得屋里有点空？", "有一点。要不要把灯和音乐都开得柔和些？"),
        ("我刚才看到一只猫从楼下跑过去。", "真的？它跑得快不快？说不定是在追什么。"),
        ("这杯水放一会儿就凉了。", "那就别一次倒太多，慢慢喝正好。"),
        ("我今天突然想吃点清淡的。", "那就别勉强自己吃重口味的，我陪你想想。"),
        ("刚才那阵风还挺舒服。", "嗯，比闷在屋里好。要不要开一会儿窗？"),
        ("我把桌面收拾了一下。", "看起来清爽多了。这样找东西也方便。"),
        ("今天没什么特别的事发生。", "没事，平平稳稳的一天也很好。"),
        ("我刚才差点睡着。", "那说明你是真的累了。要不要先休息一会儿？"),
        ("这首歌听起来有点旧。", "旧一点也没关系，有时候反而更耐听。"),
        ("你今天怎么这么安静？", "在想事情而已。你愿意说话的话，我在听。"),
    ],
    "emotion_attunement": [
        ("我今天有点烦，什么都不想做。", "那就先别逼自己做决定，歇一会儿也没关系。"),
        ("我刚才把事情搞砸了。", "先别急着给自己下结论，我们看看还能不能补回来。"),
        ("其实我有点害怕明天。", "我听到了。你不用现在就把所有事都想明白。"),
        ("今天被人说了几句，心里不太舒服。", "换成我也会在意。你想说说具体发生了什么吗？"),
        ("我觉得自己最近总是在拖延。", "能发现这一点已经是开始了。我们先挑一件最小的事做。"),
        ("我现在有点累，但还不想睡。", "那就先陪你坐一会儿，不急着马上睡。"),
        ("我刚才突然觉得很孤单。", "我在这里。你不用急着把这种感觉赶走。"),
        ("我怕自己又会忘记。", "那我们把它记下来，不让你一个人扛着记。"),
        ("我今天其实挺开心的。", "那很好啊。是什么事情让你这么开心？"),
        ("我不知道该不该继续。", "先说说你最担心的那一部分，我们一起拆开看。"),
        ("我有点后悔刚才说的话。", "如果你愿意，我们可以想想怎么把意思说清楚。"),
        ("今天感觉什么都不顺。", "听起来确实很累。先让自己缓下来，再处理下一件。"),
        ("我不太想麻烦别人。", "需要帮忙不等于给别人添麻烦，你可以先告诉我。"),
        ("我现在脑子很乱。", "那就先不急着整理全部，挑最吵的那个念头说出来。"),
        ("我突然有点想家。", "这种时候想家很正常。你想起什么了？"),
        ("我觉得自己好像没做好。", "你已经很认真了。我们可以具体看看哪里还需要补。"),
    ],
    "natural_fact_embed": [
        ("外面好像要下雨了，出门前要不要找把伞？", "要找的，折叠伞现在在玄关伞架上，拿起来就能走。"),
        ("我想先把衣服洗了再休息。", "可以，洗衣机现在在漂洗，等这一轮结束就好。"),
        ("要是冷的话，热水器是不是还开着？", "已经开着了，你要用热水直接去就行。"),
        ("我们晚点是不是还要出门？", "你的理发预约是星期四下午五点，时间还来得及。"),
        ("我想把书找出来接着看。", "借来的书在书架第二层，你从右边找比较快。"),
        ("今天晚上要不要早点准备？", "你的公交车是晚上六点四十，别卡得太紧就好。"),
        ("我想把窗户关好再睡。", "厨房窗户现在留着一条缝，你顺手关上就行。"),
        ("电饭煲是不是还在保温？", "现在是预约煮饭，不是保温。要改的话我提醒你看一下按键。"),
        ("我找不到手机充电器了。", "它在沙发旁的插座那里，你先去那边看看。"),
        ("桌面风扇还能用吗？", "已经修好了，可以正常用。"),
        ("我想吃点水果，放在哪里？", "洗好的水果在餐桌果盘里，拿了就能吃。"),
        ("今晚是不是要记得吃药？", "你的胃药是晚饭后吃，不是睡前。"),
        ("白围巾我好像没见到。", "在衣帽架上，你看靠外面的那一侧。"),
        ("快递到了吗？", "还在驿站待取，不是已经送到门口了。"),
        ("阳台门我关好了吗？", "已经锁好了，你不用再跑一趟。"),
        ("我想找备用钥匙。", "在玄关挂钩上，别去厨房抽屉里找，那是之前记错的位置。"),
    ],
}

FACT_CONTEXT = {
    "外面好像要下雨了，出门前要不要找把伞？": ("当前状态：折叠伞在玄关伞架上。", "玄关伞架"),
    "我想先把衣服洗了再休息。": ("当前状态：洗衣机正在漂洗。", "漂洗"),
    "要是冷的话，热水器是不是还开着？": ("当前状态：热水器已经开启。", "已经开启"),
    "我们晚点是不是还要出门？": ("当前状态：你的理发预约是星期四下午五点。", "星期四下午五点"),
    "我想把书找出来接着看。": ("当前状态：借来的书在书架第二层。", "书架第二层"),
    "今天晚上要不要早点准备？": ("当前状态：你的公交车是晚上六点四十。", "晚上六点四十"),
    "我想把窗户关好再睡。": ("当前状态：厨房窗户留了一条缝。", "留了一条缝"),
    "电饭煲是不是还在保温？": ("当前状态：电饭煲现在是预约煮饭。", "预约煮饭"),
    "我找不到手机充电器了。": ("当前状态：手机充电器在沙发旁插座。", "沙发旁插座"),
    "桌面风扇还能用吗？": ("当前状态：桌面风扇已经修好。", "已经修好"),
    "我想吃点水果，放在哪里？": ("当前状态：洗好的水果在餐桌果盘。", "餐桌果盘"),
    "今晚是不是要记得吃药？": ("当前状态：你的胃药是晚饭后吃。", "晚饭后"),
    "白围巾我好像没见到。": ("当前状态：白围巾在衣帽架。", "衣帽架"),
    "快递到了吗？": ("当前状态：快递还在驿站待取。", "驿站待取"),
    "阳台门我关好了吗？": ("当前状态：阳台门已经锁好。", "已经锁好"),
    "我想找备用钥匙。": ("当前状态：备用钥匙在玄关挂钩。", "玄关挂钩"),
}

FACT_UNKNOWN_OVERRIDES = {
    "我想先把衣服洗了再休息。": ("洗衣机", "当前运行模式"),
    "要是冷的话，热水器是不是还开着？": ("热水器", "当前电源状态"),
    "电饭煲是不是还在保温？": ("电饭煲", "当前运行模式"),
}

# The generator owns a complete state projection.  A missing dynamic value is
# represented as unknown instead of being omitted, so the teacher cannot fill
# it with household common sense.
HOME_RUNTIME_STATE = {
    "item_locations": {
        "备用钥匙": "玄关挂钩",
        "借来的书": "书架第二层",
        "手机充电器": "沙发旁插座",
        "白围巾": "衣帽架",
        "洗好的水果": "餐桌果盘",
        "折叠伞": "玄关伞架",
    },
    "appliance_states": {
        "洗衣机": {"当前运行模式": None, "剩余时间": None},
        "冰箱": {"当前电源状态": "通电", "当前运行模式": None},
        "燃气灶": {"当前电源状态": None, "当前运行模式": None},
        "抽油烟机": {"当前电源状态": None, "当前运行模式": None},
        "电饭煲": {"当前运行模式": None, "剩余时间": None},
        "微波炉": {"当前电源状态": None, "当前运行模式": None},
        "电热水壶": {"当前电源状态": None, "当前运行模式": None},
        "风扇": {"当前电源状态": None, "当前运行模式": None},
        "电视": {"当前电源状态": None, "当前频道": None},
        "热水器": {"当前电源状态": None, "剩余热水量": None},
    },
}

PROTAGONIST_BASELINE = (
    "男主基础设定：男主是四川人。男主基础偏好（资料状态，不代表白未晞已知）：四川口味；能吃辣；喜欢油腻；"
    "平时喝温水、饮料偏低糖；喜欢安静、通风和柔和灯光；喜欢常用物品有固定位置；"
    "喜欢自然聊天和安静陪伴；低落时希望先被听见；接受温和提醒；不喜欢未经询问翻看手机或私人物品。"
    "食物过敏与禁忌：未知。"
)


def _attach_authoritative_runtime_state(view: dict[str, Any]) -> None:
    """Add complete known/unknown home state to the production-visible view."""
    known = []
    unknown = list(view.get("unknown_facts") or ())
    for subject, location in HOME_RUNTIME_STATE["item_locations"].items():
        known.append(f"{subject}的当前位置是{location}。")
    for subject, states in HOME_RUNTIME_STATE["appliance_states"].items():
        for predicate, value in states.items():
            if value is None:
                unknown.append({
                    "subject": subject,
                    "predicate": predicate,
                    "source": "权威世界状态",
                    "reason": "当前世界快照没有直接证据",
                    "status": "未知",
                })
            else:
                known.append(f"{subject}的{predicate}是{value}。")
    existing = list(view.get("game_feedback") or ())
    for line in known:
        if line not in existing:
            existing.append(line)
    view["game_feedback"] = ["权威家庭状态：" + "；".join(known)]
    view["unknown_facts"] = unknown[:32]
    view["authoritative_world"]["facts"] = [
        {"subject": "家庭场景", "predicate": "固定状态", "object": line, "source": "世界正典", "status": "当前有效"}
        for line in known
    ]
    view["authoritative_world"]["unknown_facts"] = list(view["unknown_facts"])


def _native_call(base_url: str, model: str, messages: list[dict[str, str]], max_tokens: int) -> str:
    native = base_url.rstrip("/")[:-3] if base_url.rstrip("/").endswith("/v1") else base_url.rstrip("/")
    payload = {
        "model": model, "messages": messages, "stream": False, "format": "json",
        "keep_alive": "10m", "options": {"num_ctx": 8192, "num_predict": max_tokens, "temperature": 0.55},
        "think": False,
    }
    response = httpx.post(native + "/api/chat", json=payload, timeout=90.0)
    response.raise_for_status()
    return str((response.json().get("message") or {}).get("content", ""))


def _json_object(text: str) -> dict:
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model output has no JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model output is not an object")
    return value


def _call_json_with_retry(base_url, model, messages, max_tokens, *, attempts=3):
    """Return parsed JSON plus raw responses; malformed output is retried."""
    errors = []
    for attempt in range(attempts):
        raw = _native_call(base_url, model, messages, max_tokens + attempt * 160)
        try:
            return _json_object(raw), {"raw": raw, "attempt": attempt + 1, "errors": errors}
        except Exception as error:
            errors.append(str(error))
    raise ValueError("JSON output failed after retries: " + " | ".join(errors))


def _scenario(group: str, index: int, user_text: str, *, fact_context=None) -> dict:
    from build_v5_pilot_dataset import _base_model_view

    view = _base_model_view(5000 + index)
    view["character_display_name"] = "白未晞"
    view["current_protagonist_utterance"] = user_text
    view["previous_heroine_runtime"]["living_mind"]["emotion"] = "平静但有一点在意"
    view["previous_heroine_runtime"]["living_mind"]["attention"] = "男主当前的话"
    view["recent_dialogue"] = ["男主：今天总觉得时间过得很快。", "白未晞：嗯，我也有这种感觉。"]
    view["game_feedback"].append(PROTAGONIST_BASELINE)
    # Make the authority boundary explicit even for conversational samples.
    view["unknown_facts"] = [{
        "subject": "未在当前快照登记的细节",
        "predicate": "状态",
        "source": "权威世界状态",
        "reason": "当前世界快照没有直接证据",
        "status": "未知",
    }]
    view["authoritative_world"]["unknown_facts"] = list(view["unknown_facts"])
    _attach_authoritative_runtime_state(view)
    scenario = {
        "schema_version": "aip.conversational_reply_scenario.v1",
        "scenario_id": f"style.{group}.{index:03d}",
        "profile_id": "baiweixi",
        "task_type": group,
        "model_view": view,
        "style_contract": {
            "style_intent": {
                "idle_chat": "轻松自然地闲聊，可有轻微主动追问",
                "emotion_attunement": "先接住男主情绪，再给出自然回应",
                "natural_fact_embed": "在自然聊天中准确表达上下文已有事实",
            }[group],
            "reply_shape": "spoken_only_natural_dialogue",
            "forbidden_styles": ["action_narration", "markdown", "assistant_tone", "unsupported_specific_detail"],
            "unsupported_expansion_policy": "reject",
        },
        "split_anchors": [f"style:{group}", f"scene:home-{index % 4}", f"shape:spoken-{index % 3}"],
    }
    if group == "natural_fact_embed":
        feedback, required_phrase = fact_context if fact_context is not None else FACT_CONTEXT[user_text]
        fixed_lines = [f"{subject}的当前位置是{location}。" for subject, location in HOME_RUNTIME_STATE["item_locations"].items()]
        fixed_lines.extend(
            f"{subject}的{predicate}是{value}。"
            for subject, states in HOME_RUNTIME_STATE["appliance_states"].items()
            for predicate, value in states.items() if value is not None
        )
        view["game_feedback"] = [feedback, "权威家庭状态：" + "；".join(fixed_lines)]
        # The scenario-specific fact is the only dynamic override.  Remove its
        # matching unknown entry before exposing the final state table.
        overridden = FACT_UNKNOWN_OVERRIDES.get(user_text)
        if overridden:
            view["unknown_facts"] = [
                item for item in view.get("unknown_facts", [])
                if (str(item.get("subject")), str(item.get("predicate"))) != overridden
            ]
        view["authoritative_world"]["facts"].append({
            "fact_id": f"fact.conversational.{index:03d}",
            "subject": "context_subject",
            "predicate": "authoritative_statement",
            "object": feedback,
            "source": "game_feedback",
            "status": "current",
        })
        view["authoritative_world"]["unknown_facts"] = list(view["unknown_facts"])
        scenario["fact_contract"] = {
            "required_phrases": [required_phrase],
            "answer_required": user_text != "外面好像要下雨了，出门前要不要找把伞？",
        }
    return scenario


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=ROOT / "训练数据" / "baiweixi_v5_conversational64_pilot_20260911")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", default="qwen2.5:14b-instruct-q4_K_M")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--groups", nargs="+", choices=tuple(STYLE_GROUPS), default=list(STYLE_GROUPS))
    args = parser.parse_args()
    _, _, anchor = _load_baiweixi_render_context()
    root = args.output_root
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)

    source = ROOT / "训练数据" / "baiweixi_v5_small_game_reply_pilot_20260911" / "inputs" / "game_reply_candidates.jsonl"
    factual = _read_jsonl(source)[:16]
    for row in factual:
        row["pilot_branch"] = "FACT_GROUNDED_HUMAN_APPROVED"

    style_scenarios: list[dict] = []
    for group in args.groups:
        pairs = STYLE_GROUPS[group]
        for offset, (user_text, _) in enumerate(pairs[:16]):
            style_scenarios.append(_scenario(group, len(style_scenarios), user_text))
    _write_jsonl(inputs / "conversational_scenarios.jsonl", style_scenarios)

    def run(scenario: dict) -> dict:
        context = render_game_reply_context_from_model_view(scenario["model_view"])
        contract = json.dumps(scenario["style_contract"], ensure_ascii=False)
        dialogue = build_game_reply_chat_messages(
            parse_game_reply_dialogue_lines(tuple(scenario["model_view"].get("recent_dialogue") or ())),
            character_display_name="白未晞",
            current_protagonist_utterance=scenario["model_view"]["current_protagonist_utterance"],
        )
        teacher_system = (
            "你是白未晞的自然对白实现器。只根据生产上下文自然接话，不输出动作旁白、Markdown、"
            "系统解释或JSON以外字段。权威世界状态没有提供的事实必须说不知道，不能用常识、应该、可能或估计补全。"
            "设备存在不等于正在运行，运行中不等于快结束。只输出 {\"reply\":\"...\"}。\n角色锚：" + anchor +
            "\n生产上下文：" + context + "\n风格目标：" + contract
        )
        teacher_messages = [{"role": "system", "content": teacher_system}]
        teacher_messages.extend({"role": role, "content": content} for role, content in dialogue)
        target, generation_meta = _call_json_with_retry(args.base_url, args.model, teacher_messages, 240)
        audit_system = (
            "你是独立的白未晞自然对白审核器。审核回复是否像白未晞、是否接住当前话题、是否自然、"
            "是否无依据扩展。逐项核对所有事实主张；没有权威来源或来源为unknown就必须判失败。只输出指定JSON。\n角色锚：" + anchor + "\n生产上下文：" + context +
            "\n风格合同：" + contract + "\n待审核回复：" + str(target.get("reply", "")) +
            "\nJSON格式：{\"verdicts\":{\"voice\":true,\"turn_relevance\":true,\"naturalness\":true,\"unsupported_expansion\":true},\"decision\":\"approve|reject|human_review\",\"reasons\":[\"...\"]}"
        )
        audit, audit_meta = _call_json_with_retry(args.base_url, args.model, [{"role": "system", "content": audit_system}, *teacher_messages[1:]], 360)
        report = evaluate_conversational_candidate(scenario, target, audit)
        return {"sample_id": scenario["scenario_id"], "scenario": scenario, "target": target, "audit": audit, "generation_meta": generation_meta, "audit_meta": audit_meta, "report": report, "training_record": render_conversational_training_record(scenario, target, system_anchor=anchor).to_dict()}

    rows: list[dict] = []
    failures: list[dict] = []
    lock = Lock()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run, scenario): scenario for scenario in style_scenarios}
        for future in as_completed(futures):
            scenario = futures[future]
            try:
                row = future.result()
                (rows if row["report"]["approved"] else failures).append(row)
            except Exception as error:
                failures.append({"sample_id": scenario["scenario_id"], "error": str(error), "scenario": scenario, "target": {"reply": ""}, "audit": {}, "report": {"approved": False, "reasons": ["pipeline_failure:" + str(error)]}})
    rows.sort(key=lambda x: x["sample_id"])
    failures.sort(key=lambda x: x["sample_id"])
    _write_jsonl(inputs / "conversational_approved.jsonl", rows)
    _write_jsonl(inputs / "conversational_rejected.jsonl", failures)
    _write_jsonl(inputs / "conversational_all_for_review.jsonl", rows + failures)
    report = {
        "schema_version": "aip.v5_conversational64_pilot_report.v1",
        "total": len(factual) + len(style_scenarios), "factual_human_approved": len(factual), "style_generated": len(style_scenarios),
        "style_approved": len(rows), "style_rejected": len(failures),
        "formal_training_started": False, "mixed_training_package_created": False,
        "files": ["inputs/conversational_scenarios.jsonl", "inputs/conversational_approved.jsonl", "inputs/conversational_rejected.jsonl"],
    }
    (root / "pilot_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
