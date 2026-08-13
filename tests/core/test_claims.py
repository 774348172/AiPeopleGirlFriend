"""阶段 3 C-1（claim-level validation）验收。

- 命题注册为结构化 claim，spans 的 supported_claim_id 重写为真实 claim_id；
- G2 claim 级校验：span 指向不存在的 claim → claim_not_found 拒绝；
  自报 claim 文本不在正文 → claim_missing_in_text 拒绝（"自报 used_facts 与
  正文不一致必败"）；正常关联 → 通过。
"""
from __future__ import annotations

from data_gen_v4.core.claims import build_claims, claim_text_present
from data_gen_v4.core.gates import G2SourceSpanGate
from tests.core.test_gates import _candidate_dict, _snapshots, _span


def test_build_claims_rewrites_span_links():
    item = {"plan_id": "plan-x:0000", "attempt_no": 2, "candidate_no": 3}
    spans = [
        {"source_id": "identity:age", "supported_claim_id": "old-synthetic-0"},
        {"source_id": "facts:living", "supported_claim_id": "old-synthetic-1"},
    ]
    claims, spans_out = build_claims(item, ["我今年22岁", "我们住在一起"], spans)
    assert [c["claim_id"] for c in claims] == [
        "claim:plan-x:0000:2:3:0",
        "claim:plan-x:0000:2:3:1",
    ]
    assert spans_out[0]["supported_claim_id"] == claims[0]["claim_id"]
    assert claims[0]["used_facts"] == ["identity:age"]
    assert claims[1]["used_facts"] == ["facts:living"]


def test_build_claims_fewer_propositions_shares_last_claim():
    item = {"plan_id": "plan-x", "attempt_no": 1, "candidate_no": 1}
    spans = [{"source_id": "a"}, {"source_id": "b"}, {"source_id": "c"}]
    claims, spans_out = build_claims(item, ["只有一条命题"], spans)
    assert len(claims) == 1
    assert all(s["supported_claim_id"] == claims[0]["claim_id"] for s in spans_out)
    assert claims[0]["used_facts"] == ["a", "b", "c"]


def test_claim_text_present_substring_and_trigram():
    candidate = {
        "target": {
            "messages": [
                {"role": "human", "content": "h"},
                {"role": "assistant", "content": "我今年22岁，怎么了？"},
            ]
        }
    }
    assert claim_text_present(candidate, "我今年22岁") is True
    # "我今年21岁" 含 3 字片段"我今年"（正文存在）→ trigram 语义允许（自然嵌入）
    assert claim_text_present(candidate, "我今年21岁") is True
    assert claim_text_present(candidate, "我去年21岁") is False  # 无子串/无片段


def test_g2_rejects_claim_not_found():
    candidate = _candidate_dict()
    candidate["evidence_state"] = "supported"
    candidate["source_refs"] = [{"source_kind": "timeline_event", "source_id": "alpha:timeline:001",
                                 "snapshot_id": "snap:alpha:timeline", "field_pointer": ""}]
    candidate["support_spans"] = [_span(source_id="alpha:timeline:001", quote="五岁那年搬过三次家。")]
    candidate["claims"] = [{"claim_id": "claim:other", "text": "无关", "used_facts": []}]
    result = G2SourceSpanGate().evaluate(candidate, {"snapshots": _snapshots()})
    assert result.decision == "rejected"
    assert any("claim_not_found" in c for c in result.reason_codes)


def test_g2_rejects_claim_missing_in_text():
    candidate = _candidate_dict()
    candidate["evidence_state"] = "supported"
    candidate["source_refs"] = [{"source_kind": "timeline_event", "source_id": "alpha:timeline:001",
                                 "snapshot_id": "snap:alpha:timeline", "field_pointer": ""}]
    span = _span(source_id="alpha:timeline:001", quote="五岁那年搬过三次家。")
    candidate["support_spans"] = [span]
    # claim 文本与正文不一致（正文没有该命题）
    candidate["claims"] = [{
        "claim_id": span["supported_claim_id"], "text": "我其实五岁没搬过家",
        "used_facts": ["x"],
    }]
    result = G2SourceSpanGate().evaluate(candidate, {"snapshots": _snapshots()})
    assert result.decision == "rejected"
    assert any("claim_missing_in_text" in c for c in result.reason_codes)


def test_g2_approves_consistent_claim():
    candidate = _candidate_dict()
    candidate["evidence_state"] = "supported"
    candidate["source_refs"] = [{"source_kind": "timeline_event", "source_id": "alpha:timeline:001",
                                 "snapshot_id": "snap:alpha:timeline", "field_pointer": ""}]
    span = _span(source_id="alpha:timeline:001", quote="五岁那年搬过三次家。")
    candidate["support_spans"] = [span]
    candidate["claims"] = [{
        "claim_id": span["supported_claim_id"],
        "text": "五岁那年搬过三次家。",  # 与正文一致（assistant 台词含该句）
        "used_facts": ["x"],
    }]
    # 正文须含 claim 文本（assistant 台词）
    candidate["target"] = {
        "messages": [
            {"role": "human", "content": "h"},
            {"role": "assistant", "content": "五岁那年搬过三次家。真的。"},
        ]
    }
    result = G2SourceSpanGate().evaluate(candidate, {"snapshots": _snapshots()})
    assert result.decision == "approved"
