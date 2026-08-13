"""RerankerJsonlExportAdapter：MEMORY_RERANK TrainingRecord → reranker 记录 JSONL。

对齐 AI 程序侧已冻结
`F:/AiPeople/eval/training_contract/schemas/reranker_record.schema.json`
（`$id: .../memory-reranker-record-v1.json`）的字段约束：每条记录 = (query, 候选记忆)
配对，label 三分类 + relevance_score + should_recall + label_evidence。

多角色通用：character_id / canon_snapshot / split / generation 由调用方经
export_profile 注入（gen_v4 从 profile 包读，不写死）。

一条 TrainingRecord（input=scenario+candidates, target=query+labels）展开为
N 条记录（N = labels 数）。
"""
from __future__ import annotations

import json
from typing import Any

from data_gen_v4.adapters.modes.training import TrainingRecordV4

from .export import SerializedRecord


class RerankerJsonlExportAdapter:
    """MEMORY_RERANK TrainingRecord → reranker_record JSONL 行（每条一配对）。"""

    def render(self, training_record: TrainingRecordV4, export_profile: Any = None) -> str:
        """渲染单条配对记录（label 数组展开为逐条 JSONL）。

        export_profile 必需键：
          character_id（profile_id，多角色）、canon_snapshot（{snapshot_id, sha256}）、
          run_id / generator_version / split（train|dev|test）、scenario_family。
        """
        profile = export_profile or {}
        character_id = profile.get("character_id") or "unknown"
        run_id = profile.get("run_id") or "unknown-run"
        generator_version = profile.get("generator_version") or "aipeople-gen-v4"
        split = profile.get("split") or "train"
        canon = profile.get("canon_snapshot") or {}
        scenario_family = profile.get("scenario_family") or "scenario-default"

        input_doc = json.loads(training_record.messages[0]["content"])
        target_doc = json.loads(training_record.messages[1]["content"])
        query = target_doc.get("query", {})
        query_text = _render_query_text(query)
        candidates = {c["memory_id"]: c for c in input_doc.get("candidates", [])}

        sample_id = training_record.sample_id
        conversation_group_id = sample_id
        leakage_group_id = sample_id
        lines: list[str] = []
        for index, label in enumerate(target_doc.get("labels", [])):
            memory_id = label["memory_id"]
            candidate = candidates.get(memory_id, {})
            record = {
                "sample_id": f"{sample_id}:rr{index}",
                "schema_version": 1,
                "dataset_family": "memory_reranker",
                "character_id": character_id,
                "mode": "MEMORY_RERANK",
                "split": split,
                "conversation_group_id": conversation_group_id,
                "leakage_group_id": leakage_group_id,
                "scenario_family": scenario_family,
                "source_record_ids": candidate.get("evidence_refs") or [memory_id],
                "canon_snapshot": canon,
                "generation": {
                    "run_id": run_id,
                    "generator_version": generator_version,
                    "created_at": profile.get("created_at") or "",
                },
                "content_kind": "ranking_pair",
                "query_id": f"{sample_id}:q",
                "query_text": query_text,
                "candidate_memory_id": memory_id,
                "candidate_memory_text": candidate.get("selector_text") or memory_id,
                "label": label.get("label", "easy_negative"),
                "relevance_score": float(label.get("relevance_score", 0.0)),
                "should_recall": bool(label.get("should_recall", False)),
                "label_evidence": label.get("label_evidence", ""),
            }
            lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        return "\n".join(lines)

    def validate_roundtrip(self, serialized: str) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        for line in serialized.splitlines():
            if not line.strip():
                continue
            doc = json.loads(line)
            required = (
                "sample_id", "schema_version", "dataset_family", "character_id",
                "mode", "split", "conversation_group_id", "leakage_group_id",
                "scenario_family", "source_record_ids", "canon_snapshot",
                "generation", "content_kind", "query_id", "query_text",
                "candidate_memory_id", "candidate_memory_text", "label",
                "relevance_score", "should_recall", "label_evidence",
            )
            for key in required:
                if key not in doc:
                    errors.append({"ok": False, "reason_code": f"missing:{key}"})
            if doc.get("label") not in ("positive", "hard_negative", "easy_negative"):
                errors.append({"ok": False, "reason_code": "bad_label"})
        return errors or [{"ok": True}]


def _render_query_text(query: dict[str, Any]) -> str:
    """query → 可读文本（BGE 编码文本与运行时 render_selector_query_text 的
    逐字对齐留 P1b；此处为 schema 字段级可读形式）。"""
    dialogue = "\n".join(
        f"- role=user; text={d}" for d in query.get("recent_dialogue", [])
    ) or "- （无）"
    return (
        "schema_version=recall-selector-query-v1\n"
        f"current_user_message={query.get('current_user_message', '')}\n"
        "recent_dialogue:\n"
        f"{dialogue}\n"
        f"working_state={query.get('working_state', '')}"
    )
