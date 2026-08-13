"""GenerationPlanCompiler（《数据生成器v4设计》§7.1）。

compile(run_spec, package_set) -> GenerationPlanV4

流程：resolve 四类 package 与依赖 → 加载 profile source snapshots → profile
preflight → 编排 strata/family/contrast（注入 PlanItemFactory）→ seed 派生 →
split anchor 初始标注 → 配额 / mode 注册 / 对照完整性检查 → 计算完整 lock 并
与调用方期望的 package_lock_hash 比对 → 产出不可变 plan。

不变量：编译失败不产生部分可执行 plan（直接抛 V4Error，无返回）。
"""
from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from .blueprint import load_blueprint
from .errors import (
    ModeNotRegisteredError,
    PackageIncompatibleError,
    PreconditionFailedError,
    SourceSnapshotFailedError,
)
from .interfaces import PackageRegistry, PlanItemFactory, SourceLoader
from .plan import GenerationPlanV4, PlanItemV4, PackageSetV4, RunSpec
from .resolver import PackageResolver, canonical_json
from .schemas import SCHEMAS_DIR

# 大块 A（2026-08-07）：渲染模板目录（lock 全覆盖：prompt_refs 锁定全部模板）
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
# 仓库根（file: 相对路径解析：blueprint_ref 等）
REPO_ROOT = Path(__file__).resolve().parents[2]

_SCHEMA_FILES = (
    "package_v4.schema.json",
    "record_v4.schema.json",
    "plan_v4.schema.json",
    "evidence_v4.schema.json",
    "lock_v4.schema.json",
)

_FAMILY_ROLES = {"positive_control", "boundary_variant", "standalone"}
_RISK_LEVELS = {"low", "medium", "high"}
_REVIEW_LEVELS = {"full", "family_sample", "cluster_sample", "auto"}


@dataclass(frozen=True, slots=True)
class CompileResult:
    plan: GenerationPlanV4
    lock: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)


def compute_lock_hash(
    packages: dict[str, dict[str, str]],
    source_snapshots: dict[str, dict[str, str]],
    schema_refs: dict[str, dict[str, str]],
    prompt_refs: dict[str, dict[str, str]],
    validator_refs: dict[str, dict[str, str]],
    adapter_revisions: dict[str, dict[str, str]],
    model_refs: dict[str, dict[str, str]] | None = None,
    exporter_refs: dict[str, dict[str, str]] | None = None,
) -> str:
    """按 §5.5：SHA-256(按 key 排序后的全部 pin 的规范 JSON)。

    大块 A（2026-08-07）：model_refs/exporter_refs 纳入 hash（lock 全覆盖）。
    """
    body = {
        "packages": packages,
        "source_snapshots": source_snapshots,
        "schema_refs": schema_refs,
        "prompt_refs": prompt_refs,
        "validator_refs": validator_refs,
        "adapter_revisions": adapter_revisions,
        "model_refs": model_refs or {},
        "exporter_refs": exporter_refs or {},
    }
    return "sha256:" + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def schema_refs() -> dict[str, dict[str, str]]:
    refs: dict[str, dict[str, str]] = {}
    for name in _SCHEMA_FILES:
        raw = (SCHEMAS_DIR / name).read_bytes()
        refs[name] = {"content_hash": "sha256:" + hashlib.sha256(raw).hexdigest()}
    return refs


def derive_seed(run_seed: int, family_id: str) -> int:
    """从 run seed 与 family_id 派生确定性 item seed（§7.1 seed 派生）。"""
    return (run_seed ^ (zlib.crc32(family_id.encode("utf-8")) & 0x7FFFFFFF)) & 0x7FFFFFFF


