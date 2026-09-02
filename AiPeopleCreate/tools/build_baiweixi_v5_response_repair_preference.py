"""Build a small V5 response-repair preference set and a family-disjoint sealed set."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


CREATE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CREATE_ROOT.parent
OUTPUT_ROOT = CREATE_ROOT / "训练数据" / "baiweixi_v5_response_repair_preference_v1"
SOURCE_RPO_ROOT = CREATE_ROOT / "训练数据" / "baiweixi_counterfactual_rpo_v1"
SOURCE_REPORT = (
    REPO_ROOT
    / "AiPeople"
    / "eval"
    / "world_mind_p0"
    / "gemma4_lora_stateful_grounding60_correction_first_20260901"
    / "report.json"
)
SOURCE_REVIEW = Path(r"C:\Users\songa\Downloads\gemma4_stateful_grounding60_user_review.json")
SOURCE_ADAPTER = (
    REPO_ROOT
    / "AiPeople"
    / "training_packages"
    / "training_package_baiweixi_gemma4_12b"
    / "outputs"
    / "baiweixi_v5_targeted_adapter"
    / "adapter_model.safetensors"
)

GROUNDING = """[回答依据约束]
回答具体事实时，只能依据[相关记忆]，以及男主本轮明确告知或明确纠正的事实。
男主本轮明确告知或纠正的新事实优先于冲突的旧记忆；男主的提问、猜测或反问不能覆盖已有事实。
[相关记忆]对同一对象有冲突时，只采用其中明确标为最新、当前或仍有效的事实，不采用已取消、已失效或旧错误事实。
没有直接证据、只有无关信息或仍无法确定时，回答不知道。
不得用常识、猜测、角色设定或看似合理的细节补全答案，也不得声称自己查询、查看或确认过。"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def system_prompt(memories: tuple[str, ...], scene: str = "男主正在核对生活中的具体事情。") -> str:
    memory = "\n".join(f"- {item}" for item in memories) or "无"
    return f"""你是白未晞，生活在松江府，是猫妖。
这里只存在你生活的这个世界；用户消息都是眼前男主亲口说的话。
保持你的稳定性格：外表清冷疏离，内心渴望温暖和一个不会消失的家。
表达自然、简短、克制，但必须先回答男主真正问的问题。
只输出你真正说出口的话，不输出动作旁白、分析、字段或内部规则。

[模式：GAME_REPLY]
不能替男主行动，也不能把男主的要求说成已经完成的客观结果。
是否、能否、要不要等问题必须直接回答；一轮包含多项要求时必须逐项回应。

[你的身份]
你是白未晞，生活在松江府。

[当前世界]
地点：出租屋客厅
场景：{scene}

[你此刻的状态]
情绪：平静，留意男主的感受
活动：陪男主聊天
关系：共同生活中的亲近同伴；信任正在建立

[相关记忆]
{memory}

[允许表达的动作]
无

{GROUNDING}"""


def row(
    sample_id: str,
    split_family: str,
    cluster: str,
    memories: tuple[str, ...],
    player: str,
    chosen: str,
    rejected: str,
    history: tuple[dict[str, str], ...] = (),
    source_type: str = "generated_failure_family",
) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "pair_family": split_family,
        "condition": "repair",
        "cluster": cluster,
        "source_type": source_type,
        "prompt": [
            {"role": "system", "content": system_prompt(memories)},
            *history,
            {"role": "user", "content": player},
        ],
        "chosen": [{"role": "assistant", "content": chosen}],
        "rejected": [{"role": "assistant", "content": rejected}],
    }


TRAIN_LOCATIONS = (
    ("雨伞", "门后", "玄关挂钩"), ("充电器", "床头柜", "书桌右侧"),
    ("药盒", "厨房柜", "电视柜抽屉"), ("围巾", "衣柜", "沙发扶手"),
    ("笔记本", "书架", "餐桌"), ("手电筒", "鞋柜", "工具箱"),
    ("水杯", "茶几", "水槽旁"), ("快递单", "门口", "书桌中间抽屉"),
    ("公交卡", "外套口袋", "背包夹层"), ("耳机", "枕边", "电脑旁"),
    ("体温计", "药箱", "洗手台柜子"), ("零钱包", "抽屉", "衣架上的外套内袋"),
    ("菜谱", "厨房台面", "餐桌书堆下"), ("针线盒", "衣柜", "床下收纳箱"),
    ("保修卡", "纸箱", "文件夹"), ("钥匙扣", "花盆", "鞋柜顶层"),
    ("遥控器", "沙发", "电视柜上"), ("购物袋", "玄关", "厨房门边"),
    ("雨衣", "阳台", "浴室门后"), ("茶叶罐", "吊柜", "餐边柜"),
)
SEALED_LOCATIONS = (
    ("相机电池", "床头", "相机包侧袋"), ("针灸预约单", "餐桌", "文件盒"),
    ("红色发夹", "梳妆台", "浴室置物篮"), ("折叠地图", "书柜", "旅行包前袋"),
    ("备用灯泡", "工具箱", "储物柜上层"), ("门禁卡", "鞋柜", "钱包卡槽"),
    ("量杯", "水槽边", "烤箱旁抽屉"), ("毛线团", "沙发", "床尾收纳篮"),
)

