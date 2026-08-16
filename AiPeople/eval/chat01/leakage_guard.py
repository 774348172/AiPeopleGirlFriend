from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "eval" / "chat01" / "suites" / "leakage_report_v1.json"
TRAINING_EXCLUSION_PATH = ROOT / "training_package_m3_v2" / "eval_exclusions" / "chat01_v1.json"
EVALUATION_PATHS = (
    "eval/chat01/suites/chat01_dev_v1.jsonl",
    "eval/chat01/suites/chat01_frozen_single_v1.jsonl",
    "eval/chat01/suites/chat01_frozen_multiturn_v1.jsonl",
    "eval/chat01/suites/chat01_human_blind_v1.jsonl",
)
DEFAULT_TRAINING_PATTERNS = (
    "data/life_corpus/qwx*.jsonl",
    "data/sft/qwx*.jsonl",
    "training_package_m3/data/train.jsonl",
    "training_package_m3/data/valid.jsonl",
    "training_package_m3_v2/data/train.jsonl",
    "training_package_m3_v2/data/valid.jsonl",
)
DEFAULT_EXCLUDED_PATTERNS = (
    "eval/chat01/**",
    "人物设定/秦/训练数据/_archive_*/**",
    "training_package_m3_v2/data/legacy/**",
    "历史与调研文档/**",
)
NEAR_DUPLICATE_THRESHOLD = 0.82
NEAR_DUPLICATE_MIN_CHARS = 12
QWEN_USER_RE = re.compile(
    r"<\|im_start\|>user\s*\n(.*?)<\|im_end\|>", re.DOTALL
)


@dataclass(frozen=True)
class TextRef:
    path: str
    record_id: str
    line: int | None
    text: str

    def json_ref(self) -> dict[str, Any]:
        preview = re.sub(r"\s+", " ", self.text).strip()[:240]
        return {
            "path": self.path,
            "record_id": self.record_id,
            "line": self.line,
            "text_sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
            "preview": preview,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_for_leakage(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return "".join(character for character in normalized if character.isalnum())


def _trigrams(text: str) -> set[str]:
    if len(text) < 3:
        return {text} if text else set()
    return {text[index : index + 3] for index in range(len(text) - 2)}


def trigram_jaccard(first: str, second: str) -> float:
    first_grams = _trigrams(first)
    second_grams = _trigrams(second)
    if not first_grams or not second_grams:
        return 0.0
    return len(first_grams & second_grams) / len(first_grams | second_grams)


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _read_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"expected object at {path}:{line_number}")
        rows.append((line_number, value))
    if not rows:
        raise ValueError(f"training or evaluation source is empty: {path}")
    return rows


def _evaluation_texts(path: Path) -> tuple[list[TextRef], int]:
    relative = _relative(path)
    refs: list[TextRef] = []
    rows = _read_jsonl(path)
    for line_number, case in rows:
        case_id = str(case["case_id"])
        if "messages" in case:
            for index, message in enumerate(case["messages"], 1):
                if message["role"] == "user":
                    refs.append(TextRef(relative, f"{case_id}.message-{index}", line_number, message["content"].strip()))
        if "turns" in case:
            for turn in case["turns"]:
                refs.append(TextRef(relative, f"{case_id}.{turn['turn_id']}", line_number, turn["user_message"].strip()))
        if "human_brief" in case:
            refs.append(TextRef(relative, f"{case_id}.opening", line_number, case["human_brief"]["opening"].strip()))
            for index, constraint in enumerate(case["human_brief"]["constraints"], 1):
                refs.append(TextRef(relative, f"{case_id}.constraint-{index}", line_number, constraint.strip()))
    return refs, len(rows)


def _append_text(values: list[str], candidate: Any) -> None:
    if isinstance(candidate, str) and candidate.strip():
        values.append(candidate.strip())


def extract_training_user_texts(record: dict[str, Any]) -> list[str]:
    values: list[str] = []
    conversations = record.get("conversations")
    if isinstance(conversations, list):
        for message in conversations:
            if not isinstance(message, dict):
                continue
            role = str(message.get("from", message.get("role", ""))).lower()
            if role in {"human", "user", "other"}:
                _append_text(values, message.get("value", message.get("content", message.get("text"))))

    messages = record.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict) and str(message.get("role", "")).lower() in {"human", "user"}:
                _append_text(values, message.get("content", message.get("value")))

    content = record.get("content")
    if isinstance(content, list):
        for message in content:
            if isinstance(message, dict) and str(message.get("role", "")).lower() in {"human", "user", "other"}:
                _append_text(values, message.get("text", message.get("content", message.get("value"))))

    rendered = record.get("text")
    if isinstance(rendered, str):
        for match in QWEN_USER_RE.findall(rendered):
            _append_text(values, match)

    for key in ("instruction", "input", "prompt", "query", "question"):
        _append_text(values, record.get(key))
    return list(dict.fromkeys(values))


