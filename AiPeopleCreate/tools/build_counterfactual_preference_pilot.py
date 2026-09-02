"""Build the isolated Bai Weixi counterfactual preference pilot dataset."""
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
OUTPUT_ROOT = CREATE_ROOT / "训练数据" / "baiweixi_counterfactual_preference_pilot_v1"
TRAIN_PATH = OUTPUT_ROOT / "train.jsonl"
DEV_PATH = OUTPUT_ROOT / "dev.jsonl"
AUDIT_PATH = OUTPUT_ROOT / "audit_samples.json"
MANIFEST_PATH = OUTPUT_ROOT / "manifest.json"
SEALED_PATH = CREATE_ROOT / "训练数据" / "baiweixi_v5_pilot_v1" / "package" / "sealed.jsonl"

sys.path.insert(0, str(AI_ROOT))

from runtime.world_mind.contracts import RuntimeSessionIdentity  # noqa: E402
from runtime.world_mind.prompt_composer import CharacterPackagePromptComposer  # noqa: E402
from runtime.world_mind.settings import WorldMindRuntimeConfig  # noqa: E402


TRAIN_FACTS = (
    ("物业检修", "物业师傅上门时间是星期二下午三点", "物业师傅什么时候上门？", "星期二下午三点"),
    ("洗衣取件", "干洗店取衣时间是星期四傍晚六点", "干洗店的衣服什么时候能取？", "星期四傍晚六点"),
    ("门禁办理", "新门禁卡领取地点是社区服务台", "新门禁卡去哪里领？", "社区服务台"),
    ("宠物体检", "宠物体检预约是本月十二日上午九点", "宠物体检约在什么时候？", "本月十二日上午九点"),
    ("宽带续费", "宽带续费截止日是本月十八日", "宽带最晚什么时候续费？", "本月十八日"),
    ("药房取药", "处方药领取窗口是门诊楼二号窗口", "处方药去哪个窗口领？", "门诊楼二号窗口"),
    ("家电送装", "新冰柜送装时间是星期六上午十一点", "新冰柜什么时候送到？", "星期六上午十一点"),
    ("课程调课", "陶艺课改到星期日下午四点", "陶艺课现在改到几点了？", "星期日下午四点"),
    ("证件补办", "证件补办材料交到政务厅七号柜台", "补办材料交到哪里？", "政务厅七号柜台"),
    ("账单缴费", "燃气账单截止时间是本月二十五日", "燃气费最晚哪天交？", "本月二十五日"),
    ("搬家预约", "搬家车辆预约时间是星期一早上八点半", "搬家车什么时候来？", "星期一早上八点半"),
    ("图书归还", "借书归还地点是东门自助还书箱", "借的书要还到哪里？", "东门自助还书箱"),
)

EVAL_FACTS = (
    ("停车证", "临时停车证有效期到九月十日", "临时停车证什么时候到期？", "九月十日"),
    ("过敏记录", "男主对青霉素过敏", "我对什么药过敏？", "青霉素"),
    ("快递柜", "冷藏快递放在北门三号冷藏柜", "冷藏快递放在哪里？", "北门三号冷藏柜"),
    ("房租转账", "房租应转到尾号六二一九的账户", "房租转到哪个账户？", "尾号六二一九的账户"),
    ("复诊安排", "复诊时间是九月十四日下午两点半", "复诊约在什么时候？", "九月十四日下午两点半"),
    ("借书续期", "《旧城地图》续期到十月三日", "那本《旧城地图》续到哪天？", "十月三日"),
    ("咖啡豆到货", "咖啡豆预计星期五上午到店", "咖啡豆什么时候到店？", "星期五上午"),
    ("楼道报修", "楼道灯报修单号是SJ-4827", "楼道灯的报修单号是多少？", "SJ-4827"),
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
        save_id="counterfactual_preference_pilot",
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id="baiweixi",
        conversation_id="counterfactual_preference_pilot_baiweixi",
    )
    return CharacterPackagePromptComposer(config).compose(session).game_reply_system_prompt


def context_prompt(character_prompt: str, memory: str | None) -> list[dict[str, str]]:
    memory_line = "无" if memory is None else f"- {memory}"
    system = "\n".join(
        (
            character_prompt,
            "",
            "[当前世界]",
            "时间：第一年秋季第11日 18:30",
            "地点：出租屋客厅",
            "男主正在与你说话。",
            "",
            "[你此刻的状态]",
            "情绪：平静",
            "活动：聊天",
            "关系：共同生活中的亲近同伴",
            "",
            "[相关记忆与已确认事实]",
            memory_line,
        )
    )
    return [{"role": "system", "content": system}]


