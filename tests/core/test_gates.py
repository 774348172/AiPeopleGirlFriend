"""core gates：G0-G4 确定性实现与注入式 G5/G8、物化规则（阶段 1：G3/G4 真实化）。"""
from __future__ import annotations

import pytest

from data_gen_v4.core.gates import (
    G0RecordIntegrityGate,
    G1PackageSnapshotGate,
    G2SourceSpanGate,
    G3ProfileAnchorGate,
    G4ProtocolRegistryGate,
    G6StructuralGate,
    G7HumanReviewGate,
    ReleaseQualityGate,
    accepted,
)
from tests.core.test_records import _candidate


def _candidate_dict() -> dict:
    return _candidate().to_dict()


def _lock() -> dict:
    return {
        "lock_hash": "sha256:" + "a" * 64,
        "packages": {
            "protocol:fixture:rel": {"package_version": "1.0.0", "content_hash": "h", "status": "approved"},
            "recipe:fixture:basic": {"package_version": "1.0.0", "content_hash": "h", "status": "approved"},
        },
        "source_snapshots": {
            "snap:alpha:identity": {"source_adapter_id": "structured-file", "content_hash": "h"},
        },
    }


def _snapshots() -> dict:
    from tests.core.fixtures import alpha_snapshots

    # G2 的 snapshots context 按 snapshot_id 索引（span.snapshot_id 引用）
    return {snap["snapshot_id"]: snap for snap in alpha_snapshots().values()}


def _span(source_id: str = "alpha:timeline:001", quote: str = "五岁那年搬过三次家。",
          start: int = 0, end: int | None = None, snapshot_id: str = "snap:alpha:timeline") -> dict:
    return {
        "source_kind": "timeline_event",
        "source_id": source_id,
        "snapshot_id": snapshot_id,
        "field_pointer": "",
        "offset_unit": "unicode_code_point",
        "start": start,
        "end": len(quote) if end is None else end,
        "quote": quote,
        "supported_claim_id": "claim:1",
        "evidence_role": "support",
    }


# ───────────────────────── G0 ─────────────────────────

def test_g0_approves_valid_candidate():
    result = G0RecordIntegrityGate().evaluate(_candidate_dict(), {})
    assert result.decision == "approved"


def test_g0_rejects_invalid_candidate():
    data = _candidate_dict()
    del data["target"]
    result = G0RecordIntegrityGate().evaluate(data, {})
    assert result.decision == "rejected"


# ───────────────────────── G1 ─────────────────────────

def test_g1_approves_matching_lock():
    result = G1PackageSnapshotGate().evaluate(_candidate_dict(), {"lock": _lock()})
    assert result.decision == "approved"


def test_g1_rejects_lock_hash_mismatch():
    lock = _lock()
    lock["lock_hash"] = "sha256:" + "f" * 64
    result = G1PackageSnapshotGate().evaluate(_candidate_dict(), {"lock": lock})
    assert result.decision == "rejected"
    assert "lock_hash_mismatch" in result.reason_codes


def test_g1_rejects_missing_lock():
    result = G1PackageSnapshotGate().evaluate(_candidate_dict(), {})
    assert result.decision == "rejected"


# ───────────────────────── G2 ─────────────────────────

def test_g2_approves_matching_span():
    candidate = _candidate_dict()
    candidate["support_spans"] = [_span()]
    result = G2SourceSpanGate().evaluate(candidate, {"snapshots": _snapshots()})
    assert result.decision == "approved"


def test_g2_rejects_quote_mismatch():
    candidate = _candidate_dict()
    candidate["support_spans"] = [_span(quote="完全不同的内容")]
    result = G2SourceSpanGate().evaluate(candidate, {"snapshots": _snapshots()})
    assert result.decision == "rejected"
    assert "span_quote_mismatch" in result.reason_codes


def test_g2_rejects_out_of_range():
    candidate = _candidate_dict()
    candidate["support_spans"] = [_span(start=0, end=999)]
    result = G2SourceSpanGate().evaluate(candidate, {"snapshots": _snapshots()})
    assert result.decision == "rejected"


