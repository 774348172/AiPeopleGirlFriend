"""生产 renderer 合同（《数据生成器v4设计》§10.5）。

- reply-runtime-v1：固定身份锚与输出协议 → 当前 epoch 历史 → 动态状态/RecallFrame
  → 当前输入；静态对话监督全部 assistant（runtime-grounded 只监督末条，阶段 5 接入）。
- recall-plan-v1 / memory-propose-v1：mode discriminator + grammar → 输入；只监督 JSON。

顺序与《AI女友最小心智系统设计》§11 的运行时 prompt 合同对齐：
训练数据中 system anchor 的位置/内容与生产推理一致，保证模型学到正确的 prompt 结构。
"""
from __future__ import annotations

from typing import Any

from .training import TrainingRecordV4, estimate_token_count

_OUTPUT_PROTOCOL_REPLY = (
    "不要自称AI，不要使用助手腔、Markdown、列表、emoji 或【】标签前缀。"
    "回复通常自然简短，需要解释时可以说完整。"
)

_PROTOCOL_DISCRIMINATORS = {
    "RECALL_PLAN": (
        "mode=RECALL_PLAN；输出符合 recall_plan_target 冻结 schema 的单个 JSON 对象："
        "只生成受约束检索计划（search/NO_OP），不生成回忆答案。"
    ),
    "MEMORY_PROPOSE": (
        "mode=MEMORY_PROPOSE；输出符合 memory_propose_target 冻结 schema 的单个 JSON 对象："
        "只提出带证据的派生候选（fact/preference/relationship/episode/correction/NO_OP），"
        "不含计划建立、取消、完成或错过。"
    ),
}


class ProductionRenderers:
    """三 mode 的生产渲染器（§10.5 renderer 合同）。"""

    @staticmethod
    def reply_system_anchor(
        profile: dict[str, Any],
        anchor_facts: dict[str, str] | None = None,
        beliefs: str = "",
        rules: list[str] | None = None,
    ) -> str:
        """固定身份锚（训练与推理共用的同一段文本，2026-08-06 T2 统一）。

        散文式结构，由数据渲染（不手写）：
        - 身份句：display_name + anchor_facts（age/occupation）；
        - 关系句：player_name（profile.anchor_contract）+ player_age/living/relationship；
        - nickname/personality/secret_boundary：profile.anchor_contract 声明的纹理；
        - 【角色信念】：compiler 蒸馏的三观短句；
        - 行为规则：protocol.reply_runtime_rules（产品通用）；
        - 输出协议：通用常量。

        规则：口癖不进锚（只从对话数据学）；秘密只出现边界句（secret_boundary），
        不出现秘密细节；风格合同不在此渲染（生成提示词仍用 style_contract）。
        """
        contract = profile.get("anchor_contract") or {}
        facts = anchor_facts or {}
        display_name = profile.get("display_name") or profile.get("profile_id", "角色")

        lines: list[str] = []
        identity_parts = [f"你是 {display_name}"]
        if facts.get("age"):
            identity_parts.append(f"{facts['age']}岁")
        if facts.get("occupation"):
            identity_parts.append(facts["occupation"])
        lines.append("，".join(identity_parts) + "。")

        if contract.get("player_name") and facts.get("player_age"):
            relation_parts = [f"玩家叫{contract['player_name']}，{facts['player_age']}岁"]
            if facts.get("living"):
                relation_parts.append(facts["living"])
            if facts.get("relationship"):
                relation_parts.append(facts["relationship"])
            lines.append("；".join(relation_parts) + "。")

        if contract.get("nickname_rule"):
            lines.append(contract["nickname_rule"])
        if contract.get("personality"):
            lines.append(contract["personality"])
        for key in (
            "interaction_boundary", "cohabitation", "appearance_and_habits",
            "relationship_dynamics", "modern_knowledge_gap", "capability_limits",
        ):
            if contract.get(key):
                lines.append(str(contract[key]))
        for key, label in (
            ("species", "身份"), ("human_appearance", "外貌"),
            ("current_injury", "当前状态"), ("current_magic", "妖力状态"),
            ("leaving_statement", "关系张力"),
        ):
            if facts.get(key):
                lines.append(f"{label}：{facts[key]}。")
        if beliefs:
            lines.append("【角色信念】" + beliefs.replace("\n", "；"))
        for rule in rules or []:
            lines.append(rule)
        if contract.get("secret_boundary"):
            lines.append(contract["secret_boundary"])
        lines.append(_OUTPUT_PROTOCOL_REPLY)
        return "\n".join(lines)

    @staticmethod
    def reply_training_record(
        candidate: dict[str, Any],
        package_set: dict[str, Any],
        *,
        render_profile_id: str = "reply-runtime-v1",
    ) -> TrainingRecordV4:
        """静态对话监督全部 assistant；system anchor 为首条（与推理 prompt 同序）。

        anchor 由 profile + anchor_facts + beliefs + protocol 规则渲染（T2 单一事实源）。
        """
        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in candidate["target"]["messages"]
        ]
        supervised = [i for i, m in enumerate(messages) if m["role"] == "assistant"]
        profile = package_set.get("profile", {})
        protocol = package_set.get("protocol", {})
        anchor = ProductionRenderers.reply_system_anchor(
            profile,
            anchor_facts=package_set.get("anchor_facts") or {},
            beliefs=str(package_set.get("beliefs", "")),
            rules=protocol.get("reply_runtime_rules") or [],
        )
        return TrainingRecordV4(
            sample_id=candidate["sample_id"],
            mode="REPLY",
            render_profile_id=render_profile_id,
            messages=messages,
            supervised_message_indexes=supervised,
            supervised_token_count=estimate_token_count(messages, supervised),
            protocol_snapshot_id=str(package_set.get("protocol", {}).get("package_version", "v1")),
            system_anchor=anchor,
        )

    @staticmethod
    def protocol_system_anchor(mode: str) -> str:
        """mode discriminator + grammar 约束（协议 renderer 第 1 段）。"""
        return _PROTOCOL_DISCRIMINATORS.get(
            mode, f"mode={mode}；输出符合冻结 schema 的单个 JSON 对象。"
        )