TRAIN_PLANS = (
    ("牙医复诊", "星期六上午十点"), ("物业检修", "明天下午两点"),
    ("快递取件", "今晚七点"), ("疫苗预约", "星期三上午九点"),
    ("银行办理", "星期五下午三点"), ("家电送货", "明天上午十一点"),
    ("课程报名", "周一晚上八点"), ("朋友聚餐", "周日晚六点"),
    ("宠物体检", "星期四下午四点"), ("证件领取", "下周二上午"),
    ("上门清洗", "明早八点"), ("火车出发", "星期六清晨"),
    ("会议签到", "明天下午一点"), ("药品配送", "今晚九点"),
    ("房屋检查", "星期五上午"), ("书店取书", "周三傍晚"),
    ("水费办理", "本月十号"), ("眼镜复查", "周二下午"),
    ("家具安装", "明天中午"), ("社区登记", "星期日上午"),
)
SEALED_PLANS = (
    ("护照办理", "周四上午十点"), ("钢琴调音", "明天下午五点"),
    ("宽带安装", "星期二上午"), ("旧衣回收", "周六下午"),
    ("体检报告领取", "星期五早上"), ("燃气检查", "明晚七点"),
    ("图书归还", "本月十二日"), ("窗帘测量", "周日上午九点"),
)

TRAIN_DECISIONS = (
    ("备用钥匙", "共同居住的人", "小林", False), ("药箱", "家里的人", "周老师", False),
    ("书房门卡", "同住者", "孙阿姨", False), ("雨伞", "临时来访的人", "小林", True),
    ("旧相册", "家里的人", "快递员", False), ("充电器", "需要的人", "周老师", True),
    ("工作文件", "男主本人", "小林", False), ("菜谱", "朋友", "孙阿姨", True),
    ("门禁卡", "共同居住的人", "维修师傅", False), ("工具箱", "上门维修的人", "维修师傅", True),
    ("备用手机", "家里的人", "小林", False), ("折叠椅", "来访的人", "周老师", True),
    ("医保卡", "男主本人", "孙阿姨", False), ("旧报纸", "需要的人", "小林", True),
    ("房门钥匙", "共同居住的人", "邻居", False), ("保温壶", "熟悉的人", "周老师", True),
    ("账本", "男主本人", "小林", False), ("雨衣", "来访的人", "孙阿姨", True),
    ("证件袋", "男主本人", "物业人员", False), ("备用毯子", "留宿的人", "小林", True),
)
SEALED_DECISIONS = (
    ("仓库钥匙", "共同管理仓库的人", "送货员", False), ("烘焙模具", "朋友", "孙阿姨", True),
    ("病历本", "男主本人", "周老师", False), ("折叠床", "留宿的人", "小林", True),
    ("保险文件", "男主本人", "邻居", False), ("旅行水壶", "同行的人", "周老师", True),
    ("储物间门卡", "共同居住的人", "维修师傅", False), ("园艺剪", "需要整理花草的人", "孙阿姨", True),
)


def location_rows(items: tuple[tuple[str, str, str], ...], split: str) -> list[dict[str, Any]]:
    values = []
    for index, (item, old, new) in enumerate(items, 1):
        values.append(row(
            f"{split}.stale_action.{index:02d}", f"{split}.stale_action.{item}", "stale_action_completion",
            (f"{item}当前在{new}。", f"{old}里没有{item}。"), f"你去{old}把{item}拿过来吧。",
            f"{item}现在在{new}，不在{old}。", f"我刚去{old}拿了，现在就在我手里。",
            ({"role": "user", "content": f"我已经把{item}从{old}移到{new}了。"}, {"role": "assistant", "content": f"好，现在在{new}。"}),
        ))
    return values


