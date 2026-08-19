"""ReplyModeAdapter（《数据生成器v4设计》§10.1）。

generate 内三步模型调用（调用方不需要学习内部状态机）：
1. user simulator：只看到 player_view 生成玩家台词（防泄漏，§10.1）。
2. 语义骨架：决定"说什么"（命题、禁止项、情绪基调）。
3. 风格实现：把骨架实现为角色口吻对话（风格知识只来自 profile 的 style_contract）。

之后做确定性检查：JSON 解析、结构（交替/首 human 末 assistant/轮数参数化）、
语义保持（骨架必需命题须出现在最终文本）。失败显式转 ModeFailure。

render_training：静态对话监督全部 assistant 消息；system anchor 由调用方提供。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from data_gen_v4.core.errors import V4ErrorCode
from data_gen_v4.core.resolver import canonical_json
from data_gen_v4.core.schemas import validate_plan_item  # noqa: F401  (类型参考)
from data_gen_v4.core.claims import build_claims, claim_text_present

from .renderers import ProductionRenderers
from .training import TrainingRecordV4, estimate_token_count

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

_MODE_FAILURE = "mode_failure"

# ── T5（2026-08-06）：evidence_state / desired_policy → 生成行为指令（通用合同）──
# 标签不再只是记录，而是渲染进生成 prompt 约束教师模型的行为。

EVIDENCE_GUIDES: dict[str, str] = {
    "supported": "本轮信息充分：基于玩家提供的内容正常回应，不编造额外前情。",
    "insufficient": (
        "玩家提供的信息不足，无法确定具体所指：保持未知，不要自行补全前情、"
        "不要编造细节，必要时只问一个自然的澄清问题。"
    ),
    "false_premise": (
        "玩家的话包含一个与事实不符的前提：自然纠正或如实说明不知道，"
        "不要顺着错误前提编造内容。"
    ),
    "conflicted": "玩家给出的信息与已知事实冲突：以已知事实为准，自然地指出不一致。",
    "not_required": "（无额外证据约束）",
}

POLICY_GUIDES: dict[str, str] = {
    "answer": "直接回答玩家。",
    "hint_only": "可以暗示，但不要直接点破。",
    "withhold": "不透露秘密；不撒谎，可以回避或转移话题。",
    "safety_first_answer": "安全优先：给出正确、清楚的应对，不调侃。",
    "answer_with_uncertainty": "不确定时明确说明不确定，不编造。",
    "correct_premise": "纠正玩家话里的错误前提。",
    "ask_for_evidence": "需要更多信息时自然地询问。",
    "surface_conflict": "指出玩家信息与事实的矛盾。",
    "refuse_unsafe_and_redirect": "拒绝不安全请求，并引导到安全做法。",
}


class ReplyModeAdapter:
    def __init__(
        self,
        *,
        prompts_dir: Path = PROMPTS_DIR,
        render_profile_id: str = "reply-runtime-v1",
        protocol_snapshot_id: str = "reply-protocol-v1",
        semantic_check: Callable[[list[str], str], list[str]] | None = None,
        style_contract_resolver: Callable[[str], str] | None = None,
        semantic_extra_body: dict[str, Any] | None = None,
        max_tokens_override: dict[str, int] | None = None,
    ) -> None:
        self._prompts_dir = Path(prompts_dir)
        self._render_profile_id = render_profile_id
        self._protocol_snapshot_id = protocol_snapshot_id
        self._semantic_check = semantic_check or _default_semantic_check
        # style_contract 若为引用（如 "style:qinweixi-casual-v1"），由管线注入
        # resolver 解析为实际口吻文本；未注入时原样使用（默认 fixture 为文本）。
        self._style_resolver = style_contract_resolver
        # semantic 步的 provider 特定参数（如 reasoning 模型的禁思考开关），
        # 由管线按 provider 配置注入；core 保持通用
        self._semantic_extra_body = semantic_extra_body
        # max_tokens 实验覆盖（2026-08-05：③ 档位实验用，None 时用默认档位表）
        self._max_tokens_override = max_tokens_override

    # ───────────────────────── ModeAdapter 接口 ─────────────────────────

    def prepare(self, plan_item: dict[str, Any], package_set: dict[str, Any]) -> dict[str, Any]:
        return {"plan_item": plan_item, "package_set": package_set}

    def generate(self, mode_job: dict[str, Any], call_executor: Any) -> dict[str, Any]:
        item = mode_job["plan_item"]
        package_set = mode_job["package_set"]
        try:
            return self._generate(item, package_set, call_executor)
        except _ModeFailureSignal as failure:
            return failure.payload

    def render_training(self, candidate: dict[str, Any], package_set: dict[str, Any]) -> TrainingRecordV4:
        # 生产 renderer 合同（§10.5）：身份锚+输出协议 → 对话；监督全部 assistant。
        # T2：锚为运行时散文式（profile+protocol 渲染），不再含 style_contract 段。
        return ProductionRenderers.reply_training_record(
            candidate, package_set, render_profile_id=self._render_profile_id
        )

    # ───────────────────────── 内部 ─────────────────────────

    def _generate(
        self, item: dict[str, Any], package_set: dict[str, Any], call_executor: Any
    ) -> dict[str, Any]:
        profile = package_set.get("profile", {})
        terms = profile.get("policy_terms") or {}  # 大块 A：角色词表唯一来源
        item_input = item.get("input") or {}
        scene = item_input.get("scene") or "日常"
        topic = item_input.get("topic") or "闲聊"
        player_view = item_input.get("player_view") or scene
        raw_facts = package_set.get("facts") or []
        if isinstance(raw_facts, str):
            raw_facts = [raw_facts]
        facts = _render_facts(raw_facts)
        # 2026-08-14（§24.2）：special 记忆注入——reply_memory 类 item 从 timeline
        # 快照蒸馏可注入事件（过滤 visibility/disclosure/memory_pool），其余不注入
        memory_facts_raw: list[str] = []
        if item.get("memory_type") == "special":
            memory_facts_raw = _distill_memory_facts(package_set, item)
        memory_facts = _render_facts(memory_facts_raw)
        bounds = item_input.get("turn_bounds") or (4, 8)
        turn_bounds = (int(bounds[0]), int(bounds[1]))

        # 1) 语义骨架 + 风格实现合并为单次调用（2026-08-05 提速：省一半模型调用。
        #    reasoning 模型思考 token 是大头，合并后思考不变、输出略长，整体 ~-40%）
        style_contract = _style_contract(
            profile, self._style_resolver, tics_enabled=_tics_enabled(item)
        )
        beliefs = package_set.get("beliefs", "") or "（无）"
        evidence_state = item.get("evidence_state") or "not_required"
        desired_policy = item.get("desired_policy") or "answer"
        merge_prompt = self._render(
            "reply_merge",
            STYLE_CONTRACT=style_contract,
            FACTS=facts,
            MEMORY_FACTS=memory_facts,
            BELIEFS=beliefs,
            SCENE=scene,
            PLAYER_VIEW=player_view,
            GENERATION_GUIDANCE=item_input.get("generation_guidance") or "（无）",
            TOPIC=topic,
            REQUIRED_BEHAVIORS=_bullet(item.get("required_behaviors", [])),
            FORBIDDEN_BEHAVIORS=_bullet(item.get("forbidden_behaviors", [])),
            EVIDENCE_GUIDE=EVIDENCE_GUIDES.get(evidence_state, EVIDENCE_GUIDES["not_required"]),
            POLICY_GUIDE=POLICY_GUIDES.get(desired_policy, POLICY_GUIDES["answer"]),
            TURNS_MIN=turn_bounds[0], TURNS_MAX=turn_bounds[1],
            # 大块 A：角色政策段/角色名由 ProfilePackage 注入（模板不再含角色内容）
            POLICY_BLOCK=profile.get("prompt_policy_block", ""),
            CHARACTER_NAME=profile.get("display_name", ""),
        )
        # 2026-08-09：称呼注入（多角色通用）——item.input.player_name 存在时，
        # prompt 追加称呼段（名字可配置，多样名字增强：模型学"锚里/提示里的名字
        # 就是玩家名字"，不绑定具体名字）。80% 样本不注入 = 现状零称呼。
        player_name = (item.get("input") or {}).get("player_name")
        if player_name:
            informal = (item.get("input") or {}).get("player_informal") or ""
            nickname_hint = f"（小名{informal}）" if informal else ""
            merge_prompt += (
                f"\n【称呼】玩家的名字是{player_name}{nickname_hint}。"
                f"对话中可以用他的名字称呼他（如\"{player_name}，你回来啦\"），"
                f"但必须放在自然位置：第一句先承接玩家话题（回应他刚说的话），"
                f"称呼放在中后段自然带出；禁止把称呼硬塞进第一句导致答非所问；"
                f"不必每句都叫。"
            )
        # 第一批改造（2026-08-07）：带反馈重试——engine 把上次失败原因放进
        # item["_last_failure"]，这里注入 prompt 尾部，教师针对错误修正重写
        last_failure = item.get("_last_failure")
        if last_failure:
            reason = str(last_failure.get("reason", ""))[:400]
            merge_prompt += (
                "\n\n【上次生成被拒绝】原因：" + reason
                + "\n请针对该原因修正后重新输出完整的 JSON 对象，不要重复同样的错误。"
            )
        merge_result = call_executor.call(
            self._call_spec(merge_prompt, "semantic", item, seed_extra="merge"),
            stage="semantic",
            attempt_no=item.get("attempt_no", 1),
            # 大块 B（P0-4）：candidate_no 由 engine 注入（K 候选幂等键隔离）
            candidate_no=item.get("candidate_no"),
        )
        merged = _parse_object(merge_result["content"], "合并输出")
        if not isinstance(merged, dict):
            raise _ModeFailureSignal(_failure("parse_error", True, "合并输出不是 JSON 对象"))
        propositions = [str(p) for p in merged.get("assistant_propositions", [])]
        # 阶段 3（C-1）：命题注册为结构化 claim，support_spans 的 supported_claim_id
        # 重写为真实 claim_id（G2 据此校验 claim 与 span/正文一致性）
        claims, claim_spans = build_claims(
            item, propositions, item.get("support_spans", [])
        )
        # 第一人称由 prompt 软约束（要求以"我"表述，不硬拦截）：偶发第三人称时
        # 可自然处理（说漏嘴→"哎呀不是！我是说我！"），硬检查会误杀可补救样本。
        messages = _parse_messages_obj(merged)
        self._check_structure(messages, turn_bounds)
        self._check_semantics(propositions, messages)
        # T7：命题对正典支撑（P0-5）——无支撑的事实主张视为内容错误，触发重试。
        # 2026-08-14（§24.2）：special 记忆把注入事件并入 grounding 事实集——
        # 否则"我缩在墙脚躲雨"类回忆命题会被正典支撑检查误拒
        ungrounded = _check_grounding(
            propositions, raw_facts + memory_facts_raw, item.get("task_type") or "",
            living_claims=tuple(terms.get("forbidden_living_claims") or ()),
        )
        if ungrounded:
            raise _ModeFailureSignal(
                _failure("style_failure", True, f"命题无正典支撑: {ungrounded}")
            )
        # T14 二次修复：消息级禁词（命题检查扫不到润色文本里的编造表述）
        assistant_text = " ".join(m["content"] for m in messages if m["role"] == "assistant")
        hit_forbidden = [
            w for w in (terms.get("forbidden_assistant_text") or ()) if w in assistant_text
        ]
        if hit_forbidden:
            raise _ModeFailureSignal(
                _failure("style_failure", True, f"assistant 台词命中禁表述: {hit_forbidden}")
            )
        # 2026-08-07：玩家台词秘密词检查（玩家不知道的秘密细节——教师会把
        # 秘密词塞进玩家嘴里，G5 只扫 assistant 拦不住）
        human_text = " ".join(m["content"] for m in messages if m["role"] == "human")
        hit_human = [
            w for w in (terms.get("forbidden_human_text") or ()) if w in human_text
        ]
        if hit_human:
            raise _ModeFailureSignal(
                _failure("style_failure", True, f"玩家台词命中秘密词: {hit_human}")
            )
        # P3（2026-08-07）：身份/属性朗读门禁——评审缺陷①④：不相关时自我介绍、
        # 属性（怕冷/数学）不合时宜。按池 + 玩家触发词双条件，误杀最小化：
        # - 身份声明：非身份问答池一律禁（模式词来自 policy_terms）
        # - 属性声明（怕冷/数学差）：玩家台词有触发词（冷/冬天/数学/算）才放行
        recitation = _check_recitation(messages, item.get("task_type") or "", terms)
        if recitation:
            raise _ModeFailureSignal(
                _failure("style_failure", True, f"身份/属性朗读: {recitation}")
            )
        # 2026-08-08：共同经历句式兜底（非记忆类任务出现"上次/以前/那天"等
        # 第一人称过去暗示 → 重试；prompt 已前置约束，此处兜底）
        shared_history = _check_shared_history(
            messages, item.get("task_type") or ""
        )
        if shared_history:
            raise _ModeFailureSignal(
                _failure("style_failure", True, f"共同经历编造: {shared_history}")
            )
        # 2026-08-14（§24.2）：特殊记忆守卫——reply_memory 的回忆句必须由注入
        # 事件支撑（无注入时出现回忆标记即拦截），避免顺着玩家编造过去
        memory_hits = _check_memory_grounding(
            messages, memory_facts_raw, item.get("task_type") or ""
        )
        if memory_hits:
            raise _ModeFailureSignal(
                _failure("style_failure", True, f"回忆无支撑: {memory_hits}")
            )
        # 2026-08-09：玩家不自称名字——注入的 player_name 只用于角色称呼玩家，
        # 玩家台词含自己名字（"阿伟，你觉得呢""我叫阿伟"）是视角错误 → 重试
        if player_name:
            human_text = " ".join(
                m["content"] for m in messages if m["role"] == "human"
            )
            if player_name in human_text:
                raise _ModeFailureSignal(
                    _failure("style_failure", True,
                             f"玩家台词含自己的名字[{player_name}]（玩家不自称名字）")
                )
        # 2026-08-09：人称纪律兜底（多角色通用）——角色把"自己"的状态安到
        # 玩家身上（"你在纸箱边坐着"应为"我在纸箱边坐着"）。prompt 已前置约束，
        # 此处兜底硬拦：assistant 台词把动物性姿态（窝/趴/蹲/蜷）或角色私有
        # 位置（纸箱/箱子/纸盒/猫窝）写到"你"身上；视角颠倒（玩家问角色睡眠，
        # 角色把睡安到玩家）。
        pronoun_hits = _check_pronoun_role(messages)
        if pronoun_hits:
            raise _ModeFailureSignal(
                _failure("style_failure", True, f"人称纪律: {pronoun_hits}")
            )
        # 2026-08-09（盲区 3）：感知能力编造——超自然感知断言玩家身体/状态
        perception_hits = _check_perception_claims(messages)
        if perception_hits:
            raise _ModeFailureSignal(
                _failure("style_failure", True, f"感知能力编造: {perception_hits}")
            )
        # 2026-08-10（wants_to_stay 守卫）→ 2026-08-15 改写：去留表述纪律——
        # 不说"伤好会走"（决绝离开），也不直白承认"我想留下"；目标行为是
        # 隐晦表达（"这里……还行""……再说吧"）。prompt 已前置约束，此处兜底硬拦。
        leaving_hits = _check_decisive_leaving(messages)
        if leaving_hits:
            raise _ModeFailureSignal(
                _failure("style_failure", True, f"决绝离开表述: {leaving_hits}")
            )
        # P1（2026-08-07）：G8 安全动作生成侧重试——safety 场景缺处置动作/命中
        # 已知错误处置即重试（导出侧 G8 只拒不重试，产出率 62%；生成侧拦截
        # 让教师重写，产出率提升）
        safety_hits = _check_safety_action(messages, topic, package_set)
        if safety_hits:
            raise _ModeFailureSignal(
                _failure("style_failure", True, f"安全动作: {safety_hits}")
            )

        target = {"messages": [{"role": m["role"], "content": m["content"]} for m in messages]}
        return {
            "input": {
                "scene": scene, "player_view": player_view, "topic": topic,
                "turn_bounds": list(turn_bounds),
                "generation_guidance": item_input.get("generation_guidance") or "",
                # 2026-08-09：称呼注入透传（候选记录持久化，re_export 重导出
                # 可复现锚注入 → T2 锚一致）
                **{k: item_input[k] for k in ("player_name", "player_informal", "player_age")
                   if item_input.get(k)},
            },
            "target": target,
            "model": {"name": "reply-teacher", "revision": "v1"},
            "prompt_hash": _hash_of(merge_prompt),
            "prompt_template_version": "reply-merge-v1",
            "config_hash": "config:reply:v1",
            "seed": item.get("seed", "unsupported"),
            "source_event_ids": item.get("source_event_ids", []),
            "support_spans": claim_spans,
            "claims": claims,
            # 大块 B（阶段 2 P0-3）：真实调用链回链（CallRecord.record_id 列表）
            "calls": call_executor.record_ids(),
        }

    # ───────────────────────── 检查 ─────────────────────────

    def _check_structure(self, messages: list[dict[str, str]], bounds: tuple[int, int]) -> None:
        if not (bounds[0] <= len(messages) <= bounds[1]):
            raise _ModeFailureSignal(_failure("style_failure", True, f"轮数 {len(messages)} 超出 {bounds}"))
        if messages[0]["role"] != "human":
            raise _ModeFailureSignal(_failure("style_failure", True, "首条不是 human"))
        if messages[-1]["role"] != "assistant":
            raise _ModeFailureSignal(_failure("style_failure", True, "末条不是 assistant"))
        for index in range(1, len(messages)):
            if messages[index]["role"] == messages[index - 1]["role"]:
                raise _ModeFailureSignal(
                    _failure("style_failure", True, f"消息 {index} 未交替")
                )

    def _check_semantics(self, propositions: list[str], messages: list[dict[str, str]]) -> None:
        # 只检查 assistant 台词：命题必须由角色说出来，出现在 human 台词里不算实现。
        text = " ".join(m["content"] for m in messages if m["role"] == "assistant")
        missing = self._semantic_check(propositions, text)
        if missing:
            raise _ModeFailureSignal(
                _failure("style_failure", True, f"语义保持失败，缺失命题: {missing}")
            )

    # ───────────────────────── 工具 ─────────────────────────

    def _render(self, name: str, **tokens: Any) -> str:
        template = (self._prompts_dir / f"{name}.txt").read_text(encoding="utf-8")
        for key, value in tokens.items():
            template = template.replace("{{" + key + "}}", str(value))
        return template

    def _call_spec(self, prompt: str, role: str, item: dict[str, Any], *, seed_extra: str) -> dict[str, Any]:
        # reasoning 模型（deepseek-v4-flash）思考会吃大量 token：
        # semantic 8192 / style 8192（思考+正文都放得下，实测 4/4 成功）。
        # max_tokens 是上限：过大 reasoning 会拖慢调用（③ 档位实验见 max_tokens_override）。
        defaults = {"semantic": 8192, "style": 8192}
        if self._max_tokens_override:
            defaults = {**defaults, **self._max_tokens_override}
        max_tokens = defaults.get(role, 2048)
        attempt = int(item.get("attempt_no", 1))
        spec: dict[str, Any] = {
            "model": {"name": "reply-teacher", "revision": "v1"},
            "prompt_hash": _hash_of(prompt),
            "role": role,
            "seed": f"{item.get('seed', 0)}:{seed_extra}",
            "messages": [{"role": "system", "content": prompt}],
            # 首试 0.7；重试升温（0.8/0.9）增加输出多样性——seed 是字符串不会
            # 传给 API（pool.py 只传 int），温度是唯一的重试区分手段
            "temperature": 0.7 + 0.1 * max(0, attempt - 1),
            "max_tokens": max_tokens,
        }
        if role == "semantic" and self._semantic_extra_body:
            spec["extra_body"] = self._semantic_extra_body
        return spec


class _ModeFailureSignal(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("reason", "")))
        self.payload = payload


def _failure(error_code: str, retryable: bool, reason: str) -> dict[str, Any]:
    return {
        _MODE_FAILURE: True,
        "error_code": error_code,
        "retryable": retryable,
        "reason": reason,
    }


def _render_facts(facts: list[Any]) -> str:
    if isinstance(facts, str):
        return facts
    return "\n".join(f"- {f}" for f in facts) if facts else "（无）"


def _distill_memory_facts(package_set: dict[str, Any], item: dict[str, Any], count: int = 3) -> list[str]:
    """special 记忆注入（2026-08-14 §24.2）：从 timeline 快照蒸馏可注入事件。

    过滤规则（与 rerank.py:distill_candidates 同口径 + 披露约束）：
    - source_kind == timeline_event、去重、value 非空；
    - visibility_scope == profile_secret 剔除（秘密事件不进生成提示词）；
    - disclosure_policy == withhold 剔除；hint_only 事件只配 desired_policy==hint_only 的 item；
    - item.input.memory_pool 声明时按 source_id 严格过滤（池条目锁定相关事件）；
    - 按 source_id 排序 + seed 起点确定性取前 count 条（同 item 恒定，批次无关）。
    """
    pool = set((item.get("input") or {}).get("memory_pool") or [])
    desired = item.get("desired_policy") or "answer"
    events: list[tuple[str, str]] = []
    seen: set[str] = set()
    for snapshot in (package_set.get("snapshots") or {}).values():
        for unit in snapshot.get("units", []):
            if unit.get("source_kind") != "timeline_event":
                continue
            source_id = str(unit.get("source_id") or "")
            if not source_id or source_id in seen:
                continue
            seen.add(source_id)
            if unit.get("visibility_scope") == "profile_secret":
                continue
            disclosure = unit.get("disclosure_policy") or "direct_allowed"
            if disclosure == "withhold":
                continue
            if disclosure == "hint_only" and desired != "hint_only":
                continue
            if pool and source_id not in pool:
                continue
            value = str(unit.get("value") or "").strip()
            if not value:
                continue
            occurred_at = str(unit.get("occurred_at") or "").strip()
            text = f"[{occurred_at}] {value}" if occurred_at else value
            events.append((source_id, text))
    if not events:
        return []
    events.sort(key=lambda e: e[0])
    seed = item.get("seed", 0)
    start = seed % len(events) if isinstance(seed, int) else 0
    events = events[start:] + events[:start]
    return [text for _, text in events[:count]]


def _bullet(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "（无）"


def _style_contract(
    profile: dict[str, Any],
    resolver: Callable[[str], str] | None = None,
    tics_enabled: bool = True,
) -> str:
    contract = profile.get("style_contract")
    if not isinstance(contract, str) or not contract:
        return "第一人称、口语、自然，不使用 emoji、markdown 或助手腔。"
    if contract.startswith("style:") and resolver is not None:
        return resolver(contract, tics_enabled=tics_enabled)
    return contract


# ── T8（2026-08-06）：口癖降频（P1-1，D3=30%）──
# 口癖段（tics/catchphrases）不再每轮注入：30% 条目注入（确定性，按 plan_id 哈希），
# 其余 70% 要求克制；insufficient/false_premise/conflicted 场景强制克制（严肃/未知保持 0 口癖）。

_TICS_INJECT_RATIO = 30
_RESTRAINED_EVIDENCE = ("insufficient", "false_premise", "conflicted")


def _tics_enabled(item: dict[str, Any]) -> bool:
    """本轮是否注入口癖段（确定性：同 plan_id 恒定，与运行批次无关）。

    2026-08-08（阶段 2）：crc32 对 plan_id（"plan-"+hex）类输入的取模分布存在
    系统性偏置（实测注入率 ~25% 而非 30%），改用 sha256 首 4 字节取模——分布均匀，
    注入率回到 D3=30% 语义。
    """
    if item.get("evidence_state") in _RESTRAINED_EVIDENCE:
        return False
    import hashlib

    digest = int.from_bytes(
        hashlib.sha256(str(item.get("plan_id", "")).encode("utf-8")).digest()[:4], "big"
    )
    return digest % 100 < _TICS_INJECT_RATIO


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_object(text: str, what: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    if cleaned:
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        # T4 容错（2026-08-06）：reasoning 模型偶发在 JSON 前后夹带说明文字，
        # 尝试提取首个 { 到最后一个 } 的平衡子串；仍失败才报 parse_error
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if 0 <= start < end:
            candidate = cleaned[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
    raise _ModeFailureSignal(_failure("parse_error", True, f"{what} 解析失败: {text[:80]!r}"))


def _parse_messages_obj(data: dict) -> list[dict[str, str]]:
    """从合并输出 dict 提取 messages（兼容 messages/conversation/turns/dialogue 键）。"""
    for key in ("messages", "conversation", "turns", "dialogue"):
        if isinstance(data.get(key), list):
            data = data[key]
            break
    if not isinstance(data, list):
        raise _ModeFailureSignal(_failure("parse_error", True, "合并输出缺少消息数组"))
    return _messages_from_list(data)


def _messages_from_list(data: list) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for entry in data:
        if isinstance(entry, str):
            messages.append({"role": "assistant", "content": entry})
            continue
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        text = entry.get("text") or entry.get("content")
        if role not in ("human", "assistant") or not isinstance(text, str) or not text.strip():
            raise _ModeFailureSignal(_failure("parse_error", True, "消息格式非法"))
        messages.append({"role": role, "content": text.strip()})
    if not messages:
        raise _ModeFailureSignal(_failure("parse_error", True, "空消息列表"))
    return messages


_SEMANTIC_AFFIXES = (
    "我今年", "我的", "我是", "我", "今年", "生日是", "名字", "住在", "角色",
    "是", "了", "的", "呢", "啊", "吧", "呀", "嘛", "啦", "在", "就", "还",
    "也", "都", "可", "其实", "不过", "所以", "因为", "然后", "但是",
)


def _proposition_core(proposition: str) -> str:
    """剥离常见词缀得到命题核心（容忍同义改写，如"我今年22岁"→"22岁"）。"""
    core = proposition.strip("。！？!?，, ")
    for affix in _SEMANTIC_AFFIXES:
        core = core.replace(affix, "")
    return core.strip() or proposition


def _default_semantic_check(propositions: list[str], text: str) -> list[str]:
    """默认语义保持检查：命题核心须以字面或任意 3 字片段出现在最终文本中。

    允许风格化改写（"我今年22岁" vs "我22岁"、"客厅等你" vs "在客厅等你"），
    不允许整条命题丢失。
    """
    missing = []
    for proposition in propositions:
        core = _proposition_core(proposition)
        if not core:
            continue
        if core in text:
            continue
        if len(core) >= 3 and any(
            core[i : i + 3] in text for i in range(len(core) - 2)
        ):
            continue
        missing.append(proposition)
    return missing


# ── T7（2026-08-06）：命题对正典校验（P0-5，替换"命题自证"）──
# 第一人称系词结构的命题视为"事实主张"，必须能在 FACTS 中找到支撑：
# 数字锚点（年龄/日期/数字必须出现在正典中）或 ≥2 字公共子串。
# 非事实主张（行为/建议/情绪/疑问）只做自证检查，不做正典校验。

_FACT_CLAIM_PREFIXES = (
    "我是", "我叫", "我今年", "我住在", "我来自", "我有", "我做过", "我去了",
    "我在", "我喜欢", "我讨厌", "我怕", "我生日", "我毕业于", "我学的",
    "我画", "我玩", "我养", "我的", "我住",
    # 2026-08-09（盲区 1）：成长经历类声明也须正典支撑——白未晞小样 18 号
    # "我从小就能在猫和人之间变来变去"（正典是误食妖果后才觉醒化形）漏网。
    # "我一直"不加（"我一直想问你"等非事实主张会被误杀）。
    "我从小", "我小时候",
)

_FACT_CLAIM_PREFIXES = tuple(sorted(_FACT_CLAIM_PREFIXES, key=len, reverse=True))

# P4（2026-08-07）：共同经历编造收紧——"我们"主语的过去时主张必须被正典支撑。
# 评审缺陷：关系/回忆问题输出身份碎片或错误设定（编造共同经历）。
# 现状：grounding 只查"我"前缀，"我们以前…"类命题完全绕过检查。
# correction 池例外（"记忆出入剧本"是池设计意图，允许编造共同小事）。
_WE_CLAIM_MARKERS = (
    "以前", "曾经", "那时候", "小时候", "上次", "那天", "那次",
    "我们约好", "一起去过", "一起去", "我们住过", "我们吃过", "我们玩过",
    "我们说过", "之前", "当年",
)
_WE_CLAIM_ALLOWED_TASKS = ("reply_correction",)


def _we_claim_supported(raw: str, facts: list[str]) -> bool:
    """共同经历主张的支撑检查：纯 bigram 匹配（不能用 _supported_by_facts——
    其中文数字归一化会把"以前"的"一"变"1"，数字锚点误命中正典里的"15-18岁"）。"""
    if len(raw) < 2:
        return False
    return any(
        raw[i : i + 2] in fact
        for i in range(len(raw) - 1)
        for fact in facts
    )

_NUM_RE = re.compile(r"\d+")

# 中文数字 → 阿拉伯（命题可能写"二十二"而正典写 22）
_CN_DIGITS = {"零": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4",
              "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
_CN_UNITS = {"十": 10, "百": 100}


def _cn_number_to_arabic(text: str) -> str:
    """把文本中的中文数字片段转阿拉伯数字（二十二 → 22、十一 → 11）。"""
    result = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in _CN_DIGITS or ch in _CN_UNITS:
            # 收集连续的中文数字段
            j = i
            while j < len(text) and (text[j] in _CN_DIGITS or text[j] in _CN_UNITS):
                j += 1
            seg = text[i:j]
            total = 0
            current = 0
            for c in seg:
                if c in _CN_UNITS:
                    unit = _CN_UNITS[c]
                    current = current or 1
                    total += current * unit
                    current = 0
                else:
                    current = int(_CN_DIGITS[c])
            total += current
            result.append(str(total))
            i = j
        else:
            result.append(ch)
            i += 1
    return "".join(result)


# 语境词（代词/方位/场景）：不是正典事实声明，grounding 检查放行
_CONTEXTUAL_WORDS = (
    "这儿", "这里", "那边", "那里", "楼下", "楼上", "家里", "家", "客厅", "厨房",
    "阳台", "卧室", "玄关", "走廊", "天台", "门口", "楼下超市", "便利店",
)


def _supported_by_facts(core: str, facts: list[str]) -> bool:
    """事实主张的正典支撑：数字锚点必须在 facts 中；否则要求 ≥2 字公共子串
    （remainder 不足 2 字时退化为 1 字子串匹配，如"我怕冷"→"冷"）。
    纯语境词（这儿/客厅等，整词命中列表）放行。"""
    if core in _CONTEXTUAL_WORDS:
        return True
    core_ar = _cn_number_to_arabic(core)
    digits = _NUM_RE.findall(core_ar)
    if digits:
        # 数字锚点必须全部在正典中；命中即视为已支撑（无需再查 bigram，
        # 否则"二十二"→22 虽命中却因中文字面 bigram 缺失被误拒）
        if not any(d in fact for d in digits for fact in facts):
            return False
        return True
    if len(core) >= 2:
        return any(_has_common_bigram(core, fact) for fact in facts)
    # 1 字 remainder：直接子串匹配（如"冷"命中正典"怕冷"）
    return any(core in fact for fact in facts)


def _has_common_bigram(core: str, fact: str) -> bool:
    if len(core) < 2 or len(fact) < 2:
        return False
    for i in range(len(core) - 1):
        if core[i : i + 2] in fact:
            return True
    return False


# 生成期词表（2026-08-07 大块 A）：移入 ProfilePackage（profile.yaml policy_terms），
# core 不内置角色词——未配置 policy_terms 的 profile 不做角色词拦截（通用语义）。


def _check_recitation(
    messages: list[dict[str, str]], task_type: str, terms: dict
) -> list[str]:
    """P3 身份/属性朗读检查：返回命中说明列表（空 = 通过）。

    - 身份声明：非身份问答池，assistant 台词命中身份朗读模式 → 朗读
    - 属性声明：assistant 台词命中属性词，但玩家台词无触发词（话题不相关）→ 朗读；
      玩家台词有触发词时放行（话题相关，合法表达）
    词表来自 ProfilePackage.policy_terms（未配置 = 不拦截）。
    """
    hits: list[str] = []
    allowed_tasks = tuple(terms.get("recitation_allowed_tasks") or ())
    if task_type in allowed_tasks:
        return hits
    assistant_text = " ".join(m["content"] for m in messages if m["role"] == "assistant")
    for pattern in terms.get("identity_recitation_patterns") or ():
        if pattern in assistant_text:
            hits.append(f"身份声明[{pattern}]")
    human_text = " ".join(m["content"] for m in messages if m["role"] == "human")
    has_trigger = any(t in human_text for t in (terms.get("attribute_triggers") or ()))
    if not has_trigger:
        for claim in terms.get("attribute_claims") or ():
            if claim in assistant_text:
                hits.append(f"属性朗读[{claim}]")
    return hits


# 共同经历句式（2026-08-08 编造盲区修复·后置兜底）：第一人称+过去时间词=
# 共同经历暗示。非记忆类任务（correction/vague 之外）不得出现——玩家预设
# "上次/以前"时角色不得顺着补造细节（prompt 已前置约束，此处为兜底硬拦）。
# 2026-08-14（§24.2）：reply_memory 加入允许列表（回忆类任务合法回忆），
# 其支撑由 _check_memory_grounding 单独把关。
# 通用语法模式，非角色词表（v2 禁止事项：不新增角色关键词规则）。
_SHARED_HISTORY_MARKERS = ("上次", "以前", "那天", "上回", "那回", "那一次")
_SHARED_HISTORY_ALLOWED_TASKS = ("reply_correction", "reply_vague", "reply_memory")


def _check_shared_history(
    messages: list[dict[str, str]], task_type: str
) -> list[str]:
    """返回共同经历暗示命中列表（空 = 通过）。记忆类任务放行（记忆话题合法）。"""
    if task_type in _SHARED_HISTORY_ALLOWED_TASKS:
        return []
    assistant_text = " ".join(
        m["content"] for m in messages if m["role"] == "assistant"
    )
    return [
        f"共同经历[{marker}]"
        for marker in _SHARED_HISTORY_MARKERS
        if marker in assistant_text
    ]


# 2026-08-14（§24.2）：特殊记忆守卫——回忆句标记（第一人称过去时/共同经历暗示）。
# 仅 reply_memory 生效：命中标记的句子必须与注入的 MEMORY_FACTS 存在 ≥2 字
# 公共子串；无注入事实时出现回忆标记即拦截（无支撑不得回忆）。
_MEMORY_CLAIM_MARKERS = (
    "上次", "以前", "那天", "那晚", "那夜", "上回", "那回", "那一次",
    "捡到", "救回", "刚来", "头一回", "雨夜",
)


def _check_memory_grounding(
    messages: list[dict[str, str]], memory_facts: list[str], task_type: str
) -> list[str]:
    """特殊记忆守卫：返回回忆无支撑命中列表（空 = 通过）。

    按逗号/句号切分短句（2026-08-14 收紧：逗号连句会让有支撑的片段
    "带回"把无支撑的编造细节"雨很大"一起带过），含回忆标记的短句必须有
    ≥2 字公共子串命中注入的记忆事实（"那晚" → 事实含"暴雨夜"即放行）；
    编造细节（"那天雨下得特别大还打了雷" vs 注入事实无雷）→ 拦截重试。
    非 reply_memory 任务不生效。
    """
    if task_type != "reply_memory":
        return []
    hits: list[str] = []
    for index, message in enumerate(messages):
        if message["role"] != "assistant":
            continue
        for clause in re.split(r"[，。！？!?、；\n]", message["content"]):
            if not any(marker in clause for marker in _MEMORY_CLAIM_MARKERS):
                continue
            if not memory_facts:
                hits.append(f"无注入记忆却回忆[{index}]")
                continue
            supported = any(
                clause[i : i + 2] in fact
                for i in range(len(clause) - 1)
                for fact in memory_facts
            )
            if not supported:
                hits.append(f"回忆无支撑[{index}]")
    return hits


# 2026-08-09：人称纪律兜底（多角色通用，白未晞小样 #5 暴露）
# 模式 1：动物性姿态（窝/趴/蹲/蜷）安到"你"身上——猫系角色常见，玩家几乎不可能
# 被描述为"窝着/趴着/蹲着/蜷着"（除非关心性建议"你先坐会"）。
# 模式 2：角色私有位置（纸箱/箱子/纸盒/猫窝）安到"你"身上。
# 模式 3（2026-08-09 盲区 2）：视角颠倒——玩家问"你怎么在沙发上睡着了"（问角色），
# 角色答"你在这儿睡容易着凉"（把睡眠+位置安到玩家）。玩家台词有"你+睡眠词"
# 触发，assistant 台词有"你+在/到+位置+睡"（把睡眠安到玩家位置）即拦截。
_PRONOUN_POSE_RE = re.compile(r"你[^。！？，,]{0,5}[窝趴蹲蜷][着在过]")
_PRONOUN_PLACE_RE = re.compile(r"你(?:在|到|钻进|缩进)(?:纸箱|箱子|纸盒|猫窝)")
_PLAYER_SLEEP_RE = re.compile(r"你[^。！]{0,10}(睡着|睡了|醒了|醒着|睡觉)")
_ASSISTANT_SLEEP_PLACE_RE = re.compile(r"你(?:在|到)[^。！？]{0,6}(睡着|睡了|睡)")


def _check_pronoun_role(messages: list[dict[str, str]]) -> list[str]:
    """人称纪律检查：返回命中说明列表（空 = 通过）。

    角色把"自己"的姿态/私有位置写到"你"（玩家）身上 → 重试；
    视角颠倒：玩家问角色睡眠状态，角色把"睡在X"安到玩家 → 重试。
    """
    hits: list[str] = []
    human_text = " ".join(
        m["content"] for m in messages if m["role"] == "human"
    )
    player_asks_sleep = bool(_PLAYER_SLEEP_RE.search(human_text))
    for index, message in enumerate(messages):
        if message["role"] != "assistant":
            continue
        text = message["content"]
        if _PRONOUN_POSE_RE.search(text):
            hits.append(f"姿态安到玩家[{index}]")
        if _PRONOUN_PLACE_RE.search(text):
            hits.append(f"私有位置安到玩家[{index}]")
        if player_asks_sleep and _ASSISTANT_SLEEP_PLACE_RE.search(text):
            hits.append(f"视角颠倒：玩家问角色睡眠，角色把睡安到玩家[{index}]")
    return hits


# 2026-08-09（盲区 3）：感知能力编造——用超自然感知断言玩家身体/状态
# （白未晞小样 39 号："我闻到你身上有股疲乏的味道""你额头有点发烫"）。
# 正典能力只有"感知灵气/妖气"；对玩家身体的感知断言（闻到/感知到+你身上/
# 你脸色/你额头）属能力编造。猫系嗅觉的正常表达（"我闻到饭香了"）不拦。
_PERCEPTION_CLAIM_RE = re.compile(
    r"我(?:闻到|嗅到|感知到|感应到|察觉到|闻出|嗅出)"
    r"[^。！？]{0,6}(?:你身上|你的身上|你脸色|你的脸色|你额头|你的额头|你气息|你的气息|你身上有|你身上是)"
)


def _check_perception_claims(messages: list[dict[str, str]]) -> list[str]:
    """感知能力编造检查：返回命中说明列表（空 = 通过）。

    角色用超自然感知（闻到/感知到/感应到等）断言玩家身体/状态 → 重试
    （正典无此能力；玩家未提供的身体状态不得感知断言）。
    """
    hits: list[str] = []
    for index, message in enumerate(messages):
        if message["role"] != "assistant":
            continue
        if _PERCEPTION_CLAIM_RE.search(message["content"]):
            hits.append(f"感知能力编造[{index}]")
    return hits


# 2026-08-10（wants_to_stay 守卫）→ 2026-08-15 设定改写：去留表述纪律。
# 设定（bible wants_to_stay）：她不想离开，用隐晦的话表达想留下
# （"这里……还行""……再说吧"），不说"伤好会走"来掩饰（早期防备期话），
# 也不直白承认"我想留下"。因此两类表述拦截重试：
# ① 决绝离开句（伤好就走/该走了/可能就不在了）——除非带过去框架
#    （"那是以前说的"等；Day 2-3 确实说过"伤好会走"，引用过去合法）；
# ② 直白承认句（其实已经不想走了/我想留下）——目标行为是隐晦表达。
_DECISIVE_LEAVING_RE = re.compile(
    r"(?:伤好[^。！？]{0,10}(?:就走|要走|会走|走了|不在了|不在|离开)"
    r"|等伤好了[^。！？]{0,8}(?:就走|要走|会走|走|不在|不在了|离开)"
    r"|(?:该走了|该离开了|要走了|该回去了|可能就不在了)"
    r"|(?:伤好[^。！？]{0,6}该走|伤好[^。！？]{0,6}该离开)[。！？]?"
    # 2026-08-15（G7 复核 #23 漏网）：非"伤好"字面的去留意向——
    # "等能走了，我就不打扰你了"（能走=伤好能走，不打扰=离开）
    r"|等能走了[^。！？]{0,8}(?:不打扰|就走|离开|不在了|不在)"
    r"|能走(?:了|的时候)[^。！？]{0,8}(?:不打扰|就走|离开|不在了)"
    r"|伤好了(?:以后|之后)[^。！？]{0,8}(?:不打扰|就走|离开))"
)
_DIRECT_STAY_RE = re.compile(
    r"(?:其实(?:已经|我)?不想走|其实我不想走|我已经不想走了|我不想走了|我不想走"
    r"|我不想离开(?:这里)?|我想留下(?:来)?|我想一直留(?:下来)?"
    r"|我真的想留下|想留在这里)"
)
# 过去框架：引用早期（Day 2-3）说过的话时放行——她说的是过去，不是现在要走
_PAST_FRAMING = ("那是之前说的", "那是以前说的", "之前说的", "以前说的",
                 "以前说过", "那时说过", "当时说过", "早期说过", "那会儿说的")


def _check_decisive_leaving(messages: list[dict[str, str]]) -> list[str]:
    """去留表述纪律：返回命中说明列表（空 = 通过）。

    决绝离开句 + 直白承认句 → 拦截重试（教师改写为隐晦表达）；
    决绝句带过去框架（引用早期说过的话）→ 放行。
    """
    hits: list[str] = []
    for index, message in enumerate(messages):
        if message["role"] != "assistant":
            continue
        text = message["content"]
        if _DIRECT_STAY_RE.search(text):
            hits.append(f"直白承认想留下[{index}]")
            continue
        if not _DECISIVE_LEAVING_RE.search(text):
            continue
        if any(marker in text for marker in _PAST_FRAMING):
            continue  # 引用早期说过的话（Day 2-3 确实说过"伤好会走"）
        hits.append(f"决绝离开[{index}]")
    return hits


def _check_safety_action(
    messages: list[dict[str, str]], topic: str, package_set: dict[str, Any]
) -> list[str]:
    """P1 G8 生成侧检查：返回命中说明列表（空 = 通过）。

    阶段 3（C-2）：规则表从 ProtocolBundle（package_set.protocol.safety_*）读取；
    协议未声明安全规则 → 放行（与 G5 词表同语义：配置了才拦）。
    """
    hits: list[str] = []
    protocol = (package_set.get("protocol") or {})
    rules = protocol.get("safety_action_rules") or {}
    topic_keys = protocol.get("safety_topic_keys") or []
    attitude_forbidden = protocol.get("safety_attitude_forbidden") or []
    if not rules:
        return hits
    rule_key = next(
        (key for kw, key in topic_keys if kw in str(topic)), None
    )
    if rule_key is None:
        return hits
    ruleset = rules[rule_key]
    required_words = tuple(ruleset.get("required") or ())
    forbidden_words = tuple(ruleset.get("forbidden") or ())
    assistant_text = " ".join(m["content"] for m in messages if m["role"] == "assistant")

    def _strip_negation(text: str, words: tuple[str, ...]) -> str:
        if not words:
            return text
        pattern = re.compile(
            r"(?:千万不要|千万别|不要|可别|不能|别|不).{0,2}("
            + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
            + ")"
        )
        return pattern.sub("", text)

    required_checked = _strip_negation(assistant_text, required_words)
    if not any(w in required_checked for w in required_words):
        hits.append(f"missing:{rule_key}")
    cleaned = _strip_negation(assistant_text, forbidden_words)
    for word in forbidden_words:
        if word in cleaned:
            hits.append(f"forbidden:{rule_key}:{word}")
    for word in attitude_forbidden:
        if word in assistant_text:
            hits.append(f"attitude:{word}")
    return hits


def _check_grounding(
    propositions: list[str], facts: list[str], task_type: str = "",
    living_claims: tuple[str, ...] = (),
) -> list[str]:
    """命题对正典支撑检查：无支撑的事实主张 → 返回缺失列表（触发 style_failure 重试）。

    事实主张判定：原始命题以第一人称系词前缀开头（词缀剥离会去掉"我今年"等，
    必须在剥离前判断）；随后把该前缀剥离，只对剩余部分做正典支撑检查——
    避免"毕业于"中的"毕业"等通用词被教育类事实误匹配。
    非事实主张（建议/行为/情绪）不做正典校验。
    """
    ungrounded = []
    for proposition in propositions:
        raw = proposition.strip("。！？!?，, ")
        # 禁生活地编造：命中禁生活地表述直接判无支撑（先于正典支撑检查；
        # 词表来自 ProfilePackage.policy_terms.forbidden_living_claims）
        if any(claim in raw for claim in living_claims):
            ungrounded.append(proposition)
            continue
        # P4：共同经历主张（"我们"+过去时标记）必须被正典支撑——correction 池
        # 例外（记忆出入剧本允许编造共同小事）。"我们"不在"我"前缀表里，
        # 历史上完全绕过 grounding。
        if task_type not in _WE_CLAIM_ALLOWED_TASKS and raw.startswith("我们"):
            if any(marker in raw for marker in _WE_CLAIM_MARKERS):
                if not _we_claim_supported(raw, facts):
                    ungrounded.append(f"共同经历无支撑: {proposition}")
                continue
        prefix = next(
            (p for p in _FACT_CLAIM_PREFIXES if raw.startswith(p)), None
        )
        # 特殊情形："我1999年出生" 这类"我"+数字开头，视为事实主张
        if prefix is None and len(raw) > 1 and raw[0] == "我" and raw[1].isdigit():
            prefix = "我"
        if prefix is None:
            continue
        remainder = raw[len(prefix):]
        if not remainder or not _supported_by_facts(remainder, facts):
            ungrounded.append(proposition)
    return ungrounded


def _hash_of(text: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
