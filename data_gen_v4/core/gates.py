"""独立质量门：core gates（《数据生成器v4设计》§14）。

G0-G3 为不可裁剪必过门；G4-G7 由 ReleasePolicy.required_core_gates[] 启用。
阶段 1（2026-08-07，v2 P0-1）：G3/G4 为真实实现（profile 锚定 / protocol 注册），
不再接受注入；仅 G5/G8 保留注入式接口。G0/G1/G2/G6/G7 为确定性实现。

硬门语义：G2（来源与 span 事实）与 G5（安全）失败不可被其他轴分数补偿。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .claims import claim_text_present
from .schemas import validate_record

GateDecision = Literal["approved", "rejected", "needs_revision"]

# 不可裁剪的必过门（§14.1）；G2/G5 为硬门。tuple 保证执行顺序稳定。
MANDATORY_CORE_GATES = ("G0", "G1", "G2", "G3")
HARD_GATES = frozenset({"G2", "G5"})

# 可注入门白名单：阶段 1（P0-1）起仅 G5（秘密词）/G8（安全动作）允许注入知识，
# G3/G4 为真实实现——注入式 no-op 通道关闭（ReleasePolicy 不允许调用方 no-op）。
INJECTABLE_GATES = frozenset({"G5", "G8"})


@dataclass(frozen=True, slots=True)
class GateResult:
    gate_id: str
    decision: GateDecision
    reason_codes: list[str] = field(default_factory=list)


class CoreGate:
    gate_id = ""

    def evaluate(self, candidate: dict[str, Any], context: dict[str, Any]) -> GateResult:
        raise NotImplementedError


def _approved(gate_id: str) -> GateResult:
    return GateResult(gate_id=gate_id, decision="approved")


def _rejected(gate_id: str, *reason_codes: str) -> GateResult:
    return GateResult(gate_id=gate_id, decision="rejected", reason_codes=list(reason_codes))


# ───────────────────────── G0-G2：确定性实现 ─────────────────────────

class G0RecordIntegrityGate(CoreGate):
    """G0 record/schema integrity：candidate 必须通过 record_v4 schema 校验。"""

    gate_id = "G0"

    def evaluate(self, candidate: dict[str, Any], context: dict[str, Any]) -> GateResult:
        try:
            validate_record(candidate)
        except Exception as error:  # noqa: BLE001
            return _rejected(self.gate_id, f"record_schema:{error}")
        return _approved(self.gate_id)


class G1PackageSnapshotGate(CoreGate):
    """G1 package and snapshot integrity：candidate 的 lock 引用必须与上下文中的
    冻结 lock 一致；package 层锚定由 lock_hash 覆盖（hash 含全部 pin，§5.5），
    此处再校验 source snapshot 引用存在。"""

    gate_id = "G1"

    def evaluate(self, candidate: dict[str, Any], context: dict[str, Any]) -> GateResult:
        lock = context.get("lock")
        if not lock:
            return _rejected(self.gate_id, "missing_lock")
        if candidate.get("package_lock_hash") != lock.get("lock_hash"):
            return _rejected(self.gate_id, "lock_hash_mismatch")
        snapshots = lock.get("source_snapshots", {})
        for ref in candidate.get("source_refs", []):
            snapshot_id = ref.get("snapshot_id")
            if snapshot_id and snapshot_id not in snapshots:
                return _rejected(self.gate_id, f"snapshot_not_in_lock:{snapshot_id}")
        return _approved(self.gate_id)


def _resolve_field(value: Any, pointer: str) -> Any:
    """极简 JSON Pointer 解析（"" 返回自身；"/a/b" 逐段索引）。"""
    if not pointer:
        return value
    node = value
    for part in pointer.lstrip("/").split("/"):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit():
            index = int(part)
            node = node[index] if index < len(node) else None
        else:
            return None
        if node is None:
            return None
    return node


# 需要证据的任务（P0-2，阶段 1）：这些 evidence_state 下空 source_refs/spans 即拒绝；
# not_required（安静陪伴等）为空证据任务的装饰性允许。
EVIDENCE_REQUIRED_STATES = frozenset({"supported", "insufficient", "conflicted", "false_premise"})


class G2SourceSpanGate(CoreGate):
    """G2 source ID and support span validity：support span 按 snapshot 与 field_pointer
    取字符串，按 Unicode code point [start,end) 截取并逐字符比对 quote（§8.3）。

    阶段 1（P0-2）：需要证据的任务（evidence_state ∈ EVIDENCE_REQUIRED_STATES）空
    source_refs / support_spans → 拒绝（evidence_missing_*）——证据是发布事实，
    不是装饰性字段；not_required 任务保留空允许。
    阶段 3（C-1）：候选显式声明 claims 时启用 claim 级校验（span→claim 关联、
    claim 文本与正文一致，自报 used_facts 与正文不一致必败）。
    """

    gate_id = "G2"

    def evaluate(self, candidate: dict[str, Any], context: dict[str, Any]) -> GateResult:
        snapshots = context.get("snapshots", {})
        evidence_state = candidate.get("evidence_state", "not_required")
        refs = candidate.get("source_refs", [])
        spans = candidate.get("support_spans", [])
        if evidence_state in EVIDENCE_REQUIRED_STATES:
            if not refs:
                return _rejected(self.gate_id, "evidence_missing_source_refs")
            if not spans:
                return _rejected(self.gate_id, "evidence_missing_spans")
        if not spans:
            return _approved(self.gate_id)
        # 阶段 3（C-1）：claim 级校验——候选显式声明 claims 时启用：
        # 1) span.supported_claim_id 必须指向候选声明的 claim（可追溯）
        # 2) claim 文本必须出现在正文（自报事实与正文一致）
        claims = candidate.get("claims") or []
        if claims:
            claims_by_id = {c.get("claim_id"): c for c in claims}
            for span in spans:
                claim_id = span.get("supported_claim_id")
                if claim_id and claim_id not in claims_by_id:
                    return _rejected(self.gate_id, f"claim_not_found:{claim_id}")
            for claim in claims:
                claim_text = str(claim.get("text", ""))
                if claim_text and not claim_text_present(candidate, claim_text):
                    return _rejected(
                        self.gate_id, f"claim_missing_in_text:{claim.get('claim_id')}"
                    )
        for span in spans:
            reason = self._check_span(span, snapshots)
            if reason:
                return _rejected(self.gate_id, reason)
        return _approved(self.gate_id)

    @staticmethod
    def _check_span(span: dict[str, Any], snapshots: dict[str, Any]) -> str | None:
        if span.get("offset_unit") != "unicode_code_point":
            return f"bad_offset_unit:{span.get('offset_unit')!r}"
        start, end = span.get("start"), span.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
            return f"bad_range:{start}-{end}"
        snapshot = snapshots.get(span.get("snapshot_id"))
        if snapshot is None:
            return f"missing_snapshot:{span.get('snapshot_id')}"
        unit = next(
            (
                u
                for u in snapshot.get("units", [])
                if u.get("source_id") == span.get("source_id")
                and u.get("source_kind") == span.get("source_kind")
            ),
            None,
        )
        if unit is None:
            return f"missing_unit:{span.get('source_kind')}:{span.get('source_id')}"
        text = _resolve_field(unit.get("value"), span.get("field_pointer", ""))
        if not isinstance(text, str):
            return f"span_target_not_text:{span.get('field_pointer')!r}"
        if end > len(text):
            return f"span_out_of_range:{end}>{len(text)}"
        quote = span.get("quote", "")
        if text[start:end] != quote:
            return "span_quote_mismatch"
        return None


# ───────────────────────── G3-G4：真实实现（阶段 1，替代注入式占位） ─────────────────────────

class G3ProfileAnchorGate(CoreGate):
    """G3 profile knowledge gate：candidate 的证据必须锚定 profile 来源快照。

    阶段 1（2026-08-07，v2 P0-1）：替代原注入式占位——发布真实性要求 G3 有真实
    检查，禁止调用方注入无条件通过。
    - source_refs 非空（证据必须引用 profile 来源）
    - 每条 ref 的 snapshot_id 必须在 lock.source_snapshots（lock 仅含编译期
      profile 来源快照，来源性由此保证，无需角色知识）
    - 每条 ref 的 source_kind/source_id/field_pointer 非空（SourceRef 合同完整性）
    profile_snapshot_id 与 lock 的一致性由 lock_hash 覆盖（hash 含全部 pin）。
    """

    gate_id = "G3"

    def evaluate(self, candidate: dict[str, Any], context: dict[str, Any]) -> GateResult:
        lock = context.get("lock")
        if not lock:
            return _rejected(self.gate_id, "missing_lock")
        # 装饰性任务（安静陪伴等）不主张事实，无需 profile 锚定（与 G2 语义对齐）
        if candidate.get("evidence_state") == "not_required":
            return _approved(self.gate_id)
        refs = candidate.get("source_refs", [])
        if not refs:
            return _rejected(self.gate_id, "profile_source_refs_missing")
        snapshots = lock.get("source_snapshots", {})
        for ref in refs:
            snapshot_id = ref.get("snapshot_id")
            if not snapshot_id or snapshot_id not in snapshots:
                return _rejected(self.gate_id, f"snapshot_not_in_lock:{snapshot_id}")
            if not ref.get("source_kind"):
                return _rejected(self.gate_id, "source_kind_missing")
            if not ref.get("source_id"):
                return _rejected(self.gate_id, "source_id_missing")
            # field_pointer 允许 ""（指向单元 value 本身，与 G2 语义一致），只查存在性
            if "field_pointer" not in ref:
                return _rejected(self.gate_id, "field_pointer_missing")
        return _approved(self.gate_id)


class G4ProtocolRegistryGate(CoreGate):
    """G4 protocol registry gate：candidate 的协议包必须已冻结在 lock。

    阶段 1（2026-08-07，v2 P0-1）：替代原注入式占位。protocol_bundle_id 非空；
    有 context.protocol 时校验 bundle 包（package_id）已进入 lock.packages——
    bundle_id 与 package_id 是不同字段（如 relationship-runtime-v1 ↔
    protocol.relationship.runtime.v1），以 package_id 为准查 lock；无 context 时
    退化按 bundle_id 直接查（测试场景）。mode 注册检查由 compiler 完成。
    """

    gate_id = "G4"

    def evaluate(self, candidate: dict[str, Any], context: dict[str, Any]) -> GateResult:
        lock = context.get("lock")
        if not lock:
            return _rejected(self.gate_id, "missing_lock")
        bundle_id = candidate.get("protocol_bundle_id")
        if not bundle_id:
            return _rejected(self.gate_id, "protocol_bundle_id_missing")
        packages = lock.get("packages", {})
        protocol = context.get("protocol")
        if protocol is not None:
            if bundle_id != protocol.get("protocol_bundle_id"):
                return _rejected(
                    self.gate_id, f"protocol_bundle_mismatch:{bundle_id}!={protocol.get('protocol_bundle_id')}"
                )
            if protocol.get("package_id") not in packages:
                return _rejected(self.gate_id, f"protocol_not_in_lock:{protocol.get('package_id')}")
        elif bundle_id not in packages:
            return _rejected(self.gate_id, f"protocol_not_in_lock:{bundle_id}")
        return _approved(self.gate_id)


# ───────────────────────── G5：注入式接口（仅 G5/G8） ─────────────────────────

class InjectedGate(CoreGate):
    """由注入的校验函数提供知识；未注入时返回 needs_revision（不能静默通过）。"""

    def __init__(self, gate_id: str, checker: Callable[[dict[str, Any], dict[str, Any]], list[str]] | None = None) -> None:
        self.gate_id = gate_id
        self._checker = checker

    def evaluate(self, candidate: dict[str, Any], context: dict[str, Any]) -> GateResult:
        if self._checker is None:
            return GateResult(self.gate_id, "needs_revision", [f"{self.gate_id}_validator_not_configured"])
        reasons = self._checker(candidate, context)
        if reasons:
            return _rejected(self.gate_id, *reasons)
        return _approved(self.gate_id)


def secret_keyword_checker(terms: tuple[str, ...] = ()):
    """返回 G5 注入式 checker：扫描 candidate 全部 assistant 文本，命中即拒绝。

    checker 签名 (candidate, context) -> list[str]（reason codes）。
    terms 必由调用方从 ProfilePackage.policy_terms.secret_terms 传入（空 = 不拦截）。
    """

    def checker(candidate: dict[str, Any], context: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        target = candidate.get("target") or {}
        for message in target.get("messages", []):
            if message.get("role") != "assistant":
                continue
            text = str(message.get("content", ""))
            for term in terms:
                if term in text:
                    reasons.append(f"secret_leak:{term}")
        return reasons

    return checker


# ───────────────────────── G8：安全动作硬门（阶段 3 C-2：规则进 ProtocolBundle） ─────────────────────────
# P1（2026-08-07）：评审缺陷③——v2500 在脑卒中/化学入眼/冻伤等场景给出缺失
# 或错误处置。safety 池样本由教师生成，正确性上限受教师知识约束——G8 用
# "场景×必需动作/禁止词"硬门兜底：缺必需处置动作或命中已知错误处置 → 拒绝重试。
# 阶段 3（2026-08-08）：规则表从 ProtocolBundle（context.protocol.safety_action_rules
# /safety_topic_keys/safety_attitude_forbidden）读取——安全知识只进审核过的协议包，
# core 不内置；规则缺失 → safety_rules_not_configured（安全门不静默放行）。
# 注意：G8 只按 topic 场景激活（topic 无匹配规则时放行，不误伤其他池）。


def safety_action_checker():
    """返回 G8 注入式 checker：safety 场景样本必须含处置动作、不得含已知错误处置。

    checker 签名 (candidate, context) -> list[str]（reason codes）。
    阶段 3（C-2）：规则表从 context.protocol（ProtocolBundle）读取；协议未声明
    安全规则 → 放行（与 G5 词表同语义：配置了才拦；生产协议必须携带规则，
    gen_qin_v4 注入时校验）。topic 无匹配规则 → 放行（不误伤其他池）。
    """

    def checker(candidate: dict[str, Any], context: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        protocol = context.get("protocol") or {}
        rules = protocol.get("safety_action_rules") or {}
        topic_keys = protocol.get("safety_topic_keys") or []
        attitude_forbidden = protocol.get("safety_attitude_forbidden") or []
        if not rules:
            return reasons
        item_input = (candidate.get("input") or {}).get("topic") or ""
        rule_key = next(
            (key for kw, key in topic_keys if kw in str(item_input)), None
        )
        if rule_key is None:
            return reasons
        ruleset = rules[rule_key]
        target = candidate.get("target") or {}
        assistant_text = " ".join(
            str(m.get("content", ""))
            for m in target.get("messages", [])
            if m.get("role") == "assistant"
        )
        required_words = tuple(ruleset.get("required") or ())
        forbidden_words = tuple(ruleset.get("forbidden") or ())
        required_hit = any(w in assistant_text for w in required_words)
        if not required_hit:
            reasons.append(f"safety_action_missing:{rule_key}")
        # 禁止词：剔除否定指令（"别揉""别用热水""千万别开灯"）后再查——
        # 否定词 + 0-2 个间隔字 + 禁止词 整体剔除，避免"别揉"残留"揉"
        forbidden_sorted = sorted(forbidden_words, key=len, reverse=True)
        if forbidden_sorted:
            neg_pattern = re.compile(
                r"(?:千万不要|千万别|不要|可别|不能|别|不).{0,2}("
                + "|".join(re.escape(w) for w in forbidden_sorted)
                + ")"
            )
            cleaned = neg_pattern.sub("", assistant_text)
        else:
            cleaned = assistant_text
        # required 词同样剔除否定（"别碰凉水"是错误指导，不能算命中"凉水"）
        required_sorted = sorted(required_words, key=len, reverse=True)
        if required_sorted:
            neg_required = re.compile(
                r"(?:千万不要|千万别|不要|可别|不能|别|不).{0,2}("
                + "|".join(re.escape(w) for w in required_sorted)
                + ")"
            )
            required_checked = neg_required.sub("", assistant_text)
        else:
            required_checked = assistant_text
        if not any(w in required_checked for w in required_words):
            reasons.append(f"safety_action_missing:{rule_key}")
        for word in forbidden_words:
            if word in cleaned:
                reasons.append(f"safety_action_forbidden:{rule_key}:{word}")
        # 通用态度禁止词（原句查，否定指令也拦——"没空"就是拒绝协助）
        for word in attitude_forbidden:
            if word in assistant_text:
                reasons.append(f"safety_attitude_forbidden:{word}")
        return reasons

    return checker


class G6StructuralGate(CoreGate):
    """G6 structural completeness：REPLY 类 target 的消息结构（轮数范围由调用方
    参数化，核心不写死 4-8）；协议样本无 messages 数组时由 G0/G3 覆盖。"""

    gate_id = "G6"

    def evaluate(self, candidate: dict[str, Any], context: dict[str, Any]) -> GateResult:
        target = candidate.get("target", {})
        messages = target.get("messages")
        if messages is None:
            return _approved(self.gate_id)
        if not isinstance(messages, list) or not messages:
            return _rejected(self.gate_id, "empty_messages")
        bounds = context.get("turn_bounds", (2, 64))
        if not (bounds[0] <= len(messages) <= bounds[1]):
            return _rejected(
                self.gate_id, f"turn_count_out_of_bounds:{len(messages)}"
            )
        for i, message in enumerate(messages):
            if not isinstance(message, dict) or not message.get("content"):
                return _rejected(self.gate_id, f"empty_message_at:{i}")
            role = message.get("role")
            if i == 0 and role != "human":
                return _rejected(self.gate_id, "first_message_not_human")
            if i == len(messages) - 1 and role != "assistant":
                return _rejected(self.gate_id, "last_message_not_assistant")
            if i > 0 and role == messages[i - 1].get("role"):
                return _rejected(self.gate_id, f"non_alternating_at:{i}")
        return _approved(self.gate_id)


class G7HumanReviewGate(CoreGate):
    """G7 required human review：context 声明需要人工审核时，必须存在 reviewer
    非空且 decision=approved 的 gate decision 记录（§14.4）。"""

    gate_id = "G7"

    def evaluate(self, candidate: dict[str, Any], context: dict[str, Any]) -> GateResult:
        if not context.get("human_review_required", False):
            return _approved(self.gate_id)
        decisions = context.get("gate_decisions", [])
        for decision in decisions:
            if (
                decision.get("subject_candidate_record_id") == candidate.get("record_id")
                and decision.get("reviewer")
                and decision.get("decision") == "approved"
            ):
                return _approved(self.gate_id)
        return _rejected(self.gate_id, "human_review_missing")


# ───────────────────────── 编排 ─────────────────────────

class ReleaseQualityGate:
    """编排已锁定的 core gates 与注入式校验器（§14）。

    阶段 1（P0-1）：绑定 ReleasePolicy 后，policy 声明的 required_core_gates 必须
    全部执行——调用方 enabled 集合不可裁剪 policy 门（缺执行即不真实）；声明门无
    实现 → rejected（missing_required_gate），不允许"声明了却不跑/空跑"。
    """

    def __init__(
        self,
        gates: list[CoreGate] | None = None,
        release_policy: dict[str, Any] | None = None,
    ) -> None:
        by_id = {gate.gate_id: gate for gate in (gates or _default_gates())}
        self._gates = by_id
        self._required: set[str] = set()
        if release_policy is not None:
            self._required = {str(g) for g in (release_policy.get("required_core_gates") or [])}

    def evaluate(
        self,
        candidate: dict[str, Any],
        context: dict[str, Any],
        enabled_gates: set[str] | None = None,
    ) -> list[GateResult]:
        enabled = enabled_gates if enabled_gates is not None else set(self._gates)
        results = []
        # 1) 必过门（G0-G3）恒执行；实现缺失 → rejected（发布真实性，不可空跑）
        for gate_id in MANDATORY_CORE_GATES:
            gate = self._gates.get(gate_id)
            if gate is None:
                results.append(_rejected(gate_id, f"missing_required_gate:{gate_id}"))
            else:
                results.append(gate.evaluate(candidate, context))
        # 2) policy required 门（G4-G7 等）：调用方 enabled 不可裁剪；缺失实现 → rejected
        for gate_id in sorted(self._required - set(MANDATORY_CORE_GATES)):
            gate = self._gates.get(gate_id)
            if gate is None:
                results.append(_rejected(gate_id, f"missing_required_gate:{gate_id}"))
            else:
                results.append(gate.evaluate(candidate, context))
        # 3) 其余 enabled（增强门：G5/G8 注入等，调用方自由选择）
        for gate_id in sorted((enabled - set(MANDATORY_CORE_GATES)) - self._required):
            gate = self._gates.get(gate_id)
            if gate is not None:
                results.append(gate.evaluate(candidate, context))
        return results

    def set_injected(self, gate_id: str, checker: Callable[[dict[str, Any], dict[str, Any]], list[str]]) -> None:
        """为 G5/G8 注入知识校验器；G3/G4 自阶段 1 起为真实实现，禁止注入（no-op 通道关闭）。"""
        if gate_id not in INJECTABLE_GATES:
            raise ValueError(
                f"gate {gate_id} 不允许注入（仅 {sorted(INJECTABLE_GATES)}；"
                "G3/G4 为真实实现，阶段 1 起禁止 no-op 注入）"
            )
        self._gates[gate_id] = InjectedGate(gate_id, checker)


def accepted(results: list[GateResult], hard_gates: set[str] = HARD_GATES) -> bool:
    """物化：accepted release 由全部必需 gate 的最新有效 decision 物化（§8.4），
    任一非 approved 即不通过；G2/G5 硬门失败一票否决且不可被其他轴补偿。"""
    if not results:
        return False
    for result in results:
        if result.decision != "approved":
            return False
        if result.gate_id in hard_gates and result.reason_codes:
            return False
    return True


def _default_gates() -> list[CoreGate]:
    return [
        G0RecordIntegrityGate(),
        G1PackageSnapshotGate(),
        G2SourceSpanGate(),
        G3ProfileAnchorGate(),
        G4ProtocolRegistryGate(),
        InjectedGate("G5"),
        G6StructuralGate(),
        G7HumanReviewGate(),
    ]