def _training_texts(path: Path) -> tuple[list[TextRef], int]:
    relative = _relative(path)
    refs: list[TextRef] = []
    rows = _read_jsonl(path)
    for line_number, record in rows:
        record_id = str(record.get("id", record.get("sample_id", f"line-{line_number}")))
        for index, text in enumerate(extract_training_user_texts(record), 1):
            refs.append(TextRef(relative, f"{record_id}.user-{index}", line_number, text))
    return refs, len(rows)


def resolve_training_paths(
    patterns: Iterable[str] = DEFAULT_TRAINING_PATTERNS,
    excluded_patterns: Iterable[str] = DEFAULT_EXCLUDED_PATTERNS,
) -> list[Path]:
    excludes = tuple(excluded_patterns)
    paths: dict[str, Path] = {}
    for pattern in patterns:
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            if path.stat().st_size == 0:
                continue
            relative = _relative(path)
            if relative.startswith("eval/chat01/"):
                raise ValueError(f"evaluation asset cannot be a training source: {relative}")
            if any(fnmatch(relative, excluded) for excluded in excludes):
                continue
            paths[relative] = path
    if not paths:
        raise ValueError("no training sources matched the leakage scan contract")
    return [paths[key] for key in sorted(paths)]


def _source_entry(path: Path, records: int, extracted_texts: int) -> dict[str, Any]:
    return {
        "path": _relative(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "records": records,
        "extracted_texts": extracted_texts,
    }


def _finding(kind: str, similarity: float, evaluation: TextRef, training: TextRef) -> dict[str, Any]:
    key = "\x1f".join((kind, evaluation.path, evaluation.record_id, training.path, training.record_id))
    finding_id = "leak." + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return {
        "finding_id": finding_id,
        "kind": kind,
        "similarity": round(similarity, 6),
        "evaluation": evaluation.json_ref(),
        "training": training.json_ref(),
    }


def scan_leakage(
    *,
    training_patterns: Iterable[str] = DEFAULT_TRAINING_PATTERNS,
    excluded_patterns: Iterable[str] = DEFAULT_EXCLUDED_PATTERNS,
    near_duplicate_threshold: float = NEAR_DUPLICATE_THRESHOLD,
    near_duplicate_min_chars: int = NEAR_DUPLICATE_MIN_CHARS,
) -> dict[str, Any]:
    evaluation_refs: list[TextRef] = []
    evaluation_sources: list[dict[str, Any]] = []
    for relative in EVALUATION_PATHS:
        path = ROOT / relative
        refs, records = _evaluation_texts(path)
        evaluation_refs.extend(refs)
        evaluation_sources.append(_source_entry(path, records, len(refs)))

    training_refs: list[TextRef] = []
    training_sources: list[dict[str, Any]] = []
    training_records = 0
    for path in resolve_training_paths(training_patterns, excluded_patterns):
        refs, records = _training_texts(path)
        training_refs.extend(refs)
        training_records += records
        training_sources.append(_source_entry(path, records, len(refs)))

    exact_index: dict[str, TextRef] = {}
    normalized_index: dict[str, TextRef] = {}
    for ref in training_refs:
        exact_index.setdefault(ref.text.strip(), ref)
        normalized = normalize_for_leakage(ref.text)
        if normalized:
            normalized_index.setdefault(normalized, ref)

    unique_training = list(normalized_index.items())
    findings: list[dict[str, Any]] = []
    for evaluation in evaluation_refs:
        exact = exact_index.get(evaluation.text.strip())
        if exact is not None:
            findings.append(_finding("exact", 1.0, evaluation, exact))
            continue
        normalized = normalize_for_leakage(evaluation.text)
        normalized_match = normalized_index.get(normalized)
        if normalized and normalized_match is not None:
            findings.append(_finding("normalized", 1.0, evaluation, normalized_match))
            continue
        if len(normalized) < near_duplicate_min_chars:
            continue
        best_ref: TextRef | None = None
        best_score = 0.0
        for training_normalized, training in unique_training:
            if len(training_normalized) < near_duplicate_min_chars:
                continue
            length_ratio = min(len(normalized), len(training_normalized)) / max(len(normalized), len(training_normalized))
            if length_ratio < 0.7:
                continue
            score = trigram_jaccard(normalized, training_normalized)
            if score > best_score:
                best_score = score
                best_ref = training
        if best_ref is not None and best_score >= near_duplicate_threshold:
            findings.append(_finding("near_duplicate", best_score, evaluation, best_ref))

    counts = {kind: sum(1 for finding in findings if finding["kind"] == kind) for kind in ("exact", "normalized", "near_duplicate")}
    return {
        "report_id": "chat01-leakage-v1",
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "evaluation_sources": evaluation_sources,
        "training_sources": training_sources,
        "algorithms": {
            "exact": "utf8_text_equality",
            "normalized": "nfkc_lower_alnum_cjk",
            "near_duplicate": "character_trigram_jaccard",
            "near_duplicate_threshold": near_duplicate_threshold,
            "near_duplicate_min_chars": near_duplicate_min_chars,
            "normalization": "Unicode NFKC, lowercase, retain only Unicode alphanumeric/CJK characters.",
        },
        "findings": findings,
        "summary": {
            "evaluation_texts": len(evaluation_refs),
            "training_records": training_records,
            "training_user_texts": len(training_refs),
            "exact": counts["exact"],
            "normalized": counts["normalized"],
            "near_duplicate": counts["near_duplicate"],
            "blocking_findings": len(findings),
        },
        "status": "blocked" if findings else "passed",
    }


def write_report(report: dict[str, Any], path: Path = REPORT_PATH) -> None:
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_training_exclusion_hashes(
    path: Path = TRAINING_EXCLUSION_PATH,
) -> dict[str, Any]:
    normalized_hashes: set[str] = set()
    for relative in EVALUATION_PATHS:
        refs, _records = _evaluation_texts(ROOT / relative)
        for ref in refs:
            normalized = normalize_for_leakage(ref.text)
            if normalized:
                normalized_hashes.add(
                    hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                )
    payload = {
        "contract_id": "chat01-training-exclusions-v1",
        "schema_version": 1,
        "hash_algorithm": "sha256",
        "normalization": "nfkc_lower_alnum_cjk",
        "source_suite": "chat01-qinweixi-v1",
        "normalized_text_sha256": sorted(normalized_hashes),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan CHAT-01 cases against active Qin Weixi training inputs.")
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--write-training-exclusions", action="store_true")
    args = parser.parse_args()
    if args.write_training_exclusions:
        exclusions = write_training_exclusion_hashes()
        print(
            json.dumps(
                {"training_exclusion_hashes": len(exclusions["normalized_text_sha256"])},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    report = scan_leakage()
    write_report(report, args.report)
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
