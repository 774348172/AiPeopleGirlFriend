# -*- coding: utf-8 -*-
"""T1 锚渲染校验工具：用生产渲染路径重渲染 system 锚并跑 G-A 验收检查。

用法:
  python tools/rerender_anchors.py                    # 只检查 2500 条锚（不写文件）
  python tools/rerender_anchors.py --out <path>       # 输出重锚后的 jsonl（不覆盖原文件）

用途（2026-08-06 T1）：
- 修复前：训练锚含 "你是 qinweixi"（profile_id 当角色名）、未解析 style: 引用、
  元注释（虚构写法）、重复事实、无【角色信念】。
- 修复后：display_name 渲染角色名、style 由 resolver 解析、secret 事实被
  _collect_facts 过滤、按值去重、beliefs 进锚。
- 本工具复用 gen_qin_v4.py 的生产 resolver，保证校验路径 == 导出路径。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gen_qin_v4 import PROFILES_ROOT, QWX_PACKAGE_SET, ROOT as GEN_ROOT  # noqa: E402
from data_gen_v4.adapters.modes.renderers import ProductionRenderers  # noqa: E402
from data_gen_v4.adapters.sources.registry import CompositeSourceLoader, FilePackageRegistry  # noqa: E402
from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory  # noqa: E402
from data_gen_v4.core.compiler import GenerationPlanCompiler  # noqa: E402
from data_gen_v4.core.plan import RunSpec  # noqa: E402

DEFAULT_IN = Path("训练数据/qin_v4_2500.jsonl")

FAIL_TERMS = ("你是 qinweixi", "style:", "虚构写法", "可配置", "地堡", "防空洞", "裂缝", "365", "常用词：常用词", "。。")
REQUIRE_TERMS = ("你是 秦未晞", "【角色信念】", "不要自称AI", "实时天气", "默认倾向，不要求每次回复都使用称呼")


def build_context():
    registry = FilePackageRegistry(PROFILES_ROOT)
    loader = CompositeSourceLoader(GEN_ROOT)
    factory = RecipeDrivenItemFactory(pools_path=str(PROFILES_ROOT / "qinweixi" / "pools.yaml"))
    compiler = GenerationPlanCompiler(registry, loader, factory)
    result = compiler.compile(RunSpec(run_id="anchor-check", seed=42), QWX_PACKAGE_SET)
    return result.context


def render_anchor(context: dict) -> str:
    # T2：锚 = 运行时散文式（profile + anchor_facts + beliefs + protocol 规则），无 style 段
    return ProductionRenderers.reply_system_anchor(
        context["profile"],
        anchor_facts=context.get("anchor_facts") or {},
        beliefs=str(context.get("beliefs", "")),
        rules=context.get("protocol", {}).get("reply_runtime_rules") or [],
    )


def check_anchor(anchor: str) -> list[str]:
    errors = [t for t in FAIL_TERMS if t in anchor]
    missing = [t for t in REQUIRE_TERMS if t not in anchor]
    return errors + [f"缺少 {t}" for t in missing]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default=str(DEFAULT_IN))
    ap.add_argument("--out", default=None, help="输出重锚后 jsonl（不覆盖原文件）")
    args = ap.parse_args()

    context = build_context()
    anchor = render_anchor(context)
    problems = check_anchor(anchor)
    if problems:
        print("[error] 新锚本身不合格:")
        for p in problems:
            print("  -", p)
        return 1

    rows = [json.loads(l) for l in open(args.in_path, encoding="utf-8")]
    n_ok = n_fail = 0
    for row in rows:
        convs = row.get("conversations", [])
        replaced = False
        for message in convs:
            if message.get("from") == "system":
                message["value"] = anchor
                replaced = True
                break
        if not replaced:
            convs.insert(0, {"from": "system", "value": anchor})
        bad = check_anchor(convs[0]["value"])
        if bad:
            n_fail += 1
        else:
            n_ok += 1
    print(f"锚检查: 通过 {n_ok} / 失败 {n_fail}（共 {len(rows)} 条）")
    if n_fail:
        return 1

    if args.out:
        out = Path(args.out)
        with out.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"重锚后文件 → {out}（原文件未动）")
    print("G-A 锚验收: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