def test_g2_rejects_missing_snapshot():
    candidate = _candidate_dict()
    candidate["support_spans"] = [_span(snapshot_id="snap:ghost")]
    result = G2SourceSpanGate().evaluate(candidate, {"snapshots": _snapshots()})
    assert result.decision == "rejected"
    assert any("missing_snapshot" in c for c in result.reason_codes)


def test_g2_handles_multibyte_code_points():
    # 中文 + emoji 混排：Python str 切片即 Unicode code point，[start,end) 必须一致
    text = "猫叫豆包。🎨"
    from tests.core.fixtures import make_snapshot, _unit

    snapshots = {
        "snap:multi": make_snapshot(
            "snap:multi", "structured-file",
            [_unit("canon_fact", "multi:001", "fixture.alpha", "snap:multi", text)],
        )
    }
    quote = "猫叫豆包。"
    candidate = _candidate_dict()
    candidate["support_spans"] = [
        {
            "source_kind": "canon_fact", "source_id": "multi:001",
            "snapshot_id": "snap:multi", "field_pointer": "",
            "offset_unit": "unicode_code_point",
            "start": 0, "end": len(quote), "quote": quote,
            "supported_claim_id": "c1", "evidence_role": "support",
        }
    ]
    result = G2SourceSpanGate().evaluate(candidate, {"snapshots": snapshots})
    assert result.decision == "approved"


def test_g2_approves_empty_spans():
    # not_required（安静陪伴等）空证据 → 装饰性允许（P0-2 语义：只强制需要证据的任务）
    result = G2SourceSpanGate().evaluate(_candidate_dict(), {"snapshots": {}})
    assert result.decision == "approved"


# ───────────────────────── G2 evidence 强制（块 1.2，P0-2 验收） ─────────────────────────

def _evidence_candidate() -> dict:
    candidate = _candidate_dict()
    candidate["evidence_state"] = "supported"
    return candidate


def test_g2_rejects_evidence_missing_source_refs():
    candidate = _evidence_candidate()
    candidate["support_spans"] = [_span()]
    result = G2SourceSpanGate().evaluate(candidate, {"snapshots": _snapshots()})
    assert result.decision == "rejected"
    assert "evidence_missing_source_refs" in result.reason_codes


def test_g2_rejects_evidence_missing_spans():
    candidate = _evidence_candidate()
    candidate["source_refs"] = [_ref()]
    result = G2SourceSpanGate().evaluate(candidate, {"snapshots": _snapshots()})
    assert result.decision == "rejected"
    assert "evidence_missing_spans" in result.reason_codes


def test_g2_approves_evidence_with_valid_spans():
    candidate = _evidence_candidate()
    candidate["source_refs"] = [_ref()]
    candidate["support_spans"] = [_span()]
    result = G2SourceSpanGate().evaluate(candidate, {"snapshots": _snapshots()})
    assert result.decision == "approved"


# ───────────────────────── G6 ─────────────────────────

def _reply_messages(count: int) -> dict:
    messages = []
    for i in range(count):
        role = "human" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"消息{i}"})
    return {"target": {"messages": messages}}


def test_g6_approves_alternating_dialogue():
    candidate = _candidate_dict()
    candidate["target"] = _reply_messages(6)["target"]
    result = G6StructuralGate().evaluate(candidate, {"turn_bounds": (4, 8)})
    assert result.decision == "approved"


def test_g6_rejects_out_of_bounds_turns():
    candidate = _candidate_dict()
    candidate["target"] = _reply_messages(9)["target"]
    result = G6StructuralGate().evaluate(candidate, {"turn_bounds": (4, 8)})
    assert result.decision == "rejected"


def test_g6_rejects_first_message_not_human():
    candidate = _candidate_dict()
    messages = _reply_messages(4)["target"]["messages"]
    messages[0]["role"] = "assistant"
    candidate["target"] = {"messages": messages}
    result = G6StructuralGate().evaluate(candidate, {"turn_bounds": (2, 8)})
    assert result.decision == "rejected"
    assert "first_message_not_human" in result.reason_codes


def test_g6_rejects_non_alternating():
    candidate = _candidate_dict()
    messages = _reply_messages(4)["target"]["messages"]
    messages[2]["role"] = "assistant"
    candidate["target"] = {"messages": messages}
    result = G6StructuralGate().evaluate(candidate, {"turn_bounds": (2, 8)})
    assert result.decision == "rejected"
    assert any("non_alternating" in c for c in result.reason_codes)


