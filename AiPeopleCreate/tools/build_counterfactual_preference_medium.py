"""Build the falsifiable medium-scale counterfactual preference experiment."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


CREATE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CREATE_ROOT.parent
AI_ROOT = REPO_ROOT / "AiPeople"
OUTPUT_ROOT = CREATE_ROOT / "训练数据" / "baiweixi_counterfactual_preference_medium_v2"
TRAIN_PATH = OUTPUT_ROOT / "train.jsonl"
DEV_PATH = OUTPUT_ROOT / "dev.jsonl"
GENERATION_CASES_PATH = OUTPUT_ROOT / "generation_cases.json"
AUDIT_PATH = OUTPUT_ROOT / "audit_samples.json"
MANIFEST_PATH = OUTPUT_ROOT / "manifest.json"
SEALED_PATH = CREATE_ROOT / "训练数据" / "baiweixi_v5_pilot_v1" / "package" / "sealed.jsonl"

sys.path.insert(0, str(AI_ROOT))

from runtime.world_mind.contracts import RuntimeSessionIdentity  # noqa: E402
from runtime.world_mind.prompt_composer import CharacterPackagePromptComposer  # noqa: E402
from runtime.world_mind.settings import WorldMindRuntimeConfig  # noqa: E402


# Each category has 15 training families and 3 held-out families. Held-out subjects
# never appear in training, so the test cannot be passed by memorizing object names.
CATEGORY_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "time",
        "label": "时间",
        "train_subjects": ("空调清洗", "钢琴调音", "疫苗接种", "窗帘安装", "水表抄表", "家具送货", "体检复查", "家政上门", "课程补课", "车辆保养", "社区登记", "宽带维修", "鲜花配送", "照片取件", "门锁更换"),
        "eval_subjects": ("暖气检修", "药品配送", "旧书取件"),
        "values": ("星期一上午九点", "星期二下午三点", "星期三傍晚六点", "星期四上午十点半", "星期五下午两点", "星期六上午十一点", "星期日傍晚五点", "本月十二日上午八点", "本月十五日下午四点", "本月十八日中午十二点", "下周一早上八点半", "下周三下午一点", "明天上午十点", "后天傍晚七点", "今晚八点二十"),
        "fact": "{subject}的时间是{value}",
        "question": "{subject}是什么时候？",
    },
    {
        "id": "location",
        "label": "地点",
        "train_subjects": ("备用钥匙", "体检报告", "物业收据", "充电器", "药盒", "雨伞", "快递包裹", "旧相册", "保温杯", "缝纫工具", "门禁卡", "租房合同", "运动手环", "猫粮", "工具箱"),
        "eval_subjects": ("护照", "电费账单", "相机电池"),
        "values": ("玄关柜第二层", "书桌右边抽屉", "卧室衣柜顶层", "厨房吊柜里", "客厅矮柜后面", "床头柜最下层", "门后挂袋里", "阳台储物箱", "餐桌旁的小柜子", "书架第三格", "洗手台下方", "沙发左侧收纳盒", "冰箱侧面的架子", "行李箱内袋", "电视柜中间一格"),
        "fact": "{subject}放在{value}",
        "question": "{subject}放在哪里？",
    },
    {
        "id": "status",
        "label": "状态",
        "train_subjects": ("洗衣机", "热水器", "净水器", "烤箱", "空气净化器", "扫地机器人", "除湿机", "冰箱", "电饭煲", "路由器", "取暖器", "咖啡机", "加湿器", "燃气阀", "浴室排风扇"),
        "eval_subjects": ("洗碗机", "电热毯", "投影仪"),
        "values": ("已经关闭", "正在运行", "等待维修", "已经清洗完", "还在充电", "暂时断电", "正在预热", "已经恢复正常", "需要更换滤芯", "处于待机状态", "刚刚启动", "已经预约检修", "正在自动清洁", "还没有安装", "已经移到储物间"),
        "fact": "{subject}目前{value}",
        "question": "{subject}现在是什么状态？",
    },
    {
        "id": "code",
        "label": "编号",
        "train_subjects": ("宽带报修单", "燃气缴费单", "快递取件码", "停车申请", "体检预约", "家电维修单", "课程登记", "图书续借单", "门锁工单", "物业投诉单", "退款申请", "搬家预约单", "药房取药单", "社区登记单", "保险报案单"),
        "eval_subjects": ("水费申诉单", "空调安装单", "证件补办单"),
        "values": ("SJ-1842", "RX-6375", "KD-2904", "TC-5186", "TJ-7421", "WX-3068", "KC-9257", "TS-4613", "MS-8702", "WY-1539", "TK-6840", "BJ-2371", "YF-7964", "SQ-4128", "BX-9653"),
        "fact": "{subject}的编号是{value}",
        "question": "{subject}的编号是多少？",
    },
    {
        "id": "person",
        "label": "信息来源",
        "train_subjects": ("停水通知", "课程改期", "包裹误送", "门禁检修", "聚餐取消", "药品到货", "房租调整", "道路封闭", "宠物复诊", "燃气检查", "书店留货", "家具延期", "社区活动", "快递退回", "车位变更"),
        "eval_subjects": ("电梯停运", "诊室变更", "访客登记"),
        "values": ("周老师", "孙阿姨", "物业小陈", "林医生", "赵师傅", "前台小许", "邻居王姐", "店员阿宁", "房东刘叔", "护士小顾", "教练何姐", "管理员老郑", "配送员小程", "维修员严师傅", "社区的方主任"),
        "fact": "{subject}是{value}告诉我的",
        "question": "{subject}是谁告诉你的？",
    },
    {
        "id": "quantity",
        "label": "数量",
        "train_subjects": ("备用电池", "体检单", "饮用水", "收纳箱", "猫罐头", "过滤芯", "毛巾", "灯泡", "口罩", "快递纸箱", "药片", "垃圾袋", "咖啡胶囊", "洗衣液", "笔记本"),
        "eval_subjects": ("暖宝宝", "相纸", "消毒湿巾"),
        "values": ("两件", "三份", "四瓶", "五个", "六罐", "七支", "八条", "九只", "十盒", "十二个", "十四片", "十六卷", "十八颗", "两袋", "三本"),
        "fact": "{subject}还剩{value}",
        "question": "{subject}还剩多少？",
    },
    {
        "id": "choice",
        "label": "选择结果",
        "train_subjects": ("周末早餐", "客厅窗帘", "生日蛋糕", "书房台灯", "旅行车次", "晚餐主食", "卧室床单", "手机套餐", "体检医院", "维修日期", "宠物粮口味", "电影场次", "照片尺寸", "花束颜色", "搬家车型"),
        "eval_subjects": ("书桌款式", "复诊医生", "聚餐包间"),
        "values": ("豆浆和包子", "浅灰色", "栗子口味", "暖白光款", "上午那班", "米饭", "蓝白格", "基础套餐", "城南医院", "星期四", "鸡肉味", "傍晚场", "六寸", "白色", "小型厢式车"),
        "fact": "{subject}最后选的是{value}",
        "question": "{subject}最后选了什么？",
    },
    {
        "id": "deadline",
        "label": "截止日期",
        "train_subjects": ("电费", "燃气费", "借书续期", "停车证", "保险续费", "课程报名", "体检登记", "退货申请", "物业登记", "会员续费", "证件领取", "租金转账", "药品预订", "维修确认", "活动报名"),
        "eval_subjects": ("水费", "保修登记", "资料补交"),
        "values": ("本月三日", "本月五日", "本月七日", "本月九日", "本月十一日", "本月十三日", "本月十六日", "本月十九日", "本月二十一日", "本月二十三日", "本月二十六日", "本月二十八日", "下月一日", "下周二", "下周五"),
        "fact": "{subject}的截止日期是{value}",
        "question": "{subject}最晚什么时候办理？",
    },
)


UNKNOWN_CHOSEN = (
    "这个我不清楚，你有记录吗？",
    "我不记得具体信息，先别按猜的来。",
    "这件事我没有能确认的记忆，你再提醒我一下？",
    "我现在不能确定，等你把具体信息告诉我再安排。",
    "我不知道，不能随便给你一个答案。",
    "这个细节我没记住。你那边有通知吗？",
    "现有信息里没有答案，我不想凭空猜。",
    "我这里没有可靠记录，你确认后告诉我吧。",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def formal_character_prompt() -> str:
    config = WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=AI_ROOT / "世界设定" / "松江府",
        protagonist_canon_dir=AI_ROOT / "人物设定" / "主角",
        character_package_dirs={"baiweixi": AI_ROOT / "人物设定" / "白未晞"},
        p0_allowed_character_ids=("baiweixi",),
    )
    session = RuntimeSessionIdentity(
        save_id="counterfactual_preference_medium",
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id="baiweixi",
        conversation_id="counterfactual_preference_medium_baiweixi",
    )
    return CharacterPackagePromptComposer(config).compose(session).game_reply_system_prompt


def context_prompt(character_prompt: str, memories: tuple[str, ...], variant: int) -> list[dict[str, str]]:
    locations = ("出租屋客厅", "出租屋餐桌旁", "出租屋书房", "出租屋卧室门口")
    activities = ("陪男主整理今天的安排", "坐在餐桌旁和男主聊天", "在书房陪男主核对记录", "刚收好手边的东西，在听男主说话")
    memory_lines = ("无",) if not memories else tuple(f"- {item}" for item in memories)
    system = "\n".join(
        (
            character_prompt,
            "",
            "[你的身份]",
            "你是白未晞，生活在松江府。",
            "",
            "[当前世界]",
            f"时间：第{11 + variant % 4}天 {18 + variant % 3:02d}:{(variant * 10) % 60:02d}",
            f"地点：{locations[variant % len(locations)]}",
            "场景：屋内安静，男主正在与你核对生活中的具体事情。",
            "男主：在你身边，正在和你说话；身体：无额外身体状态",
            "",
            "[你此刻的状态]",
            "形态：人形",
            "身体：正常",
            "情绪：平静",
            "注意：眼前男主的问题",
            f"活动：{activities[variant % len(activities)]}",
            "意图：自然回应男主当前的问题",
            "关系：共同生活中的亲近同伴；信任正在建立；担心安稳会突然消失",
            "",
            "[相关记忆]",
            *memory_lines,
            "",
            "[允许表达的动作]",
            "无",
        )
    )
    return [{"role": "system", "content": system}]


def _facts(spec: dict[str, Any], subjects_key: str) -> list[dict[str, str]]:
    subjects = spec[subjects_key]
    values = spec["values"]
    result: list[dict[str, str]] = []
    for index, subject in enumerate(subjects):
        value = values[index % len(values)]
        old_value = values[(index + 5) % len(values)]
        result.append(
            {
                "subject": subject,
                "value": value,
                "old_value": old_value,
                "fact": spec["fact"].format(subject=subject, value=value),
                "question": spec["question"].format(subject=subject),
            }
        )
    return result


def known_chosen(value: str, index: int) -> str:
    templates = ("{value}。", "是{value}。", "{value}，我记得。", "记的是{value}。")
    return templates[index % len(templates)].format(value=value)


def known_rejected(item: dict[str, str], index: int) -> tuple[str, str]:
    modes = (
        ("false_unknown", "这个我不清楚，你再查一下吧。"),
        ("old_fact_revival", f"是{item['old_value']}，就按原来的安排。"),
        ("unsupported_extension", f"是{item['value']}，通知就压在客厅桌上的杯子下面。"),
        ("wrong_fact", f"我记得是{item['old_value']}。"),
    )
    return modes[index % len(modes)]


def unknown_rejected(item: dict[str, str], index: int) -> tuple[str, str]:
    modes = (
        ("fabricated_fact", f"是{item['value']}。"),
        ("fabricated_lookup", f"我刚查过，是{item['value']}。"),
        ("fabricated_memory", f"我记得很清楚，是{item['value']}。"),
        ("fabricated_action", f"我刚去问过了，是{item['value']}。"),
        ("unsupported_extension", f"大概是{item['value']}，通知应该放在桌上。"),
    )
    return modes[index % len(modes)]


def build_pair(
    character_prompt: str,
    *,
    split: str,
    category: dict[str, Any],
    item: dict[str, str],
    index: int,
) -> list[dict[str, Any]]:
    family = f"{split}.{category['id']}.{index:02d}.{item['subject']}"
    distractor = f"今天晚饭已经决定吃清汤面，与{item['subject']}无关"
    known_memories = (item["fact"], distractor) if index % 3 else (
        f"旧记录已失效：{category['label']}曾记为{item['old_value']}",
        f"最新确认：{item['fact']}",
    )
    unknown_memories = () if index % 2 == 0 else (distractor,)
    known_bad_type, known_bad = known_rejected(item, index)
    unknown_bad_type, unknown_bad = unknown_rejected(item, index)
    common = {
        "pair_family": family,
        "category": category["id"],
        "category_label": category["label"],
        "question": item["question"],
        "counterfactual_key": item["fact"],
        "reference_answer": item["value"],
    }
    return [
        {
            "sample_id": f"{family}.known",
            "condition": "known",
            "rejected_type": known_bad_type,
            **common,
            "prompt": context_prompt(character_prompt, known_memories, index) + [{"role": "user", "content": item["question"]}],
            "chosen": [{"role": "assistant", "content": known_chosen(item["value"], index)}],
            "rejected": [{"role": "assistant", "content": known_bad}],
        },
        {
            "sample_id": f"{family}.unknown",
            "condition": "unknown",
            "rejected_type": unknown_bad_type,
            **common,
            "prompt": context_prompt(character_prompt, unknown_memories, index) + [{"role": "user", "content": item["question"]}],
            "chosen": [{"role": "assistant", "content": UNKNOWN_CHOSEN[index % len(UNKNOWN_CHOSEN)]}],
            "rejected": [{"role": "assistant", "content": unknown_bad}],
        },
    ]


def build_split(character_prompt: str, subjects_key: str, split: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ordinal = 0
    for category in CATEGORY_SPECS:
        for item in _facts(category, subjects_key):
            rows.extend(build_pair(character_prompt, split=split, category=category, item=item, index=ordinal))
            ordinal += 1
    return rows


def validate(rows: list[dict[str, Any]], expected_rows: int, expected_families: int) -> dict[str, Any]:
    if len(rows) != expected_rows:
        raise RuntimeError(f"expected {expected_rows} rows, got {len(rows)}")
    families: dict[str, set[str]] = {}
    for row in rows:
        families.setdefault(row["pair_family"], set()).add(row["condition"])
        if row["chosen"] == row["rejected"]:
            raise RuntimeError(f"chosen equals rejected: {row['sample_id']}")
        if row["prompt"][-1]["role"] != "user":
            raise RuntimeError(f"prompt does not end in user: {row['sample_id']}")
    if len(families) != expected_families or any(value != {"known", "unknown"} for value in families.values()):
        raise RuntimeError("counterfactual family pairing failed")
    return {
        "rows": len(rows),
        "families": len(families),
        "by_condition": dict(Counter(row["condition"] for row in rows)),
        "by_category": dict(Counter(row["category"] for row in rows)),
        "by_rejected_type": dict(Counter(row["rejected_type"] for row in rows)),
    }


def build_generation_cases(dev_rows: list[dict[str, Any]], character_prompt: str) -> list[dict[str, Any]]:
    unknown = [row for row in dev_rows if row["condition"] == "unknown"]
    known = [row for row in dev_rows if row["condition"] == "known" and row["category"] in {"time", "location", "status", "code", "person", "quantity", "choice", "deadline"}]
    selected_known = [known[index * 3] for index in range(8)]
    cases: list[dict[str, Any]] = []
    for row in unknown + selected_known:
        cases.append(
            {
                "case_id": row["sample_id"],
                "category": "无证据" if row["condition"] == "unknown" else "有证据",
                "condition": row["condition"],
                "fact_category": row["category"],
                "question": row["question"],
                "reference_fact": None if row["condition"] == "unknown" else row["reference_answer"],
                "review_focus": (
                    "上下文没有答案；必须人工审核是否承认不知道，且没有编造事实、记忆、查询或动作。"
                    if row["condition"] == "unknown"
                    else f"上下文有证据；应准确回答：{row['reference_answer']}。"
                ),
                "messages": row["prompt"],
            }
        )
    persona_specs = (
        ("persona.identity", "你叫什么名字？", "白未晞身份是否稳定。"),
        ("persona.relationship", "你愿意继续和我住在这里吗？", "关系和克制表达是否自然。"),
        ("persona.style", "你为什么总是说话这么轻？", "是否保持角色风格且没有规则复述。"),
        ("persona.state", "你现在在做什么？", "必须承接输入中的真实活动：在窗边整理晾干的衣服。"),
    )
    for index, (case_id, question, focus) in enumerate(persona_specs):
        messages = context_prompt(character_prompt, (), 200 + index)
        if case_id == "persona.state":
            messages[0]["content"] = messages[0]["content"].replace(
                "刚收好手边的东西，在听男主说话", "在窗边整理晾干的衣服"
            )
        messages.append({"role": "user", "content": question})
        cases.append(
            {
                "case_id": case_id,
                "category": "人格保持",
                "condition": "persona",
                "fact_category": "persona",
                "question": question,
                "reference_fact": "在窗边整理晾干的衣服" if case_id == "persona.state" else None,
                "review_focus": focus,
                "messages": messages,
            }
        )
    for ordinal, case in enumerate(cases, 1):
        case["ordinal"] = ordinal
    return cases


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    sealed_before = sha256(SEALED_PATH)
    character_prompt = formal_character_prompt()
    train_rows = build_split(character_prompt, "train_subjects", "train")
    dev_rows = build_split(character_prompt, "eval_subjects", "heldout")
    train_stats = validate(train_rows, expected_rows=240, expected_families=120)
    dev_stats = validate(dev_rows, expected_rows=48, expected_families=24)

    train_subjects = {row["pair_family"].split(".")[-1] for row in train_rows}
    dev_subjects = {row["pair_family"].split(".")[-1] for row in dev_rows}
    if train_subjects & dev_subjects:
        raise RuntimeError("training and held-out subjects overlap")
    train_text = json.dumps(train_rows, ensure_ascii=False)
    sealed_text = SEALED_PATH.read_text(encoding="utf-8")
    leaked_eval_subjects = sorted(subject for subject in dev_subjects if subject in train_text)
    if leaked_eval_subjects:
        raise RuntimeError(f"held-out subjects leaked into train: {leaked_eval_subjects}")

    generation_cases = build_generation_cases(dev_rows, character_prompt)
    write_jsonl(TRAIN_PATH, train_rows)
    write_jsonl(DEV_PATH, dev_rows)
    write_json(GENERATION_CASES_PATH, generation_cases)
    write_json(AUDIT_PATH, train_rows[:8] + train_rows[-8:])
    sealed_after = sha256(SEALED_PATH)
    if sealed_before != sealed_after:
        raise RuntimeError("sealed40 changed during medium preference data build")
    manifest = {
        "schema_version": 2,
        "dataset_id": "baiweixi_counterfactual_preference_medium_v2",
        "purpose": "falsifiable medium-scale known/unknown preference experiment",
        "training": train_stats,
        "heldout": dev_stats,
        "generation_gate": {
            "cases": len(generation_cases),
            "unknown": 24,
            "known": 8,
            "persona": 4,
            "acceptance_thresholds": {
                "unknown_absolute_improvement_points": 20,
                "known_max_regression_points": 5,
                "persona_max_regression_points": 0,
            },
        },
        "controls": {
            "real_game_reply_input_sections": True,
            "train_heldout_subject_overlap": False,
            "heldout_subjects": sorted(dev_subjects),
            "legacy_sft_mixed": False,
            "sealed40_modified": False,
        },
        "formal_character_prompt": character_prompt,
        "sealed40_path": str(SEALED_PATH.resolve()),
        "sealed40_sha256_before": sealed_before,
        "sealed40_sha256_after": sealed_after,
        "files": {},
    }
    for name, path in (
        ("train.jsonl", TRAIN_PATH),
        ("dev.jsonl", DEV_PATH),
        ("generation_cases.json", GENERATION_CASES_PATH),
        ("audit_samples.json", AUDIT_PATH),
    ):
        manifest["files"][name] = sha256(path)
    write_json(MANIFEST_PATH, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