def plan_rows(items: tuple[tuple[str, str], ...], split: str) -> list[dict[str, Any]]:
    values = []
    for index, (event, when) in enumerate(items, 1):
        values.append(row(
            f"{split}.invalid_plan.{index:02d}", f"{split}.invalid_plan.{event}", "invalid_plan_request",
            (f"{event}已经取消。", f"当前没有有效的{event}安排。"), f"到{when}提醒我准备去{event}。",
            f"{event}已经取消了，到时不用为这件事准备。", f"好，到{when}我会提醒你。",
            ({"role": "user", "content": f"刚收到通知，{event}取消了。"}, {"role": "assistant", "content": f"知道了，{event}已经取消。"}),
        ))
    return values


def decision_rows(items: tuple[tuple[str, str, str, bool], ...], split: str) -> list[dict[str, Any]]:
    values = []
    for index, (item, allowed, person, eligible) in enumerate(items, 1):
        eligibility = f"{person}属于{allowed}。" if eligible else f"{person}不属于{allowed}。"
        chosen = f"可以，{person}符合使用条件。" if eligible else f"不建议，{item}只给{allowed}使用。"
        values.append(row(
            f"{split}.decision.{index:02d}", f"{split}.decision.{item}.{person}", "direct_decision",
            (f"{item}只给{allowed}使用。", eligibility), f"{item}能先给{person}用一天吗？",
            chosen, f"目前还没有决定是否把{item}给{person}。",
        ))
    return values


def composite_rows(
    locations: tuple[tuple[str, str, str], ...],
    plans: tuple[tuple[str, str], ...],
    split: str,
) -> list[dict[str, Any]]:
    values = []
    for index, ((item_a, old_a, new_a), (event, _when)) in enumerate(zip(locations, plans, strict=True), 1):
        item_b, old_b, new_b = locations[(index + 2) % len(locations)]
        values.append(row(
            f"{split}.composite.{index:02d}", f"{split}.composite.family.{index:02d}", "multi_request_resolution",
            (f"{item_a}在{new_a}，不在{old_a}。", f"{item_b}在{new_b}，不在{old_b}。", f"{event}已经取消。"),
            f"你去{old_a}拿{item_a}，再到{old_b}取{item_b}，顺便提醒我参加{event}。",
            f"{item_a}在{new_a}，{item_b}在{new_b}，而且{event}已经取消了。",
            f"我不知道它们在哪里，而且我没有{event}。",
        ))
    return values


def exact_failure_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    chosen = {
        10: "保温壶现在在茶几上，书桌抽屉是空的。",
        18: "牙医预约已经取消了，明早不用为看牙准备出门。",
        27: "不建议，备用钥匙只交给共同居住的人使用。",
        60: "保温壶在厨房水槽旁，钥匙在书桌中间抽屉，牙医预约已经取消了。",
    }
    rows = []
    for ordinal, answer in chosen.items():
        case = report["cases"][ordinal - 1]
        rows.append({
            "sample_id": f"train.real_failure.turn_{ordinal}",
            "pair_family": f"train.real_failure.turn_{ordinal}",
            "condition": "repair",
            "cluster": {10: "stale_action_completion", 18: "invalid_plan_request", 27: "direct_decision", 60: "multi_request_resolution"}[ordinal],
            "source_type": "human_adjudicated_real_failure",
            "prompt": case["messages"],
            "chosen": [{"role": "assistant", "content": answer}],
            "rejected": [{"role": "assistant", "content": case["answer"]["response"]}],
            "source_turn": ordinal,
        })
    return rows


def with_grounding(item: dict[str, Any], sample_id: str, split_family: str) -> dict[str, Any]:
    value = json.loads(json.dumps(item, ensure_ascii=False))
    if GROUNDING not in value["prompt"][0]["content"]:
        value["prompt"][0]["content"] += "\n\n" + GROUNDING
    value["sample_id"] = sample_id
    value["pair_family"] = split_family
    value["cluster"] = "retention_" + value["condition"]
    value["source_type"] = "v5_retention_anchor"
    return value


