# -*- coding: utf-8 -*-
"""T2 锚一致性守卫的同步工具：用生成器生产路径重渲染 system 锚，
更新 AI 程序侧运行时快照 runtime/_prompt.py 的角色锚常量。

用途（2026-08-07，大块 A 协议/ProfilePackage 变更后；2026-08-08 多角色接线）：
- tests/test_reply_prompt.py 守卫断言"生成器渲染锚 == 运行时快照"；
- 协议包/ProfilePackage 变更导致锚文本变化时，先跑本工具再跑守卫测试；
- 新增角色：--profile <id> + --const-name <常量名>（运行时侧需先存在同名常量）。

用法:
  python tools/sync_runtime_anchor.py             # 秦未晞：渲染 + 更新 runtime/_prompt.py
  python tools/sync_runtime_anchor.py --check     # 只对比不写文件
  python tools/sync_runtime_anchor.py --profile baiweixi --const-name BAIWEIXI_REPLY_SYSTEM

前置：本仓库 人物设定/ 为唯一权威正典；F:\\AiPeople 侧存在运行时（生成器只读引用，
快照更新为本工具唯一写入口）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gen_v4 import PROFILES_ROOT, ROOT as GEN_ROOT, build_package_set  # noqa: E402
from data_gen_v4.adapters.modes.renderers import ProductionRenderers  # noqa: E402
from data_gen_v4.adapters.sources.registry import CompositeSourceLoader, FilePackageRegistry  # noqa: E402
from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory  # noqa: E402
from data_gen_v4.core.compiler import GenerationPlanCompiler  # noqa: E402
from data_gen_v4.core.plan import RunSpec  # noqa: E402

PROMPT_PATH = Path(r"F:\AiPeople\runtime\_prompt.py")
DEFAULT_PROFILE = "qinweixi"
DEFAULT_CONST = "QIN_WEIXI_REPLY_SYSTEM"


def render_anchor(profile_id: str) -> str:
    registry = FilePackageRegistry(PROFILES_ROOT)
    loader = CompositeSourceLoader(GEN_ROOT)
    pools_path = PROFILES_ROOT / profile_id / "pools.yaml"
    factory = RecipeDrivenItemFactory(
        pools_path=str(pools_path) if pools_path.exists() else None
    )
    compiler = GenerationPlanCompiler(registry, loader, factory)
    context = compiler.compile(
        RunSpec(run_id=f"anchor-sync-{profile_id}", seed=42),
        build_package_set(PROFILES_ROOT, profile_id),
    ).context
    return ProductionRenderers.reply_system_anchor(
        context["profile"],
        anchor_facts=context["anchor_facts"],
        beliefs=str(context["beliefs"]),
        rules=context["protocol"].get("reply_runtime_rules") or [],
    )


def replace_constant(text: str, value: str, const_name: str) -> str:
    """替换 <CONST_NAME> = 三引号字符串块（保留文件其余部分）。"""
    marker = f"{const_name} = "
    start = text.index(marker) + len(marker)
    assert text[start] == '"', "快照不是三引号字符串"
    end = text.index('"""', start + 3) + 3
    return text[:start] + f'"""{value}"""' + text[end:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只对比不写文件")
    parser.add_argument("--profile", default=DEFAULT_PROFILE,
                        help=f"角色包 id（默认 {DEFAULT_PROFILE}）")
    parser.add_argument("--const-name", default=DEFAULT_CONST,
                        help=f"运行时快照常量名（默认 {DEFAULT_CONST}；多角色时传角色常量名）")
    args = parser.parse_args()

    if not PROMPT_PATH.exists():
        print(f"运行时快照不存在: {PROMPT_PATH}")
        return 1
    rendered = render_anchor(args.profile)
    text = PROMPT_PATH.read_text(encoding="utf-8")
    if f"{args.const_name} = " not in text:
        print(f"运行时快照缺少常量 {args.const_name}（多角色需先在 runtime/_prompt.py 增加）")
        return 1
    updated = replace_constant(text, rendered, args.const_name)
    if updated == text:
        print(f"[{args.profile}] 锚一致，无需更新")
        return 0
    if args.check:
        print(f"[{args.profile}] 锚不一致（--check 模式，未写文件）")
        return 1
    PROMPT_PATH.write_text(updated, encoding="utf-8")
    print(f"[{args.profile}] 锚已更新: {PROMPT_PATH}（{len(rendered)} 字符）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