def test_g6_skips_protocol_targets_without_messages():
    candidate = _candidate_dict()
    candidate["target"] = {"action": "NO_OP", "reason": "current_context_is_sufficient"}
    result = G6StructuralGate().evaluate(candidate, {})
    assert result.decision == "approved"


# ───────────────────────── G7 ─────────────────────────

def test_g7_skips_when_no_review_required():
    result = G7HumanReviewGate().evaluate(_candidate_dict(), {"human_review_required": False})
    assert result.decision == "approved"


def test_g7_rejects_missing_human_review():
    result = G7HumanReviewGate().evaluate(
        _candidate_dict(), {"human_review_required": True, "gate_decisions": []}
    )
    assert result.decision == "rejected"


def test_g7_approves_with_reviewer_approved_decision():
    candidate = _candidate_dict()
    decisions = [
        {
            "subject_candidate_record_id": candidate["record_id"],
            "reviewer": "human-1",
            "decision": "approved",
        }
    ]
    result = G7HumanReviewGate().evaluate(
        candidate, {"human_review_required": True, "gate_decisions": decisions}
    )
    assert result.decision == "approved"


# ───────────────────────── 编排与物化 ─────────────────────────

def test_release_quality_gate_runs_mandatory_gates_first():
    gate = ReleaseQualityGate()
    results = gate.evaluate(_candidate_dict(), {"lock": _lock()}, enabled_gates={"G4", "G6"})
    ids = [r.gate_id for r in results]
    assert ids[:4] == ["G0", "G1", "G2", "G3"]  # 必过门总是最先执行
    assert "G6" in ids
    assert "G5" not in ids  # 未启用


def test_injected_gate_unconfigured_is_needs_revision():
    gate = ReleaseQualityGate()
    results = gate.evaluate(_candidate_dict(), {"lock": _lock()})
    g5 = next(r for r in results if r.gate_id == "G5")
    assert g5.decision == "needs_revision"


def test_injected_gate_with_checker():
    gate = ReleaseQualityGate()
    gate.set_injected("G5", lambda candidate, context: ["bad_secret"])
    results = gate.evaluate(_candidate_dict(), {"lock": _lock()})
    g5 = next(r for r in results if r.gate_id == "G5")
    assert g5.decision == "rejected"


# ───────────────────────── G3/G4：真实门（阶段 1，P0-1 验收） ─────────────────────────

def _ref(
    source_kind: str = "identity_fact",
    source_id: str = "identity:age",
    snapshot_id: str = "snap:alpha:identity",
) -> dict:
    return {
        "source_kind": source_kind,
        "source_id": source_id,
        "snapshot_id": snapshot_id,
        "field_pointer": "",
    }


def test_g3_approves_not_required_without_refs():
    # 装饰性任务（安静陪伴等）不主张事实 → 无需 profile 锚定
    result = G3ProfileAnchorGate().evaluate(_candidate_dict(), {"lock": _lock()})
    assert result.decision == "approved"


def test_g3_rejects_empty_source_refs():
    candidate = _candidate_dict()
    candidate["evidence_state"] = "supported"
    result = G3ProfileAnchorGate().evaluate(candidate, {"lock": _lock()})
    assert result.decision == "rejected"
    assert "profile_source_refs_missing" in result.reason_codes


def test_g3_rejects_snapshot_not_in_lock():
    candidate = _candidate_dict()
    candidate["evidence_state"] = "supported"
    candidate["source_refs"] = [_ref(snapshot_id="snap:elsewhere")]
    result = G3ProfileAnchorGate().evaluate(candidate, {"lock": _lock()})
    assert result.decision == "rejected"
    assert "snapshot_not_in_lock:snap:elsewhere" in result.reason_codes


def test_g3_rejects_incomplete_ref():
    candidate = _candidate_dict()
    candidate["evidence_state"] = "supported"
    candidate["source_refs"] = [{"snapshot_id": "snap:alpha:identity", "source_kind": "identity_fact"}]
    result = G3ProfileAnchorGate().evaluate(candidate, {"lock": _lock()})
    assert result.decision == "rejected"
    assert "source_id_missing" in result.reason_codes


