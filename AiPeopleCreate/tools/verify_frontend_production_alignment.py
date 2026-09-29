from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


BLOCKS = (
    "[角色锚]", "[当前权威事实]", "[本轮玩家事实报告]", "[明确 unknown]",
    "[当前世界]", "[你此刻的状态]", "[进行中的游戏动作]", "[相关记忆]",
    "[第三方对话证据]", "[允许表达的动作]", "[回答依据约束]",
)


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    sys.path.insert(0, str(repo / "AiPeople"))
    sys.path.insert(0, str(repo / "AiPeopleCreate"))
    from runtime.world_mind import CharacterPackagePromptComposer
    from runtime.world_mind import game_reply_context as runtime_renderer
    from tests.world_mind._helpers import runtime_config, session
    from data_gen_v4.runtime_grounded import renderer as training_renderer
    from tools.build_v5_pilot_dataset import _load_baiweixi_render_context

    production_prompt = CharacterPackagePromptComposer(runtime_config()).compose(session()).game_reply_system_prompt
    generator_prompt = _load_baiweixi_render_context()[2]
    rows = []
    for split in ("train", "dev", "test"):
        rows.extend(read_jsonl(args.package / f"{split}.accepted.jsonl"))
    prefixes, block_failures, role_failures = set(), [], []
    for row in rows:
        system = row["messages"][0]["content"]
        marker = "\n\n[角色锚]"
        prefixes.add(system.split(marker, 1)[0] if marker in system else system)
        positions = [system.find(block) for block in BLOCKS]
        if any(pos < 0 for pos in positions) or positions != sorted(positions):
            block_failures.append(row["sample_id"])
        if (row.get("render_profile_id") != "GAME_REPLY_CONTEXT_V1" or
                row["supervised_message_indexes"] != [len(row["messages"]) - 1] or
                row["messages"][-1]["role"] != "assistant" or row["messages"][-2]["role"] != "user"):
            role_failures.append(row["sample_id"])
    prefix_records = [{"sha256": sha_text(value), "length": len(value), "text": value} for value in sorted(prefixes)]
    prompt_equal = len(prefixes) == 1 and next(iter(prefixes)) == production_prompt
    shared_identity = training_renderer.render_game_reply_context_v1 is runtime_renderer.render_game_reply_context_v1
    generator_prompt_equal = generator_prompt == production_prompt
    passed = (
        shared_identity and generator_prompt_equal and not block_failures
        and not role_failures and prompt_equal
    )
    report = {
        "schema_version": "aip.frontend_production_alignment.v1",
        "source_package": str(args.package.resolve()),
        "accepted_rows_checked": len(rows),
        "checks": {
            "shared_renderer_function_identity": shared_identity,
            "generator_anchor_equals_current_production_prompt": generator_prompt_equal,
            "all_required_blocks_present_in_order": not block_failures,
            "all_message_boundaries_valid": not role_failures,
            "training_role_anchor_equals_current_production_prompt": prompt_equal,
        },
        "production_prompt": {"sha256": sha_text(production_prompt), "length": len(production_prompt), "text": production_prompt},
        "generator_prompt": {"sha256": sha_text(generator_prompt), "length": len(generator_prompt), "text": generator_prompt},
        "training_prompt_prefixes": prefix_records,
        "block_failures": block_failures,
        "role_failures": role_failures,
        "production_input_alignment_verified": passed,
        "training_authorized": False,
        "blocking_reason": None if passed else (
            "训练数据角色锚与当前生产动态角色Prompt不一致；共享上下文渲染函数本身不足以证明端到端输入同构。"
            if not prompt_equal else "上下文区块、消息边界或共享渲染器检查失败。"
        ),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "alignment_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = [
        "# GAME_REPLY_CONTEXT_V1 训练与生产输入对齐报告", "", f"结论：{'通过' if passed else '不通过'}。", "",
        f"检查 accepted 数据：{len(rows)} 条。", "", "## 检查结果", "",
        f"- 生产与生成器引用同一个渲染函数对象：{shared_identity}",
        f"- 生成器当前角色锚与生产角色 Prompt 完全一致：{generator_prompt_equal}",
        f"- 必需上下文区块完整且顺序一致：{not block_failures}",
        f"- 消息边界与最终监督位置正确：{not role_failures}",
        f"- 训练角色锚与当前生产角色 Prompt 完全一致：{prompt_equal}", "",
        "## 阻断原因", "", report["blocking_reason"] or "无。", "",
        f"生产 Prompt SHA256：`{sha_text(production_prompt)}`。",
        *(f"训练角色锚 SHA256：`{item['sha256']}`。" for item in prefix_records), "",
        "当前保持 `training_authorized=false`。不得仅因 render_profile_id 相同而授权训练。", "",
    ]
    (args.output / "alignment_report.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"passed": passed, "checks": report["checks"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
