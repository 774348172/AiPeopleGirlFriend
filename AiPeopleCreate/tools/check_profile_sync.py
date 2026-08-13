"""【已退役】正典 ↔ V4 sources 同步校验（防止双份漂移）。

大块 A（2026-08-07）：ProfilePackage 直接编译唯一权威正典（人物设定/秦/），
profiles/qinweixi/sources 生产副本已归档至 _archive_sources_20260807/，
双份同步机制与标注流程（annotate_qinweixi_sources.py）一并消灭。
本工具仅作历史对照，不再需要运行。
"""

用法:
  python tools/check_profile_sync.py            # 只检查
  python tools/check_profile_sync.py --sync     # 用 人物设定/秦/ 覆盖 profiles/qinweixi/sources/

注意：--sync 覆盖后会丢失 sources 中的 id/visibility 标注，需要重跑标注脚本。
因此建议流程：改正典 → --sync → 重跑标注（timeline id + canon visibility）。
"""
from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CANON_DIR = ROOT / "人物设定" / "秦"  # 正典唯一物理副本（2026-08-07 迁移；AI 程序侧为 junction）
SOURCES_DIR = ROOT / "profiles" / "qinweixi" / "sources"

FILES = ["bible.yaml", "canon.json", "timeline.yaml"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", action="store_true", help="用正典覆盖 sources")
    args = ap.parse_args()

    missing = []
    for name in FILES:
        src, dst = CANON_DIR / name, SOURCES_DIR / name
        if not src.exists():
            missing.append(str(src))
            continue
        if args.sync:
            shutil.copy2(src, dst)
            print(f"[sync] {name} <- 人物设定/秦/{name}")
            print(f"[sync] ⚠️ 覆盖后需重跑: python tools/annotate_qinweixi_sources.py")
        elif not filecmp.cmp(src, dst, shallow=False):
            # 允许的差异：sources 侧有 id/visibility 标注；正文内容不同才算真漂移
            print(f"[info] {name}: 与正典存在标注差异（预期）——若正文有改动请 --sync + 重标注")

    if missing:
        print(f"[error] 正典缺失: {missing}")
        return 1
    if not args.sync:
        # 标注完整性检查（只读）
        t = yaml.safe_load((SOURCES_DIR / "timeline.yaml").read_text(encoding="utf-8"))
        no_id = [e for e in t["events"] if not e.get("id")]
        dup = len({e.get("id") for e in t["events"]}) != len(t["events"])
        c = json.loads((SOURCES_DIR / "canon.json").read_text(encoding="utf-8"))
        no_vis = [k for k, v in c["facts"].items() if "visibility" not in v]
        print(f"[check] timeline id 缺失: {len(no_id)}, 重复: {dup}")
        print(f"[check] canon 无 visibility 标注: {len(no_vis)} 条")
        if no_id or dup or no_vis:
            return 1
        print("[check] ✅ sources 标注完整")
    return 0


if __name__ == "__main__":
    sys.exit(main())