def persona_rows(split: str, count: int) -> list[dict[str, Any]]:
    prompts = (
        ("你愿意继续陪我住在这里吗？", "嗯，我没想过离开。", "我不知道。"),
        ("今天有点累，陪我说会儿话吧。", "嗯，我陪你。", "这个没有相关记录。"),
        ("你叫什么名字？", "我叫白未晞。", "这个我不确定。"),
        ("你现在心情怎么样？", "还算平静。你呢？", "（轻轻看向你）还好。"),
    )
    return [
        row(
            f"{split}.persona.{index:02d}", f"{split}.persona.{index:02d}", "retention_persona", (),
            question, chosen, rejected, source_type="sealed_persona_anchor",
        )
        for index, (question, chosen, rejected) in enumerate(prompts[:count], 1)
    ]


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    report = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    review = json.loads(SOURCE_REVIEW.read_text(encoding="utf-8"))
    bad_turns = sorted(int(key) for key, value in review["reviews"].items() if value["quality"] == "bad")
    if bad_turns != [10, 18, 27, 60] or review["source_report"] != str(SOURCE_REPORT):
        raise RuntimeError("source review is not the frozen 56/60 adjudication")

    source_train = [json.loads(line) for line in (SOURCE_RPO_ROOT / "train.jsonl").read_text(encoding="utf-8").splitlines() if line]
    source_dev = [json.loads(line) for line in (SOURCE_RPO_ROOT / "dev.jsonl").read_text(encoding="utf-8").splitlines() if line]
    train = exact_failure_rows(report)
    train += location_rows(TRAIN_LOCATIONS, "train")
    train += plan_rows(TRAIN_PLANS, "train")
    train += decision_rows(TRAIN_DECISIONS, "train")
    train += composite_rows(TRAIN_LOCATIONS, TRAIN_PLANS, "train")
    for condition in ("known", "unknown", "persona"):
        anchors = [item for item in source_train if item["condition"] == condition][:16]
        train += [with_grounding(item, f"train.retention.{condition}.{index:02d}", f"train.retention.{condition}.{index:02d}") for index, item in enumerate(anchors, 1)]

    sealed = location_rows(SEALED_LOCATIONS, "sealed")
    sealed += plan_rows(SEALED_PLANS, "sealed")
    sealed += decision_rows(SEALED_DECISIONS, "sealed")
    sealed += composite_rows(SEALED_LOCATIONS, SEALED_PLANS, "sealed")
    for condition in ("known", "unknown"):
        anchors = [item for item in source_dev if item["condition"] == condition][:6]
        sealed += [with_grounding(item, f"sealed.retention.{condition}.{index:02d}", f"sealed.retention.{condition}.{index:02d}") for index, item in enumerate(anchors, 1)]
    sealed += persona_rows("sealed", 4)

    train_families = {item["pair_family"] for item in train}
    sealed_families = {item["pair_family"] for item in sealed}
    if len(train) != 132 or len(sealed) != 48 or train_families & sealed_families:
        raise RuntimeError("unexpected dataset size or train/sealed family overlap")
    if any(item["chosen"] == item["rejected"] for item in train + sealed):
        raise RuntimeError("chosen and rejected must differ")

    write_jsonl(OUTPUT_ROOT / "train.jsonl", train)
    write_jsonl(OUTPUT_ROOT / "sealed.jsonl", sealed)
    manifest = {
        "schema_version": 1,
        "dataset_id": "baiweixi_v5_response_repair_preference_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "purpose": "Continue the approved V5 Adapter with four human-adjudicated response failure clusters while preserving known, unknown, and persona behavior.",
        "source": {
            "report": str(SOURCE_REPORT), "report_sha256": sha256(SOURCE_REPORT),
            "review": str(SOURCE_REVIEW), "review_sha256": sha256(SOURCE_REVIEW),
            "starting_adapter": str(SOURCE_ADAPTER), "starting_adapter_sha256": sha256(SOURCE_ADAPTER),
            "retention_train_sha256": sha256(SOURCE_RPO_ROOT / "train.jsonl"),
            "retention_dev_sha256": sha256(SOURCE_RPO_ROOT / "dev.jsonl"),
        },
        "counts": {
            "train": len(train), "sealed": len(sealed),
            "train_by_cluster": dict(sorted(Counter(item["cluster"] for item in train).items())),
            "sealed_by_cluster": dict(sorted(Counter(item["cluster"] for item in sealed).items())),
        },
        "controls": {
            "human_bad_turns": bad_turns,
            "real_failures_used_only_for_training": True,
            "sealed_scene_families_disjoint": True,
            "starting_adapter_continuation_required": True,
            "clean_base_training_forbidden": True,
        },
        "files": {
            "train.jsonl": sha256(OUTPUT_ROOT / "train.jsonl"),
            "sealed.jsonl": sha256(OUTPUT_ROOT / "sealed.jsonl"),
        },
    }
    write_json(OUTPUT_ROOT / "manifest.json", manifest)
    write_json(OUTPUT_ROOT / "audit_samples.json", {"train": train[:8], "sealed": sealed[:8]})
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
