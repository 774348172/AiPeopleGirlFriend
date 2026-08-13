"""【已退役】秦未晞 V4 sources 标注：timeline 稳定 id + canon/bible visibility 分级。

大块 A（2026-08-07）：标注已固化进唯一权威正典（人物设定/秦/），
本工具随生产副本归档一并退役，仅作历史对照。

流程（改正典后）：
  1. python tools/check_profile_sync.py --sync    # 正典 → sources
  2. python tools/annotate_qinweixi_sources.py     # 补 id + 秘密标注
  3. python tools/check_profile_sync.py            # 校验标注完整

用法: python tools/annotate_qinweixi_sources.py
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "profiles" / "qinweixi" / "sources"

# timeline date(含重复消歧) → 稳定 id
TIMELINE_IDS = {
    "0岁": "ev:childhood_birth",
    "3岁": "ev:childhood_lollipop",
    "5岁:马尾": "ev:childhood_ponytail",
    "5岁:搬走": "ev:childhood_move_away",
    "5-15岁": "ev:separated_decade",
    "15岁": "ev:hs_reunion",
    "15-18岁": "ev:hs_three_years",
    "16岁": "ev:hs_protect",
    "16岁-同桌": "ev:hs_deskmate",
    "17岁-运动会": "ev:hs_sports_day",
    "17岁-晚自习": "ev:hs_evening_study",
    "18岁-毕业": "ev:hs_graduation",
    "18岁": "ev:ow_enter",
    "18岁-春": "ev:ow_spring",
    "18岁-入冬": "ev:ow_winter_onset",
    "18岁-冬": "ev:ow_winter_bunker",
    "18岁-冬-守夜": "ev:ow_winter_watch",
    "18岁-冬-发烧": "ev:ow_winter_fever",
    "18岁-冬-聊天": "ev:ow_winter_talk",
    "18岁-末": "ev:ow_final_days",
    "18岁-过关": "ev:ow_return",
    "18-22岁": "ev:sep_four_years",
    "19岁-动态": "ev:uni_socials",
    "20岁-电话": "ev:uni_first_call",
    "21岁-毕业": "ev:uni_graduate",
    "22岁": "ev:cohabit_start",
    "22岁-第一个周末": "ev:cohabit_first_weekend",
    "现在": "ev:cohabit_now",
}

# canon 事实 → 秘密（withhold）
SECRET_KEYS = [
    "other_world_age", "other_world_duration", "other_world_time_flow",
    "other_world_mechanism", "other_world_entrance", "other_world_return",
    "other_world_amnesia_cause", "other_world_she_knows", "other_world_alone",
    "other_world_crack_now", "other_world_return_day", "other_world_relationship",
    "other_world_you_forgot", "other_world_she_remembers", "separated_duration",
]
# canon 事实 → 私密（hint_only）
PRIVATE_KEYS = [
    "player_feelings", "player_memory_fragments", "current_relationship",
    "nickname_usage", "cohabit_duration", "psyche_aftermath", "finance",
    "player_age", "birthday",
]


def annotate_timeline(path: Path) -> int:
    t = yaml.safe_load(path.read_text(encoding="utf-8"))
    n_secret = 0
    for e in t["events"]:
        d = e["date"]
        key = d if d != "5岁" else ("5岁:搬走" if ("搬走" in e["summary"] or "项目调动" in e["summary"]) else "5岁:马尾")
        e["id"] = TIMELINE_IDS.get(key, f"ev:unknown_{d}")
        if d.startswith("18岁") or "异世界" in str(e.get("tags", [])):
            e["visibility"], e["disclosure_policy"] = "profile_secret", "withhold"
            n_secret += 1
        elif "玩家" in str(e.get("people", [])) and e.get("importance", 0) >= 7:
            e["visibility"], e["disclosure_policy"] = "profile_private", "hint_only"
        else:
            e["visibility"], e["disclosure_policy"] = "profile_public", "direct_allowed"
    yaml.safe_dump(t, path.open("w", encoding="utf-8"), allow_unicode=True, sort_keys=False)
    return n_secret


def annotate_canon(path: Path) -> tuple[int, int, int]:
    c = json.loads(path.read_text(encoding="utf-8"))
    n_sec = n_pri = n_pub = 0
    for k, v in c["facts"].items():
        if k in SECRET_KEYS:
            v["visibility"], v["disclosure_policy"] = "profile_secret", "withhold"
            n_sec += 1
        elif k in PRIVATE_KEYS:
            v["visibility"], v["disclosure_policy"] = "profile_private", "hint_only"
            n_pri += 1
        else:
            v["visibility"], v["disclosure_policy"] = "profile_public", "direct_allowed"
            n_pub += 1
    path.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    return n_sec, n_pri, n_pub


def main() -> None:
    n_secret = annotate_timeline(SOURCES / "timeline.yaml")
    n_sec, n_pri, n_pub = annotate_canon(SOURCES / "canon.json")
    print(f"✅ timeline: {n_secret} 秘密事件")
    print(f"✅ canon: secret={n_sec} private={n_pri} public={n_pub}")
    print("下一步: python tools/check_profile_sync.py 校验")


if __name__ == "__main__":
    main()
