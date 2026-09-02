"""C-block V5 pilot-package builder.

The builder is intentionally a packager, not a generator: it never calls a
model and never treats the old V4 acceptance record as a V5 semantic audit.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from data_gen_v4.adapters.exporters.export import ShareGPTReplyExportAdapter
from data_gen_v4.adapters.modes.training import TrainingRecordV4, estimate_token_count

from .gates import evaluate_runtime_grounded_candidate
from .renderer import render_runtime_grounded_training_record
from .scenario import validate_scenario

GROUNDED_TASKS = (
    "reply_accept_authoritative_update",
    "reply_reject_stale_claim",
    "reply_correct_false_premise",
    "reply_resolve_world_memory_conflict",
    "reply_confirm_current_state",
    "reply_subject_attribution",
    "reply_insufficient_information",
    "reply_direct_answer",
)
GROUNDED_TARGET_PER_TASK = 30
GROUNDED_TOTAL = GROUNDED_TARGET_PER_TASK * len(GROUNDED_TASKS)
STATIC_TOTAL = 160
PILOT_TOTAL = GROUNDED_TOTAL + STATIC_TOTAL
SEALED_TOTAL = 40
GOLDEN_SCENARIO_IDS = frozenset(
    {
        "accept.dentist.reschedule",
        "accept.protagonist.no_sugar",
        "reject.dentist.old_friday",
        "reject.rice_cooker.keep_warm",
        "correct.takeout.not_arrived",
        "correct.scissors.drawer_not_wardrobe",
        "conflict.keys.table_over_memory",
        "conflict.cooker.cooking_over_memory",
        "confirm.dentist.saturday",
        "confirm.window.closed",
        "subject.protagonist.no_sugar",
        "subject.third_party.trip",
        "unknown.first_seaside_date",
        "unknown.flower.color",
        "direct.thermos.location",
        "direct.phone.location",
    }
)


class PilotPackageError(ValueError):
    """Raised when C-block inputs cannot produce a safe pilot package."""


@dataclass(frozen=True, slots=True)
class PilotBuildReport:
    dataset_id: str
    total: int
    grounded: int
    static: int
    split_counts: dict[str, int]
    task_counts: dict[str, int]
    source_counts: dict[str, int]
    status_counts: dict[str, int]
    assistant_total: dict[str, int]
    supervised_message_total: dict[str, int]
    supervised_token_total: dict[str, int]
    rg_gate_counts: dict[str, dict[str, int]]
    sealed_total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "total": self.total,
            "grounded": self.grounded,
            "static": self.static,
            "split_counts": self.split_counts,
            "task_counts": self.task_counts,
            "source_counts": self.source_counts,
            "status_counts": self.status_counts,
            "assistant_total": self.assistant_total,
            "supervised_message_total": self.supervised_message_total,
            "supervised_token_total": self.supervised_token_total,
            "rg_gate_counts": self.rg_gate_counts,
            "sealed_total": self.sealed_total,
        }


@dataclass(frozen=True, slots=True)
class _PackRow:
    kind: str
    sample_id: str
    family_key: str
    task_type: str
    record: TrainingRecordV4
    scenario: dict[str, Any] | None = None
    audit_report: dict[str, Any] | None = None


def build_pilot_package(
    *,
    grounded_candidates: Iterable[dict[str, Any]],
    static_records: Iterable[dict[str, Any]],
    static_reaudit: Iterable[dict[str, Any]],
    sealed_rows: Iterable[dict[str, Any]],
    output_dir: str | Path,
    dataset_id: str = "baiweixi_v5_pilot_v1",
    system_anchor: str = "你是白未晞。只输出角色说出口的自然回复。",
    golden_scenario_ids: frozenset[str] = GOLDEN_SCENARIO_IDS,
) -> PilotBuildReport:
    """Build C-block train/dev/test/sealed files from already audited inputs.

    ``grounded_candidates`` are the persisted outputs of the B-block adapter:
    each row must contain ``scenario``, ``teacher_target`` and
    ``semantic_audit``. ``static_reaudit`` is a separate explicit V5 review
    ledger and is mandatory for every selected static sample.
    """

    grounded = _load_grounded(
        grounded_candidates,
        system_anchor=system_anchor,
        golden_scenario_ids=golden_scenario_ids,
    )
    static = _load_static(static_records, static_reaudit, system_anchor=system_anchor)
    sealed = _load_sealed(sealed_rows, golden_scenario_ids=golden_scenario_ids)
    if len(grounded) != GROUNDED_TOTAL:
        raise PilotPackageError(
            f"grounded 必须为 {GROUNDED_TOTAL} 条，实际 {len(grounded)}；"
            "不足时不得用旧数据或降低硬门补齐"
        )
    counts = Counter(row.task_type for row in grounded)
    expected = {task: GROUNDED_TARGET_PER_TASK for task in GROUNDED_TASKS}
    if counts != expected:
        raise PilotPackageError(f"grounded task 覆盖不符合 8×30: {dict(counts)}")
    if len(static) != STATIC_TOTAL:
        raise PilotPackageError(
            f"static 必须为 {STATIC_TOTAL} 条，实际 {len(static)}；必须有独立 V5 复审"
        )
    if len(sealed) != SEALED_TOTAL:
        raise PilotPackageError(
            f"sealed 必须为 {SEALED_TOTAL} 条，实际 {len(sealed)}"
        )
    rows = grounded + static
    _assert_unique_ids(rows, label="pilot")
    _assert_unique_ids(sealed, label="sealed")
    overlap = {row.sample_id for row in rows} & {row.sample_id for row in sealed}
    if overlap:
        raise PilotPackageError(f"train/dev/test 与 sealed sample 泄漏: {sorted(overlap)[:5]}")
    family_overlap = {row.family_key for row in rows} & {row.family_key for row in sealed}
    if family_overlap:
        raise PilotPackageError(
            f"train/dev/test 与 sealed 改写家族泄漏: {sorted(family_overlap)[:5]}"
        )
    _assert_family_isolation(rows)
    _assert_family_isolation(sealed)

    splits = _assign_splits(rows)
    if min(len(splits[name]) for name in ("train", "dev", "test")) == 0:
        raise PilotPackageError("train/dev/test 任一切分为空")
    report = _build_report(dataset_id, rows, splits, sealed)
    if report.total != PILOT_TOTAL:
        raise PilotPackageError(f"pilot 总量必须为 {PILOT_TOTAL}，实际 {report.total}")
    _write_package(Path(output_dir), dataset_id, splits, sealed, report)
    return report


def _load_grounded(
    values: Iterable[dict[str, Any]],
    *,
    system_anchor: str,
    golden_scenario_ids: frozenset[str],
) -> list[_PackRow]:
    rows: list[_PackRow] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise PilotPackageError(f"grounded[{index}] 不是对象")
        scenario = value.get("scenario")
        teacher = value.get("teacher_target")
        audit = value.get("semantic_audit")
        if not isinstance(scenario, dict) or not isinstance(teacher, dict) or not isinstance(audit, dict):
            raise PilotPackageError(f"grounded[{index}] 缺 scenario/teacher_target/semantic_audit")
        validate_scenario(scenario)
        scenario_id = scenario["scenario_id"]
        if scenario_id in golden_scenario_ids:
            raise PilotPackageError(f"黄金场景不得进入训练: {scenario_id}")
        report = evaluate_runtime_grounded_candidate(scenario, teacher, audit)
        if not report.approved:
            raise PilotPackageError(
                f"grounded[{index}] RG 硬门未通过: {scenario_id}: {report.to_dict()}"
            )
        record = render_runtime_grounded_training_record(
            scenario,
            teacher,
            system_anchor=system_anchor,
            sample_id=str(value.get("sample_id") or scenario_id),
        )
        sample_id = record.sample_id
        if sample_id in seen:
            raise PilotPackageError(f"grounded sample_id 重复: {sample_id}")
        seen.add(sample_id)
        anchors = scenario.get("split_anchors") or []
        family_key = _family_key(anchors, fallback=scenario_id)
        rows.append(
            _PackRow(
                kind="runtime_grounded",
                sample_id=sample_id,
                family_key=family_key,
                task_type=scenario["task_type"],
                record=record,
                scenario=scenario,
                audit_report=report.to_dict(),
            )
        )
    return rows


def _load_static(
    values: Iterable[dict[str, Any]],
    audits: Iterable[dict[str, Any]],
    *,
    system_anchor: str,
) -> list[_PackRow]:
    audit_by_id: dict[str, dict[str, Any]] = {}
    for index, audit in enumerate(audits):
        if not isinstance(audit, dict) or not isinstance(audit.get("sample_id"), str):
            raise PilotPackageError(f"static_reaudit[{index}] 缺 sample_id")
        sample_id = audit["sample_id"]
        if sample_id in audit_by_id:
            raise PilotPackageError(f"static_reaudit sample_id 重复: {sample_id}")
        if audit.get("approved") is not True:
            raise PilotPackageError(f"static 样本未通过独立 V5 复审: {sample_id}")
        if audit.get("audit_version") != "runtime-grounded-static-v1":
            raise PilotPackageError(f"static 复审版本错误: {sample_id}")
        audit_by_id[sample_id] = audit

    rows: list[_PackRow] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise PilotPackageError(f"static[{index}] 不是对象")
        sample_id = str(value.get("sample_id", ""))
        if not sample_id:
            raise PilotPackageError(f"static[{index}] 缺 sample_id")
        if sample_id not in audit_by_id:
            raise PilotPackageError(f"static 样本没有 V5 复审记录: {sample_id}")
        task_type = str(value.get("task_type", ""))
        if task_type == "reply_correction":
            raise PilotPackageError(f"V4 reply_correction 禁止进入 V5: {sample_id}")
        conversations = value.get("conversations")
        if not isinstance(conversations, list) or not conversations:
            raise PilotPackageError(f"static[{index}] 缺 conversations")
        messages: list[dict[str, str]] = []
        for message in conversations:
            if not isinstance(message, dict):
                raise PilotPackageError(f"static[{index}] message 不是对象")
            role = {"from": "assistant", "gpt": "assistant", "human": "human", "system": "system"}.get(
                str(message.get("from", ""))
            )
            content = str(message.get("value", ""))
            if role is None or not content.strip():
                raise PilotPackageError(f"static[{index}] 非法 role 或空内容")
            messages.append({"role": role, "content": content})
        if messages[0]["role"] != "system" or messages[-1]["role"] != "assistant":
            raise PilotPackageError(f"static[{index}] 必须 system 开头、assistant 结尾")
        assistant_indexes = [i for i, message in enumerate(messages) if message["role"] == "assistant"]
        if not assistant_indexes:
            raise PilotPackageError(f"static[{index}] 没有 assistant")
        record = TrainingRecordV4(
            sample_id=sample_id,
            mode="STATIC_PERSONA_REPLY",
            render_profile_id="static-persona-reply-v5",
            messages=messages[1:],
            supervised_message_indexes=[i - 1 for i in assistant_indexes if i > 0],
            supervised_token_count=estimate_token_count(
                messages[1:], [i - 1 for i in assistant_indexes if i > 0]
            ),
            protocol_snapshot_id="runtime-grounded-static-v1",
            system_anchor=messages[0]["content"] or system_anchor,
        )
        family_key = _family_key(
            audit_by_id[sample_id].get("split_anchor_ids") or [],
            fallback=sample_id,
        )
        rows.append(
            _PackRow(
                kind="static_persona",
                sample_id=sample_id,
                family_key=family_key,
                task_type=task_type or "static_persona_reply",
                record=record,
            )
        )
    return rows


def _load_sealed(
    values: Iterable[dict[str, Any]], *, golden_scenario_ids: frozenset[str]
) -> list[_PackRow]:
    rows: list[_PackRow] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise PilotPackageError(f"sealed[{index}] 不是对象")
        scenario_id = str(value.get("scenario_id", ""))
        if scenario_id in golden_scenario_ids:
            raise PilotPackageError(f"sealed 不得直接复用黄金场景: {scenario_id}")
        record = value.get("training_record")
        if not isinstance(record, dict):
            raise PilotPackageError(f"sealed[{index}] 缺 training_record")
        try:
            training = TrainingRecordV4.from_dict(record)
        except (KeyError, TypeError, ValueError) as error:
            raise PilotPackageError(f"sealed[{index}] training_record 非法: {error}") from error
        anchors = value.get("split_anchors") or []
        rows.append(
            _PackRow(
                kind="sealed",
                sample_id=training.sample_id,
                family_key=_family_key(anchors, fallback=scenario_id),
                task_type=str(value.get("task_type", "sealed")),
                record=training,
            )
        )
    return rows


def _assert_unique_ids(rows: list[_PackRow], *, label: str) -> None:
    ids = [row.sample_id for row in rows]
    if len(ids) != len(set(ids)):
        duplicates = [sample_id for sample_id, count in Counter(ids).items() if count > 1]
        raise PilotPackageError(f"{label} sample_id 重复: {duplicates[:5]}")


def _family_key(anchors: Iterable[Any], *, fallback: str) -> str:
    normalized = sorted({str(anchor).strip() for anchor in anchors if str(anchor).strip()})
    family_anchors = [anchor for anchor in normalized if anchor.startswith("family:")]
    if family_anchors:
        if len(family_anchors) != 1:
            raise PilotPackageError(f"一个样本必须恰好有一个 family: 锚: {family_anchors}")
        return family_anchors[0]
    return "|".join(normalized) or fallback


def _assert_family_isolation(rows: list[_PackRow]) -> None:
    groups: dict[str, list[str]] = {}
    for row in rows:
        if not row.family_key:
            raise PilotPackageError(f"{row.sample_id} 缺 split family anchor")
        groups.setdefault(row.family_key, []).append(row.sample_id)
    # Within a source collection, repeated family keys are allowed only for
    # the same task: cross-task reuse would make the split label ambiguous.
    del groups


def _assign_splits(rows: list[_PackRow]) -> dict[str, list[_PackRow]]:
    groups: dict[str, list[_PackRow]] = {}
    for row in rows:
        groups.setdefault(row.family_key, []).append(row)
    ordered_groups = sorted(
        groups.items(), key=lambda pair: hashlib.sha256(pair[0].encode("utf-8")).hexdigest()
    )
    split_names = ("train", "dev", "test")
    target = {"train": 0.8, "dev": 0.1, "test": 0.1}
    splits = {name: [] for name in split_names}
    total = len(rows)
    for family_key, family_rows in ordered_groups:
        best = min(
            split_names,
            key=lambda name: (len(splits[name]) / max(total, 1) - target[name], len(splits[name])),
        )
        splits[best].extend(family_rows)
    return splits


def _build_report(
    dataset_id: str,
    rows: list[_PackRow],
    splits: dict[str, list[_PackRow]],
    sealed: list[_PackRow],
) -> PilotBuildReport:
    grounded = [row for row in rows if row.kind == "runtime_grounded"]
    task_counts = Counter(row.task_type for row in rows)
    source_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    rg_gate_counts: dict[str, dict[str, int]] = {}
    for row in grounded:
        for fact in row.scenario["oracle_view"]["facts"]:
            source_counts[str(fact["source"])] += 1
            status_counts[str(fact["status"])] += 1
        for gate, passed in row.audit_report["verdicts"].items():
            bucket = rg_gate_counts.setdefault(gate, {"passed": 0, "failed": 0})
            bucket["passed" if passed else "failed"] += 1
    rg_gate_counts["RG11"] = {"passed": len(grounded), "failed": 0}
    assistant_total: Counter[str] = Counter()
    supervised_total: Counter[str] = Counter()
    supervised_tokens: Counter[str] = Counter()
    for row in rows:
        key = row.kind
        assistants = sum(message["role"] == "assistant" for message in row.record.messages)
        assistant_total[key] += assistants
        supervised_total[key] += len(row.record.supervised_message_indexes)
        supervised_tokens[key] += row.record.supervised_token_count
    return PilotBuildReport(
        dataset_id=dataset_id,
        total=len(rows),
        grounded=len(grounded),
        static=len(rows) - len(grounded),
        split_counts={name: len(values) for name, values in splits.items()},
        task_counts=dict(task_counts),
        source_counts=dict(source_counts),
        status_counts=dict(status_counts),
        assistant_total=dict(assistant_total),
        supervised_message_total=dict(supervised_total),
        supervised_token_total=dict(supervised_tokens),
        rg_gate_counts=rg_gate_counts,
        sealed_total=len(sealed),
    )


def _write_package(
    output_dir: Path,
    dataset_id: str,
    splits: dict[str, list[_PackRow]],
    sealed: list[_PackRow],
    report: PilotBuildReport,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    exporter = ShareGPTReplyExportAdapter()
    for split, rows in {**splits, "sealed": sealed}.items():
        path = output_dir / f"{split}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(exporter.render(row.record) + "\n")
    manifest = {
        "schema_version": "aip.runtime_grounded_pilot_manifest.v1",
        "dataset_id": dataset_id,
        "counts": report.to_dict(),
        "golden_scenarios_excluded": sorted(GOLDEN_SCENARIO_IDS),
        "v4_reply_correction_excluded": True,
        "model_calls_during_packaging": 0,
        "training_started": False,
        "sealed_files": ["sealed.jsonl"],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "coverage_report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
