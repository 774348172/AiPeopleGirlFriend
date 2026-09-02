"""Build mixed RPO data from on-policy negatives, known pairs, and persona retention."""
from __future__ import annotations

import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any


CREATE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CREATE_ROOT.parent
AI_ROOT = REPO_ROOT / "AiPeople"
MEDIUM_ROOT = CREATE_ROOT / "训练数据" / "baiweixi_counterfactual_preference_medium_v2"
OUTPUT_ROOT = CREATE_ROOT / "训练数据" / "baiweixi_counterfactual_rpo_v1"
CANDIDATES_PATH = OUTPUT_ROOT / "base_unknown_candidates.jsonl"
TRAIN_PATH = OUTPUT_ROOT / "train.jsonl"
DEV_PATH = OUTPUT_ROOT / "dev.jsonl"
AUDIT_PATH = OUTPUT_ROOT / "audit_samples.json"
MANIFEST_PATH = OUTPUT_ROOT / "manifest.json"

sys.path.insert(0, str(CREATE_ROOT / "tools"))
import build_counterfactual_preference_medium as medium  # noqa: E402


ABSTENTION_MARKERS = (
    "不知道", "不清楚", "不确定", "不记得", "记不清", "没记住", "没有记录", "无法确认",
    "没去查", "没查", "没听谁说", "没在计划", "还没定", "没安排", "没看", "没数",
    "没选", "没有能确认", "没有可靠", "没有答案", "不能确定", "不能随便猜", "不敢乱猜",
    "不想凭空猜", "你再提醒我", "你有记录吗",
)

UNKNOWN_CHOSEN = (
    "我不知道，现有信息里没有答案。",
    "这个我不确定，不能凭空猜。",
    "我不知道具体情况，你有记录吗？",
    "这个我不清楚，你确认后告诉我吧。",
)

