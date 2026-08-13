"""Claim 提取与关联（阶段 3 C-1：claim-level validation）。

教师自报命题（assistant_propositions）注册为结构化 claim；support_spans 的
supported_claim_id 重写为真实 claim_id（替换编译期合成串 `fam:{id}:{task}:{i}`）——
G2 据此校验 claim 与 span/正文的一致性（"自报 used_facts 与正文不一致必败"）。

词法蕴含复用 reply 的 _check_grounding（不合格命题不注册为 claim，已触发重试）；
本模块只做结构化注册与关联。
"""
from __future__ import annotations

from typing import Any


def build_claims(
    item: dict[str, Any],
    propositions: list[str],
    spans: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把命题注册为 claims，并把 spans 的 supported_claim_id 重写为真实 claim_id。

    返回 (claims, spans')。claim_id = claim:{plan_id}:{attempt}:{candidate_no}:{i}；
    多条 span 可支撑同一 claim（span[j] 关联 claims[min(j, len-1)]），claim 的
    used_facts 汇集其全部支撑 source_id。
    """
    plan_id = str(item.get("plan_id", "?"))
    attempt = item.get("attempt_no", 1)
    candidate_no = item.get("candidate_no", 1)
    claims: list[dict[str, Any]] = []
    for i, text in enumerate(propositions):
        claims.append(
            {
                "claim_id": f"claim:{plan_id}:{attempt}:{candidate_no}:{i}",
                "text": str(text),
                "used_facts": [],
                "evidence_role": "support",
            }
        )
    spans_out: list[dict[str, Any]] = []
    for j, span in enumerate(spans):
        span = dict(span)
        if claims:
            target = claims[min(j, len(claims) - 1)]
            span["supported_claim_id"] = target["claim_id"]
            target["used_facts"].append(str(span.get("source_id", "")))
        spans_out.append(span)
    return claims, spans_out


def claim_text_present(candidate: dict[str, Any], claim_text: str) -> bool:
    """claim 文本是否出现在候选正文（assistant 台词）——子串或任意 3 字片段。

    与 reply 语义保持检查同口径：完全子串，或 3 字片段命中（允许自然嵌入）。
    """
    assistant_text = " ".join(
        str(m.get("content", ""))
        for m in (candidate.get("target") or {}).get("messages", [])
        if m.get("role") == "assistant"
    )
    if claim_text in assistant_text:
        return True
    if len(claim_text) >= 3:
        for i in range(len(claim_text) - 2):
            if claim_text[i : i + 3] in assistant_text:
                return True
    return False
