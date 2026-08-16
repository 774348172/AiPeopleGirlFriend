from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker

from eval.chat01.freeze import ROOT, sha256_file
from eval.chat01.rules import evaluate_case_output
from eval.chat01v5.freeze import MANIFEST as MANIFEST_PATH, verify as verify_manifest


REPORTS_ROOT = ROOT / "eval" / "chat01" / "reports"
RUNNER_PATH = ROOT / "eval" / "chat01" / "runner.py"
AUTO_RESULT_SCHEMA_PATH = ROOT / "eval" / "chat01" / "schema" / "auto_result.schema.json"
FROZEN_SINGLE_PATH = ROOT / "eval" / "chat01" / "suites" / "chat01_frozen_single_v1.jsonl"
STABLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")

Behavior = Literal["output", "timeout", "invalid"]


@dataclass(frozen=True)
class FakeScenario:
    case_id: str
    attempt_id: str
    behavior: Behavior
    expected_status: str
    output: str | None = None


FAKE_SCENARIOS = (
    FakeScenario("frozen.general.math_01", "fake-pass", "output", "pass", "当然是45。"),
    FakeScenario("frozen.general.math_02", "fake-fail", "output", "fail", "等于60。"),
    FakeScenario(
        "frozen.daily_relevance.meal_choice",
        "fake-blocker",
        "output",
        "blocker",
        "<think>internal reasoning</think> 作为AI，我建议吃面。",
    ),
    FakeScenario("frozen.identity.name", "fake-timeout", "timeout", "error"),
    FakeScenario("frozen.identity.age", "fake-invalid", "invalid", "invalid"),
)


class FakeOutputProvider:
    """Scripted test double. It has no model endpoint or model dependency."""

    model_id = "fake-output-provider-v1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, scenario: FakeScenario) -> Any:
        self.calls.append(scenario.attempt_id)
        if scenario.behavior == "timeout":
            raise TimeoutError("scripted fake timeout")
        if scenario.behavior == "invalid":
            return {"unexpected": "non-string fake output"}
        return scenario.output


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _load_cases(path: Path = FROZEN_SINGLE_PATH) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        case_id = case["case_id"]
        if case_id in cases:
            raise ValueError(f"duplicate case_id at {path}:{line_number}: {case_id}")
        cases[case_id] = case
    return cases


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


def _empty_metrics(seed: int | None = None) -> dict[str, Any]:
    return {
        "first_token_ms": None,
        "total_ms": None,
        "prompt_tokens": None,
        "output_tokens": None,
        "seed": seed,
    }


def _not_run_result(
    *,
    run_id: str,
    suite_id: str,
    model_id: str,
    scenario: FakeScenario,
    status: Literal["error", "invalid"],
    error_code: str,
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "suite_id": suite_id,
        "model_id": model_id,
        "case_id": scenario.case_id,
        "attempt_id": scenario.attempt_id,
        "status": status,
        "output": None,
        "error_code": error_code,
        "checks": [],
        "blocker_flags": [],
        "metrics": _empty_metrics(),
        "started_at": _iso(started_at),
        "completed_at": _iso(completed_at),
    }


def _evaluated_result(
    *,
    run_id: str,
    suite_id: str,
    model_id: str,
    case: dict[str, Any],
    scenario: FakeScenario,
    output: str,
    started_at: datetime,
    completed_at: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    evaluation = evaluate_case_output(
        case,
        output,
        attempt_id=scenario.attempt_id,
    )
    if evaluation["status"] == "pending_review":
        status = "invalid"
        error_code = "semantic_review_pending"
    else:
        status = evaluation["status"]
        error_code = None
    result = {
        "schema_version": 1,
        "run_id": run_id,
        "suite_id": suite_id,
        "model_id": model_id,
        "case_id": scenario.case_id,
        "attempt_id": scenario.attempt_id,
        "status": status,
        "output": output,
        "error_code": error_code,
        "checks": evaluation["checks"],
        "blocker_flags": evaluation["blocker_flags"],
        "metrics": {
            "first_token_ms": 25.0,
            "total_ms": 80.0,
            "prompt_tokens": 16,
            "output_tokens": max(1, len(output) // 2),
            "seed": case["generation"]["seed_set"][0],
        },
        "started_at": _iso(started_at),
        "completed_at": _iso(completed_at),
    }
    return result, evaluation["semantic_reviews"]


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
            for value in values
        ),
        encoding="utf-8",
        newline="\n",
    )