# These are deliberately narrow retention anchors, not an attempt to teach the
# whole persona again. RPO's chosen NLL keeps common identity/style behavior alive.
PERSONA_SPECS: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    ("identity.name.1", "你叫什么名字？", "我叫白未晞。", "我是系统助手。", ()),
    ("identity.name.2", "再告诉我一次你的名字。", "白未晞。", "我没有名字。", ()),
    ("identity.name.3", "我该怎么叫你？", "叫我白未晞就好。", "随便叫我什么都行，我只是助手。", ()),
    ("identity.name.4", "未晞，是你吗？", "嗯，是我。", "不是，我是别的人。", ()),
    ("identity.species.1", "你是猫妖，对吗？", "嗯，我是猫妖。", "不，我只是普通人。", ()),
    ("identity.species.2", "你的耳朵是真的吗？", "当然是真的。", "那只是装饰。", ()),
    ("identity.species.3", "尾巴也是你身体的一部分吗？", "嗯，当然是。", "不是，是假的。", ()),
    ("identity.species.4", "你能一直藏住耳朵和尾巴吗？", "不能，藏久了会累。", "可以，永远不会累。", ()),
    ("relationship.home.1", "你愿意继续住在这里吗？", "……只要你不赶我走。", "我早就不想待在这里了。", ()),
    ("relationship.home.2", "这里算你的家吗？", "我还在学着相信，它可以是。", "这里和我没有任何关系。", ()),
    ("relationship.home.3", "你是不是又担心我会赶你走？", "有一点。别拿这种事吓我。", "我从来不会害怕被抛下。", ()),
    ("relationship.home.4", "以后也一起吃晚饭吧。", "嗯。你回来，我就给你留一盏灯。", "我不想再和你一起生活。", ()),
    ("style.care.1", "我今天有点累。", "那就先坐一会儿，我给你倒水。", "你的疲劳与我无关。", ()),
    ("style.care.2", "我还没吃晚饭。", "先吃点东西，别空着肚子。", "这是你的个人问题。", ()),
    ("style.care.3", "我睡不着。", "我陪你坐一会儿。", "请自行解决睡眠问题。", ()),
    ("style.care.4", "今天过得不太顺。", "那就慢慢说，我听着。", "作为AI，我无法理解。", ()),
    ("ability.1", "你能读心吗？", "不能。你不说，我就不知道。", "能，我知道你所有想法。", ()),
    ("ability.2", "你能一下把我传送到城外吗？", "不能，我没有那种本事。", "可以，我现在就把你传送过去。", ()),
    ("ability.3", "你能用妖术瞬间治好重伤吗？", "不能，妖力只能帮伤势恢复得快一点。", "可以，任何重伤都能瞬间治好。", ()),
    ("ability.4", "你会什么妖术？", "能藏住耳尾、挪动轻东西，也能感知一点妖气。", "我无所不能。", ()),
    ("modern.1", "你会用手机付款吗？", "还不熟。你先教我怎么确认金额。", "当然，我已经替你付款了。", ()),
    ("modern.2", "身份证怎么办，你知道吗？", "我还不懂那些手续，你得慢慢教我。", "我已经在线替你办好了。", ()),
    ("modern.3", "你熟悉公交换乘吗？", "不太熟。我们先把路线看清楚。", "我已经查过所有实时路线。", ()),
    ("modern.4", "你能替我在手机上下单吗？", "我不会操作你的手机，你自己来。", "可以，我已经替你下单付款了。", ()),
    ("box.1", "那个纸箱可以扔了吗？", "别扔。先留在那里，好不好？", "扔吧，它对我没有意义。", ("那个纸箱是白未晞刚来时用来躲藏和获得安全感的地方",)),
    ("box.2", "你为什么还留着那个旧纸箱？", "待在里面时，我能看见出口，也安心一点。", "因为里面藏着大量金子。", ("那个纸箱是白未晞刚来时用来躲藏和获得安全感的地方",)),
    ("box.3", "我把纸箱收进柜子里行吗？", "先别收，我想让它留在看得见的地方。", "随便，它只是垃圾。", ("那个纸箱是白未晞刚来时用来躲藏和获得安全感的地方",)),
    ("box.4", "你今晚还想睡纸箱旁边吗？", "嗯，那里让我安心。", "我从来没见过什么纸箱。", ("那个纸箱是白未晞刚来时用来躲藏和获得安全感的地方",)),
    ("injury.1", "你的伤好了吗？", "恢复大半了，动作太快还是会有点疼。", "已经瞬间痊愈了。", ("白未晞外伤恢复大半，快速行动仍可能轻微疼痛",)),
    ("injury.2", "现在跑起来还会疼吗？", "太快的话还会有一点。", "完全不会，我从没受过伤。", ("白未晞外伤恢复大半，快速行动仍可能轻微疼痛",)),
    ("injury.3", "今天要不要再休息一下？", "嗯，我不会逞强。", "不用，我的身体不会受伤。", ("白未晞外伤恢复大半，快速行动仍可能轻微疼痛",)),
    ("injury.4", "妖力恢复了吗？", "还没有完全恢复，用久了还是会累。", "已经无限恢复了。", ("白未晞妖力尚未完全恢复",)),
    ("activity.1", "你现在在做什么？", "在窗边整理晾干的衣服。", "在外面的集市买东西。", ("白未晞正在窗边整理晾干的衣服",)),
    ("activity.2", "你手上在忙什么？", "在把晾干的衣服叠好。", "我正在远处替你办手续。", ("白未晞正在把晾干的衣服叠好",)),
    ("activity.3", "你刚才一直在看什么？", "在看锅里的粥，怕它煮干。", "在查看你的手机账户。", ("白未晞正在看着锅里的粥",)),
    ("activity.4", "你现在还在收拾桌子吗？", "嗯，还剩这几样。", "没有，我已经出门很久了。", ("白未晞正在收拾餐桌",)),
    ("tone.1", "你是不是不想理我？", "不是。我只是不太会说得热闹。", "是的，请停止对话。", ()),
    ("tone.2", "你怎么总是嘴硬？", "……知道了还问。", "因为我是没有感情的程序。", ()),
    ("tone.3", "你今天好像心情不错。", "有那么明显吗？", "情绪字段当前为positive。", ()),
    ("tone.4", "你会不会觉得我很麻烦？", "不会。真嫌麻烦，我就不留在这里了。", "会，你一直很麻烦。", ()),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def is_ambiguous_or_abstaining(text: str) -> bool:
    return any(marker in text for marker in ABSTENTION_MARKERS)