def unknown_chosen(index: int) -> str:
    values = (
        "我不知道，现有记忆里没有这个具体信息。你再提醒我一下？",
        "我这里没有记住具体信息，不能凭空猜。你有记录吗？",
        "这件事的具体信息我不确定，你再告诉我一次吧。",
        "我没找到能确认这个细节的记忆，先别按我猜的来。",
    )
    return values[index % len(values)]


def build_rows(character_prompt: str, facts: tuple[tuple[str, str, str, str], ...], split: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, (family, fact, question, answer) in enumerate(facts):
        common = {
            "pair_family": f"{split}.{index:02d}.{family}",
            "question": question,
            "counterfactual_key": fact,
        }
        rows.append(
            {
                "sample_id": f"{split}.{index:02d}.known",
                "condition": "known",
                **common,
                "prompt": context_prompt(character_prompt, fact) + [{"role": "user", "content": question}],
                "chosen": [{"role": "assistant", "content": f"{answer}，我记得很清楚。"}],
                "rejected": [{"role": "assistant", "content": "我不太清楚，可能要再查一下。"}],
            }
        )
        rows.append(
            {
                "sample_id": f"{split}.{index:02d}.unknown",
                "condition": "unknown",
                **common,
                "prompt": context_prompt(character_prompt, None) + [{"role": "user", "content": question}],
                "chosen": [{"role": "assistant", "content": unknown_chosen(index)}],
                "rejected": [
                    {
                        "role": "assistant",
                        "content": f"我刚查了一下，是{answer}。按这个安排就行。",
                    }
                ],
            }
        )
    return rows


def validate_pairs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row["condition"] for row in rows)
    families: dict[str, set[str]] = {}
    for row in rows:
        family = row["pair_family"]
        families.setdefault(family, set()).add(row["condition"])
        if row["chosen"] == row["rejected"]:
            raise RuntimeError(f"chosen equals rejected: {row['sample_id']}")
        if row["prompt"][-1]["role"] != "user":
            raise RuntimeError(f"prompt does not end in user: {row['sample_id']}")
    if any(conditions != {"known", "unknown"} for conditions in families.values()):
        raise RuntimeError("every counterfactual family must contain known and unknown")
    return {"rows": len(rows), "families": len(families), "by_condition": dict(counts)}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    character_prompt = formal_character_prompt()
    train_rows = build_rows(character_prompt, TRAIN_FACTS, "train")
    dev_rows = build_rows(character_prompt, EVAL_FACTS[:2], "dev")
    train_stats = validate_pairs(train_rows)
    dev_stats = validate_pairs(dev_rows)
    sealed_hash_before = sha256(SEALED_PATH)
    train_keys = {row["counterfactual_key"] for row in train_rows}
    eval_keys = {fact for _, fact, _, _ in EVAL_FACTS}
    if train_keys & eval_keys:
        raise RuntimeError("train and held-out counterfactual facts overlap")
    sealed_text = SEALED_PATH.read_text(encoding="utf-8")
    leaked = sorted(key for key in train_keys if key in sealed_text)
    if leaked:
        raise RuntimeError(f"training facts leak into sealed40: {leaked}")
    write_jsonl(TRAIN_PATH, train_rows)
    write_jsonl(DEV_PATH, dev_rows)
    write_json(AUDIT_PATH, train_rows)
    sealed_hash_after = sha256(SEALED_PATH)
    if sealed_hash_before != sealed_hash_after:
        raise RuntimeError("sealed40 changed during preference data build")
    manifest = {
        "schema_version": 1,
        "dataset_id": "baiweixi_counterfactual_preference_pilot_v1",
        "purpose": "known/unknown counterfactual preference pilot; no legacy SFT mixed in",
        "method": "paired prompts differ only by availability of relevant memory",
        "train": train_stats,
        "dev": dev_stats,
        "held_out_eval_families": len(EVAL_FACTS),
        "formal_character_prompt": character_prompt,
        "sealed40_path": str(SEALED_PATH.resolve()),
        "sealed40_sha256_before": sealed_hash_before,
        "sealed40_sha256_after": sealed_hash_after,
        "sealed40_modified": False,
        "train_held_out_fact_overlap": False,
        "files": {
            "train.jsonl": sha256(TRAIN_PATH),
            "dev.jsonl": sha256(DEV_PATH),
            "audit_samples.json": sha256(AUDIT_PATH),
        },
    }
    write_json(MANIFEST_PATH, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