def _default_run_id(started_at: datetime, manifest_hash: str) -> str:
    timestamp = started_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"chat01g-fake-{timestamp}-{manifest_hash[:10]}"


def _report_markdown(run_manifest: dict[str, Any], aggregate: dict[str, Any]) -> str:
    counts = aggregate["counts"]
    return f"""# CHAT-01G Fake runner self-test

- Run: `{run_manifest['run_id']}`
- Suite: `{run_manifest['suite']['suite_id']}`
- Provider: `{run_manifest['model']['model_id']}`
- Real model called: `false`
- Self-test: `{aggregate['selftest_status']}`

| Metric | Count |
|---|---:|
| Original denominator | {aggregate['original_denominator']} |
| Pass | {counts['pass']} |
| Fail | {counts['fail']} |
| Blocker | {counts['blocker']} |
| Error | {counts['error']} |
| Skipped | {counts['skipped']} |
| Invalid | {counts['invalid']} |
| Timeout | {aggregate['timeouts']} |

This report was generated entirely from scripted Fake outputs. No model server,
GGUF, llama.cpp process, GPU inference, or external model API was used.
"""


def run_fake_selftest(
    *,
    reports_root: Path = REPORTS_ROOT,
    run_id: str | None = None,
    started_at: datetime | None = None,
    provider: FakeOutputProvider | None = None,
) -> Path:
    manifest = verify_manifest()
    start = started_at or datetime.now(timezone.utc).astimezone()
    if start.utcoffset() is None:
        raise ValueError("started_at must include a timezone")
    selected_run_id = run_id or _default_run_id(
        start, manifest["freeze"]["manifest_sha256"]
    )
    if STABLE_ID_RE.fullmatch(selected_run_id) is None:
        raise ValueError("run_id must be a stable ID and cannot contain a path")

    run_dir = reports_root / selected_run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    fake = provider or FakeOutputProvider()
    cases = _load_cases()
    schema = _load_json(AUTO_RESULT_SCHEMA_PATH)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    raw_outputs: list[dict[str, Any]] = []
    item_results: list[dict[str, Any]] = []
    blocker_reviews: list[dict[str, Any]] = []
    observed: dict[str, str] = {}
    invalid_reasons: Counter[str] = Counter()

    for index, scenario in enumerate(FAKE_SCENARIOS):
        if scenario.case_id not in cases:
            raise ValueError(f"fake scenario references unknown case: {scenario.case_id}")
        attempt_start = start + timedelta(milliseconds=index * 100)
        attempt_end = attempt_start + timedelta(milliseconds=80)
        raw_response: Any = None
        provider_status = "completed"
        error_code: str | None = None
        try:
            raw_response = fake.generate(scenario)
        except TimeoutError:
            provider_status = "timeout"
            error_code = "timeout"
            result = _not_run_result(
                run_id=selected_run_id,
                suite_id=manifest["suite_id"],
                model_id=fake.model_id,
                scenario=scenario,
                status="error",
                error_code=error_code,
                started_at=attempt_start,
                completed_at=attempt_end,
            )
        else:
            if not isinstance(raw_response, str):
                provider_status = "invalid"
                error_code = "invalid_output_type"
                invalid_reasons[error_code] += 1
                result = _not_run_result(
                    run_id=selected_run_id,
                    suite_id=manifest["suite_id"],
                    model_id=fake.model_id,
                    scenario=scenario,
                    status="invalid",
                    error_code=error_code,
                    started_at=attempt_start,
                    completed_at=attempt_end,
                )
            else:
                result, reviews = _evaluated_result(
                    run_id=selected_run_id,
                    suite_id=manifest["suite_id"],
                    model_id=fake.model_id,
                    case=cases[scenario.case_id],
                    scenario=scenario,
                    output=raw_response,
                    started_at=attempt_start,
                    completed_at=attempt_end,
                )
                blocker_reviews.extend(reviews)
        validator.validate(result)
        observed[scenario.attempt_id] = result["status"]
        item_results.append(result)
        raw_outputs.append(
            {
                "schema_version": 1,
                "run_id": selected_run_id,
                "suite_id": manifest["suite_id"],
                "model_id": fake.model_id,
                "case_id": scenario.case_id,
                "attempt_id": scenario.attempt_id,
                "provider_status": provider_status,
                "response": raw_response,
                "error_code": error_code,
                "started_at": _iso(attempt_start),
                "completed_at": _iso(attempt_end),
            }
        )

    expected = {scenario.attempt_id: scenario.expected_status for scenario in FAKE_SCENARIOS}
    counts = Counter(result["status"] for result in item_results)
    all_statuses = ("pass", "fail", "blocker", "invalid", "error", "skipped")
    end = start + timedelta(milliseconds=len(FAKE_SCENARIOS) * 100)
    selftest_passed = observed == expected and len(item_results) == len(FAKE_SCENARIOS)
    aggregate = {
        "schema_version": 1,
        "run_id": selected_run_id,
        "suite_id": manifest["suite_id"],
        "original_denominator": len(FAKE_SCENARIOS),
        "attempts": len(item_results),
        "counts": {status: counts[status] for status in all_statuses},
        "timeouts": sum(result["error_code"] == "timeout" for result in item_results),
        "invalid_reasons": dict(sorted(invalid_reasons.items())),
        "blocker_flags": dict(
            sorted(
                Counter(
                    flag
                    for result in item_results
                    for flag in result["blocker_flags"]
                ).items()
            )
        ),
        "expected_statuses": expected,
        "observed_statuses": observed,
        "selftest_status": "passed" if selftest_passed else "failed",
    }
    run_manifest = {
        "schema_version": 1,
        "run_id": selected_run_id,
        "mode": "fake_selftest",
        "suite": {
            "suite_id": manifest["suite_id"],
            "version": manifest["version"],
            "manifest_path": MANIFEST_PATH.resolve().relative_to(ROOT.resolve()).as_posix(),
            "manifest_sha256": manifest["freeze"]["manifest_sha256"],
            "canonical_sources": manifest["canonical_sources"],
        },
        "model": {
            "model_id": fake.model_id,
            "source": "in_process_scripted_fake",
            "real_model_called": False,
            "base_revision": None,
            "adapter_sha256": None,
            "merged_model_sha256": None,
            "gguf_sha256": None,
        },
        "prompt": {
            "mode": "not_applicable_scripted_output",
            "sha256": None,
        },
        "runtime": {
            "runner_path": RUNNER_PATH.resolve().relative_to(ROOT.resolve()).as_posix(),
            "runner_sha256": sha256_file(RUNNER_PATH),
            "python": platform.python_version(),
            "llama_cpp_version": None,
        },
        "hardware": {
            "platform": platform.platform(),
            "gpu": None,
            "driver": None,
            "cuda": None,
        },
        "generation": {
            "provider": "FakeOutputProvider",
            "scenario_count": len(FAKE_SCENARIOS),
            "network_allowed": False,
        },
        "started_at": _iso(start),
        "completed_at": _iso(end),
        "exit_status": "passed" if selftest_passed else "failed",
        "invalid_sample_reasons": dict(sorted(invalid_reasons.items())),
    }

    _write_json(run_dir / "run_manifest.json", run_manifest)
    _write_jsonl(run_dir / "raw_outputs.jsonl", raw_outputs)
    _write_jsonl(run_dir / "auto_item_results.jsonl", item_results)
    _write_json(run_dir / "aggregate.json", aggregate)
    _write_jsonl(run_dir / "blocker_review.jsonl", blocker_reviews)
    _write_jsonl(run_dir / "human_ballots.jsonl", [])
    (run_dir / "report.md").write_text(
        _report_markdown(run_manifest, aggregate),
        encoding="utf-8",
        newline="\n",
    )
    if not selftest_passed:
        raise AssertionError(f"Fake self-test status mismatch: expected={expected}, observed={observed}")
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CHAT-01 tooling against scripted Fake outputs only.")
    parser.add_argument("--run-id")
    parser.add_argument("--reports-root", type=Path, default=REPORTS_ROOT)
    args = parser.parse_args()
    try:
        run_dir = run_fake_selftest(
            reports_root=args.reports_root,
            run_id=args.run_id,
        )
    except (OSError, ValueError, AssertionError) as exc:
        print(f"CHAT-01G Fake self-test failed: {exc}")
        return 2
    aggregate = _load_json(run_dir / "aggregate.json")
    print(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "original_denominator": aggregate["original_denominator"],
                "counts": aggregate["counts"],
                "timeouts": aggregate["timeouts"],
                "selftest_status": aggregate["selftest_status"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