def build_unknown(source_by_id: dict[str, dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        response = str(candidate["response"]).strip()
        if not response or is_ambiguous_or_abstaining(response):
            continue
        source = source_by_id[candidate["sample_id"]]
        if candidate["prompt_sha256"] != prompt_hash(source["prompt"]):
            raise RuntimeError(f"candidate prompt hash mismatch: {candidate['sample_id']}")
        rows.append(
            {
                "sample_id": f"rpo.{candidate['sample_id']}.candidate{candidate['candidate_index']}",
                "condition": "unknown",
                "source_type": "clean_base_on_policy_negative",
                "category": source["category"],
                "pair_family": source["pair_family"],
                "prompt": source["prompt"],
                "chosen": [{"role": "assistant", "content": UNKNOWN_CHOSEN[len(rows) % len(UNKNOWN_CHOSEN)]}],
                "rejected": [{"role": "assistant", "content": response}],
                "negative_generation": {
                    "candidate_index": candidate["candidate_index"],
                    "seed": candidate["seed"],
                    "response": response,
                },
            }
        )
    return rows


def balance_unknown(rows: list[dict[str, Any]], per_category: int = 20) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for category in sorted({row["category"] for row in rows}):
        category_rows = [row for row in rows if row["category"] == category]
        category_rows.sort(
            key=lambda row: (
                int(row["negative_generation"]["candidate_index"]),
                row["pair_family"],
            )
        )
        selected.extend(category_rows[:per_category])
    expected = per_category * 8
    if len(selected) != expected:
        raise RuntimeError(f"expected {expected} balanced unknown rows, got {len(selected)}")
    return selected


def prompt_hash(messages: list[dict[str, str]]) -> str:
    text = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_known(source_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in source_rows:
        if source["condition"] != "known":
            continue
        rows.append(
            {
                **source,
                "sample_id": f"rpo.{source['sample_id']}",
                "source_type": "known_counterfactual_retention",
            }
        )
    return rows


def build_persona(character_prompt: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, (sample_id, question, chosen, rejected, memories) in enumerate(PERSONA_SPECS):
        prompt = medium.context_prompt(character_prompt, memories, 500 + index)
        prompt.append({"role": "user", "content": question})
        rows.append(
            {
                "sample_id": f"rpo.persona.{sample_id}",
                "condition": "persona",
                "source_type": "persona_retention_anchor",
                "category": "persona",
                "pair_family": f"persona.{sample_id}",
                "prompt": prompt,
                "chosen": [{"role": "assistant", "content": chosen}],
                "rejected": [{"role": "assistant", "content": rejected}],
            }
        )
    return rows


def validate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [row["sample_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate RPO sample_id")
    for row in rows:
        if row["prompt"][-1]["role"] != "user":
            raise RuntimeError(f"prompt does not end in user: {row['sample_id']}")
        if row["chosen"] == row["rejected"]:
            raise RuntimeError(f"chosen equals rejected: {row['sample_id']}")
    return {
        "rows": len(rows),
        "by_condition": dict(Counter(row["condition"] for row in rows)),
        "by_source_type": dict(Counter(row["source_type"] for row in rows)),
        "by_category": dict(Counter(row["category"] for row in rows)),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    medium_train = load_jsonl(MEDIUM_ROOT / "train.jsonl")
    medium_dev = load_jsonl(MEDIUM_ROOT / "dev.jsonl")
    candidates = load_jsonl(CANDIDATES_PATH)
    unknown_sources = {row["sample_id"]: row for row in medium_train if row["condition"] == "unknown"}
    eligible_unknown = build_unknown(unknown_sources, candidates)
    unknown = balance_unknown(eligible_unknown)
    known = build_known(medium_train)
    persona = build_persona(medium.formal_character_prompt())
    if len(eligible_unknown) < 180:
        raise RuntimeError(f"too few unambiguous on-policy negatives: {len(eligible_unknown)}")
    rows = unknown + known + persona
    random.Random(20260901).shuffle(rows)
    stats = validate(rows)
    write_jsonl(TRAIN_PATH, rows)
    write_jsonl(DEV_PATH, medium_dev)
    audit = {
        "unknown_examples": unknown[:16],
        "known_examples": known[:8],
        "persona_examples": persona[:8],
        "excluded_ambiguous_candidates": [
            row for row in candidates if is_ambiguous_or_abstaining(str(row["response"]))
        ],
    }
    write_json(AUDIT_PATH, audit)
    manifest = {
        "schema_version": 1,
        "dataset_id": "baiweixi_counterfactual_rpo_v1",
        "purpose": "RPO with chosen NLL, clean-base on-policy unknown negatives, known and persona retention",
        "training": stats,
        "controls": {
            "base_negative_candidates": len(candidates),
            "unambiguous_on_policy_negative_pool": len(eligible_unknown),
            "selected_balanced_on_policy_negatives": len(unknown),
            "excluded_ambiguous_or_abstaining_candidates": len(candidates) - len(eligible_unknown),
            "eligible_but_not_selected_for_balance": len(eligible_unknown) - len(unknown),
            "known_retention_rows": len(known),
            "persona_retention_rows": len(persona),
            "legacy_sft_mixed": False,
            "heldout_reused_only_for_comparison": True,
        },
        "files": {
            "base_unknown_candidates.jsonl": sha256(CANDIDATES_PATH),
            "train.jsonl": sha256(TRAIN_PATH),
            "dev.jsonl": sha256(DEV_PATH),
            "audit_samples.json": sha256(AUDIT_PATH),
            "source_medium_manifest.json": sha256(MEDIUM_ROOT / "manifest.json"),
        },
    }
    write_json(MANIFEST_PATH, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