def test_g3_approves_valid_refs():
    candidate = _candidate_dict()
    candidate["evidence_state"] = "supported"
    candidate["source_refs"] = [_ref()]
    result = G3ProfileAnchorGate().evaluate(candidate, {"lock": _lock()})
    assert result.decision == "approved"


def test_g4_approves_protocol_in_lock():
    result = G4ProtocolRegistryGate().evaluate(_candidate_dict(), {"lock": _lock()})
    assert result.decision == "approved"


def test_g4_approves_with_protocol_context():
    # 生产路径：bundle_id 与 package_id 不同，经 context.protocol 映射查 lock
    candidate = _candidate_dict()
    candidate["protocol_bundle_id"] = "relationship-runtime-v1"
    result = G4ProtocolRegistryGate().evaluate(
        candidate,
        {
            "lock": _lock(),
            "protocol": {
                "protocol_bundle_id": "relationship-runtime-v1",
                "package_id": "protocol:fixture:rel",
            },
        },
    )
    assert result.decision == "approved"


def test_g4_rejects_bundle_mismatch_with_context():
    result = G4ProtocolRegistryGate().evaluate(
        _candidate_dict(),
        {
            "lock": _lock(),
            "protocol": {"protocol_bundle_id": "other-bundle", "package_id": "protocol:other"},
        },
    )
    assert result.decision == "rejected"
    assert any(c.startswith("protocol_bundle_mismatch") for c in result.reason_codes)


def test_g4_rejects_missing_bundle():
    candidate = _candidate_dict()
    candidate["protocol_bundle_id"] = ""
    result = G4ProtocolRegistryGate().evaluate(candidate, {"lock": _lock()})
    assert result.decision == "rejected"
    assert "protocol_bundle_id_missing" in result.reason_codes


def test_g4_rejects_protocol_not_in_lock():
    candidate = _candidate_dict()
    candidate["protocol_bundle_id"] = "protocol:elsewhere"
    result = G4ProtocolRegistryGate().evaluate(candidate, {"lock": _lock()})
    assert result.decision == "rejected"
    assert "protocol_not_in_lock:protocol:elsewhere" in result.reason_codes


def test_set_injected_rejects_g3_and_g4():
    gate = ReleaseQualityGate()
    with pytest.raises(ValueError, match="G3"):
        gate.set_injected("G3", lambda candidate, context: [])
    with pytest.raises(ValueError, match="G4"):
        gate.set_injected("G4", lambda candidate, context: [])


# ───────────────────────── ReleasePolicy 绑定（P0-1 验收） ─────────────────────────

def test_policy_required_gate_missing_implementation_rejected():
    gate = ReleaseQualityGate(release_policy={"required_core_gates": ["G0", "G1", "G2", "G3", "G9"]})
    results = gate.evaluate(_candidate_dict(), {"lock": _lock()})
    assert any(
        r.gate_id == "G9" and r.decision == "rejected" and "missing_required_gate:G9" in r.reason_codes
        for r in results
    )
    assert not accepted(results)


def test_policy_required_gate_forced_despite_enabled():
    # 调用方 enabled 漏掉 policy 声明门 G7 → G7 仍被执行（不可裁剪）
    gate = ReleaseQualityGate(release_policy={"required_core_gates": ["G0", "G1", "G2", "G3", "G7"]})
    results = gate.evaluate(
        _candidate_dict(), {"lock": _lock()}, enabled_gates={"G0", "G1", "G2", "G3"}
    )
    assert any(r.gate_id == "G7" for r in results)


def test_no_policy_behaves_as_all_registered_gates():
    # 未绑定 policy：全部注册门执行（含真实 G3/G4、未配置 G5=needs_revision）
    gate = ReleaseQualityGate()
    results = gate.evaluate(_candidate_dict(), {"lock": _lock()})
    ids = {r.gate_id for r in results}
    assert {"G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"} <= ids


def test_accepted_requires_all_approved():
    from data_gen_v4.core.gates import GateResult

    results = [GateResult("G0", "approved"), GateResult("G2", "approved")]
    assert accepted(results) is True
    results.append(GateResult("G6", "rejected", ["x"]))
    assert accepted(results) is False
    assert accepted([]) is False