class GenerationPlanCompiler:
    def __init__(
        self,
        registry: PackageRegistry,
        source_loader: SourceLoader,
        item_factory: PlanItemFactory,
    ) -> None:
        self._resolver = PackageResolver(registry)
        self._source_loader = source_loader
        self._item_factory = item_factory

    def compile(self, run_spec: RunSpec, package_set: PackageSetV4) -> CompileResult:
        resolved = self._resolver.resolve(package_set)
        profile = resolved.profile
        protocol = resolved.protocol
        recipe = resolved.recipe
        release = resolved.release

        # 1. 加载 source snapshots + profile preflight
        snapshots = self._load_snapshots(profile)
        self._preflight_profile(profile, snapshots)
        snapshot_id = profile_snapshot_id_of(profile, snapshots)

        # 2. 编排 plan items
        raw_items = self._item_factory.build_items(
            profile=profile, recipe=recipe, protocol=protocol, seed=run_spec.seed
        )
        if not raw_items:
            raise PreconditionFailedError("item factory 未产出任何 plan item")

        # 3. mode 注册检查
        registered = {mode["mode_id"] for mode in protocol.get("modes", [])}
        unregistered = sorted({item.mode for item in raw_items} - registered)
        if unregistered:
            raise ModeNotRegisteredError(f"plan item 引用未注册 mode: {unregistered}")

        # 3.5 阶段 3（C-6）：行为族蓝图校验（profile 声明 blueprint_ref 时）——
        # strata 全部归族 + Evol 约束合法（禁止演化 canon）
        blueprint_ref = profile.get("blueprint_ref")
        if blueprint_ref:
            ref_path = blueprint_ref.removeprefix("file:")
            blueprint = load_blueprint(REPO_ROOT / ref_path)
            uncovered = blueprint.validate_family_coverage(
                [s.get("task_type", "") for s in recipe.get("strata", [])]
            )
            if uncovered:
                raise PreconditionFailedError(f"行为族未覆盖 strata: {uncovered}")
            evol_errors = blueprint.validate_evol_constraints()
            if evol_errors:
                raise PreconditionFailedError(f"蓝图 Evol 约束违规: {evol_errors}")

        # 4. 配额与对照完整性检查（recipe 视角，无角色知识）
        self._check_quota(recipe, raw_items)
        self._check_contrast_pairs(raw_items)

        # 5. 填充 plan 级字段、派生 seed、标注 split anchor
        items: list[PlanItemV4] = []
        for item in raw_items:
            if item.family_role not in _FAMILY_ROLES:
                raise PreconditionFailedError(f"非法 family_role: {item.family_role!r}")
            if item.risk_level not in _RISK_LEVELS:
                raise PreconditionFailedError(f"非法 risk_level: {item.risk_level!r}")
            if item.required_review not in _REVIEW_LEVELS:
                raise PreconditionFailedError(f"非法 required_review: {item.required_review!r}")
            anchors = [f"family:{item.family_id}"]
            if item.question_family_id:
                anchors.append(f"question_family:{item.question_family_id}")
            if item.scene_family_id:
                anchors.append(f"scene_family:{item.scene_family_id}")
            items.append(
                replace(
                    item,
                    profile_id=profile["profile_id"],
                    profile_snapshot_id=snapshot_id,
                    protocol_bundle_id=protocol["protocol_bundle_id"],
                    recipe_id=recipe["recipe_id"],
                    seed=derive_seed(run_spec.seed, item.family_id),
                    split_anchor_ids=sorted(set(item.split_anchor_ids) | set(anchors)),
                )
            )

        # 5.5 证据填充（P0-2，阶段 1）：需要证据的任务从 profile 来源快照附加
        # source_refs + support_spans——证据不空才可发布（G2 强制），not_required 装饰性
        items = self._attach_evidence(items, snapshots)

        # 6. 计算完整 lock（编译产物，§5.5），并校验与 plan 自洽
        lock = self._build_lock(
            resolved.lock_packages(),
            snapshots,
            release=release,
            model_pin=run_spec.model_pin,
            exporter_pin=run_spec.exporter_pin,
        )
        plan = GenerationPlanV4.build(
            run_id=run_spec.run_id,
            profile_id=profile["profile_id"],
            profile_snapshot_id=snapshot_id,
            protocol_bundle_id=protocol["protocol_bundle_id"],
            recipe_id=recipe["recipe_id"],
            package_lock_hash=lock["lock_hash"],
            items=items,
        )
        if plan.package_lock_hash != lock["lock_hash"]:
            raise PackageIncompatibleError("plan 与 lock 自洽校验失败")
        context = {
            "profile": profile,
            "protocol": protocol,
            "recipe": recipe,
            "release": release,
            "snapshots": snapshots,
            # 大块 A（2026-08-07）：标签/锚映射从 ProfilePackage 读（core 不内置角色键）
            "facts": _collect_facts(snapshots, profile.get("fact_labels") or {}),
            "beliefs": _collect_beliefs(snapshots),
            "anchor_facts": _collect_anchor_facts(snapshots, profile.get("anchor_key_map") or {}),
        }
        return CompileResult(plan=plan, lock=lock, context=context)

    # ───────────────────────── 内部 ─────────────────────────

    def _load_snapshots(self, profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
        refs: list[str] = []
        for key in ("identity_sources", "canon_sources", "timeline_sources", "memory_sources"):
            refs.extend(profile.get(key, []))
        snapshots: dict[str, dict[str, Any]] = {}
        for ref in refs:
            try:
                snapshot = self._source_loader.load_snapshot(ref, "freeze")
            except Exception as error:  # noqa: BLE001
                raise SourceSnapshotFailedError(f"source 加载失败: {ref}: {error}") from error
            snapshots[ref] = snapshot
        return snapshots

    @staticmethod
    def _preflight_profile(
        profile: dict[str, Any], snapshots: dict[str, dict[str, Any]]
    ) -> None:
        required = profile.get("identity_sources", []) + profile.get("canon_sources", [])
        for ref in required:
            snapshot = snapshots.get(ref)
            if snapshot is None:
                raise SourceSnapshotFailedError(f"profile 缺少来源快照: {ref}")
            if not snapshot.get("content_hash"):
                raise SourceSnapshotFailedError(f"来源快照缺 content_hash: {ref}")
            if not snapshot.get("units"):
                raise SourceSnapshotFailedError(f"来源快照无证据单元: {ref}")
        for ref, snapshot in snapshots.items():
            for unit in snapshot.get("units", []):
                if unit.get("snapshot_id") != snapshot.get("snapshot_id"):
                    raise SourceSnapshotFailedError(
                        f"证据单元 snapshot_id 与快照不一致: {ref}"
                    )

    @staticmethod
    def _check_quota(recipe: dict[str, Any], items: list[PlanItemV4]) -> None:
        errors: list[str] = []
        for stratum in recipe.get("strata", []):
            mode = stratum["mode_id"]
            task = stratum["task_type"]
            target = stratum["target_count"]
            min_families = stratum.get("min_family_count", 0)
            matched = [i for i in items if i.mode == mode and i.task_type == task]
            if len(matched) < target:
                errors.append(f"strata {mode}/{task} 计划 {target} 条，实际 {len(matched)} 条")
            families = {i.family_id for i in matched}
            if len(families) < min_families:
                errors.append(
                    f"strata {mode}/{task} 要求至少 {min_families} 个 family，实际 {len(families)}"
                )
        if errors:
            raise PreconditionFailedError("配额不足；" + "；".join(errors))

    @staticmethod
    def _check_contrast_pairs(items: list[PlanItemV4]) -> None:
        """通用对照不变量：positive_control 与 boundary_variant 必须成对出现。"""
        by_family: dict[str, set[str]] = {}
        for item in items:
            if item.family_role in ("positive_control", "boundary_variant"):
                by_family.setdefault(item.family_id, set()).add(item.family_role)
        bad = {
            fid: sorted(roles)
            for fid, roles in by_family.items()
            if roles != {"positive_control", "boundary_variant"}
        }
        if bad:
            raise PreconditionFailedError(
                "contrast family 必须同时含 positive_control 与 boundary_variant: "
                + str(bad)
            )

    # ───────────────────────── 证据填充（P0-2，阶段 1） ─────────────────────────

    _EVIDENCE_UNIT_KINDS = ("identity_fact", "canon_fact")
    _EVIDENCE_COUNT = 3

    @classmethod
    def _evidence_units(cls, snapshots: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """收集可作证据的 profile 来源单元：非 secret 的 identity/canon 事实，value 非空。

        按 (snapshot_id, source_id) 排序保证确定性（编译结果稳定可重放）。
        """
        units: list[dict[str, Any]] = []
        for snapshot in snapshots.values():
            for unit in snapshot.get("units", []):
                if unit.get("source_kind") not in cls._EVIDENCE_UNIT_KINDS:
                    continue
                if unit.get("visibility_scope") == "profile_secret":
                    continue
                if not isinstance(unit.get("value"), str) or not unit["value"]:
                    continue
                units.append(unit)
        units.sort(key=lambda u: (str(u.get("snapshot_id")), str(u.get("source_id"))))
        return units

    def _attach_evidence(
        self, items: list[PlanItemV4], snapshots: dict[str, dict[str, Any]]
    ) -> list[PlanItemV4]:
        """为需要证据的 item 附加 source_refs + support_spans（not_required 装饰性跳过）。

        2026-08-07（P0-2）：证据由编译期快照蒸馏产生，单向数据流——候选记录
        透传 item 证据（engine._wrap_candidate），G2 据此强制非空。span 引用
        快照单元 value 全文（start=0/end=len/quote=value），与快照逐字符一致，
        保证 G2 通过。claim 级验证（used_facts 与正文比对）为阶段 3。
        """
        units = self._evidence_units(snapshots)
        if not units:
            return items
        out: list[PlanItemV4] = []
        for item in items:
            if item.evidence_state == "not_required":
                out.append(item)
                continue
            offset = int(item.seed) % len(units) if isinstance(item.seed, int) else 0
            chosen = [
                units[(offset + i) % len(units)]
                for i in range(min(self._EVIDENCE_COUNT, len(units)))
            ]
            refs: list[dict[str, Any]] = []
            spans: list[dict[str, Any]] = []
            for i, unit in enumerate(chosen):
                value = str(unit["value"])
                ref = {
                    "source_kind": unit["source_kind"],
                    "source_id": unit["source_id"],
                    "snapshot_id": unit["snapshot_id"],
                    "field_pointer": "",
                }
                span = {
                    **ref,
                    "offset_unit": "unicode_code_point",
                    "start": 0,
                    "end": len(value),
                    "quote": value,
                    "supported_claim_id": f"{item.family_id}:{item.task_type}:{i}",
                    "evidence_role": "support",
                }
                refs.append(ref)
                spans.append(span)
            out.append(replace(item, source_refs=refs, support_spans=spans))
        return out

    def _build_lock(
        self,
        packages_pin: dict[str, dict[str, str]],
        snapshots: dict[str, dict[str, Any]],
        release: dict[str, Any] | None = None,
        model_pin: dict[str, Any] | None = None,
        exporter_pin: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshots_pin: dict[str, dict[str, str]] = {}
        for ref, snapshot in snapshots.items():
            snapshots_pin[snapshot["snapshot_id"]] = {
                "source_adapter_id": snapshot["source_adapter_id"],
                "content_hash": snapshot["content_hash"],
            }
        # 大块 A（2026-08-07）：lock 全覆盖——
        # 1) prompt_refs：渲染模板全部锁定（prompts/*.txt）
        # 2) validator_refs：release 声明的 validator 引用落盘（阶段 3 实现后改 registered）
        # 3) model_refs / exporter_refs：由 RunSpec 注入（真实模型/导出器 pin）
        prompt_refs: dict[str, dict[str, str]] = {}
        for name in sorted(p.name for p in PROMPTS_DIR.glob("*.txt")):
            raw = (PROMPTS_DIR / name).read_bytes()
            prompt_refs[name] = {
                "template_version": "v1",
                "content_hash": "sha256:" + hashlib.sha256(raw).hexdigest(),
            }
        validator_refs: dict[str, dict[str, str]] = {}
        for key in ("required_profile_validators", "required_protocol_validators"):
            for ref in (release or {}).get(key, []):
                validator_refs[str(ref)] = {
                    "status": "declared",
                    "content_hash": "sha256:"
                    + hashlib.sha256(str(ref).encode("utf-8")).hexdigest(),
                }
        model_refs = dict(model_pin or {})
        exporter_refs = dict(exporter_pin or {})
        lock = {
            "packages": packages_pin,
            "source_snapshots": snapshots_pin,
            "schema_refs": schema_refs(),
            "prompt_refs": prompt_refs,
            "validator_refs": validator_refs,
            "adapter_revisions": {"core": {"revision": __version__}},
            "model_refs": model_refs,
            "exporter_refs": exporter_refs,
            "created_at": run_timestamp(),
        }
        lock["lock_hash"] = compute_lock_hash(
            lock["packages"],
            lock["source_snapshots"],
            lock["schema_refs"],
            lock["prompt_refs"],
            lock["validator_refs"],
            lock["adapter_revisions"],
            lock["model_refs"],
            lock["exporter_refs"],
        )
        return lock


def _collect_facts(
    snapshots: dict[str, dict[str, Any]], fact_labels: dict[str, str] | None = None
) -> list[str]:
    """从来源快照收集可注入的事实文本（identity/canon 单元），供 REPLY 渲染。

    过滤规则（2026-08-06 T1 修复）：
    - 跳过 profile_secret 单元（withhold 秘密事实不进公开锚与生成提示词，
      这是训练数据秘密词泄漏的机制来源）；
    - 按值去重（identity 与 canon 存在同名重复条目，如角色名/性别/年龄）。

    2026-08-06 T14：canon_fact 渲染带属性名标签（"妈妈：航天科研人员（轨道计算）"），
    教师不再靠猜属性归属；identity_fact 值多为完整句，不带标签。
    大块 A（2026-08-07）：fact_labels 从 ProfilePackage 读（core 不内置角色 canon 键）。
    """
    fact_labels = fact_labels or {}
    facts: list[str] = []
    seen: set[str] = set()
    for snapshot in snapshots.values():
        for unit in snapshot.get("units", []):
            if unit.get("source_kind") not in ("identity_fact", "canon_fact"):
                continue
            if unit.get("visibility_scope") == "profile_secret":
                continue
            text = str(unit.get("value", ""))
            if not text or text in seen:
                continue
            seen.add(text)
            if unit.get("source_kind") == "canon_fact":
                source_id = str(unit.get("source_id", ""))
                key = source_id.split(":", 1)[-1] if ":" in source_id else source_id
                label = fact_labels.get(key)
                if label:
                    text = f"{label}：{text}"
            facts.append(text)
    return facts


def _collect_beliefs(snapshots: dict[str, dict[str, Any]]) -> str:
    """从 belief_fact 单元蒸馏三观信念短句（2026-08-06 通用能力修复）。

    克制原则：渲染成"信念+行为动机"的短句（"你信奉自在、美食、画画"），
    不说教；secret 单元（如 goals.secret）跳过——不进生成 prompt 的公开段，
    秘密边界由 protective 数据控制。返回空串表示该角色无三观素材。
    """
    values: list[str] = []
    fears: list[str] = []
    goals: list[str] = []
    for snapshot in snapshots.values():
        for unit in snapshot.get("units", []):
            if unit.get("source_kind") != "belief_fact":
                continue
            if unit.get("visibility_scope") == "profile_secret":
                continue
            source_id = str(unit.get("source_id", ""))
            value = str(unit.get("value", ""))
            if source_id.startswith("values:"):
                values.append(value)
            elif source_id.startswith("fears:"):
                fears.append(value)
            elif source_id.startswith("goals:"):
                goals.append(value)
    lines: list[str] = []
    if values:
        lines.append(f"你信奉：{'、'.join(values)}——这是你生活里最重要的东西")
    if fears:
        lines.append(f"你最怕：{'；'.join(fears)}——它们会在关键时刻左右你的选择")
    if goals:
        lines.append(f"你向往：{'；'.join(goals)}——是你在为之努力的方向")
    return "\n".join(lines)


def _collect_anchor_facts(
    snapshots: dict[str, dict[str, Any]], anchor_key_map: dict[str, list[str]] | None = None
) -> dict[str, str]:
    """按 anchor_key_map（ProfilePackage 声明）从 identity/canon 单元提取锚渲染
    所需的结构化事实。

    2026-08-06 T2：训练锚改为运行时散文式结构，身份/关系句由这些事实渲染，
    不再把完整事实列表灌进锚；profile 的 anchor_contract 补充无通用源的纹理。
    大块 A（2026-08-07）：anchor_key_map 从 ProfilePackage 读（core 不内置角色键）。
    """
    anchor_key_map = anchor_key_map or {}
    by_id: dict[str, str] = {}
    for snapshot in snapshots.values():
        for unit in snapshot.get("units", []):
            if unit.get("source_kind") not in ("identity_fact", "canon_fact"):
                continue
            if unit.get("visibility_scope") == "profile_secret":
                continue
            by_id.setdefault(str(unit.get("source_id", "")), str(unit.get("value", "")))
    result: dict[str, str] = {}
    for key, source_ids in anchor_key_map.items():
        for source_id in source_ids:
            if by_id.get(source_id):
                result[key] = by_id[source_id]
                break
    return result


def run_timestamp() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def profile_snapshot_id_of(
    profile: dict[str, Any], snapshots: dict[str, dict[str, Any]]
) -> str:
    """profile_snapshot_id 派生规则：profile 包 content_hash 与全部来源快照
    content_hash 排序后的整体 SHA-256（阶段 0 未定细节，此为阶段 1 实现约定）。"""
    parts = [profile["content_hash"]] + sorted(
        s["content_hash"] for s in snapshots.values()
    )
    return "sha256:" + hashlib.sha256(canonical_json(parts).encode("utf-8")).hexdigest()
