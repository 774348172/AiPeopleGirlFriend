from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from eval.chat01.freeze import sha256_file
from eval.chat01v5.freeze import MANIFEST as MANIFEST_PATH, verify as verify_manifest
from eval.chat01.runner import FakeOutputProvider, run_fake_selftest


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_FILES = {
    "run_manifest.json",
    "raw_outputs.jsonl",
    "auto_item_results.jsonl",
    "aggregate.json",
    "blocker_review.jsonl",
    "human_ballots.jsonl",
    "report.md",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture
def fake_run(tmp_path: Path) -> tuple[Path, FakeOutputProvider]:
    provider = FakeOutputProvider()
    run_dir = run_fake_selftest(
        reports_root=tmp_path,
        run_id="chat01g-test-run",
        started_at=datetime(2026, 8, 5, 13, 0, tzinfo=timezone.utc),
        provider=provider,
    )
    return run_dir, provider


def test_fake_runner_writes_complete_non_overwriting_report_bundle(
    fake_run: tuple[Path, FakeOutputProvider],
) -> None:
    run_dir, provider = fake_run
    assert {path.name for path in run_dir.iterdir()} == EXPECTED_FILES
    assert provider.calls == [
        "fake-pass",
        "fake-fail",
        "fake-blocker",
        "fake-timeout",
        "fake-invalid",
    ]
    with pytest.raises(FileExistsError):
        run_fake_selftest(
            reports_root=run_dir.parent,
            run_id=run_dir.name,
            started_at=datetime(2026, 8, 5, 13, 0, tzinfo=timezone.utc),
        )


def test_fake_runner_preserves_all_statuses_in_original_denominator(
    fake_run: tuple[Path, FakeOutputProvider],
) -> None:
    run_dir, _provider = fake_run
    aggregate = _load_json(run_dir / "aggregate.json")
    assert aggregate["original_denominator"] == 5
    assert aggregate["attempts"] == 5
    assert aggregate["counts"] == {
        "pass": 1,
        "fail": 1,
        "blocker": 1,
        "invalid": 1,
        "error": 1,
        "skipped": 0,
    }
    assert aggregate["timeouts"] == 1
    assert aggregate["invalid_reasons"] == {"invalid_output_type": 1}
    assert aggregate["selftest_status"] == "passed"


def test_every_fake_auto_result_validates_and_attempts_are_retained(
    fake_run: tuple[Path, FakeOutputProvider],
) -> None:
    run_dir, _provider = fake_run
    schema = _load_json(ROOT / "eval/chat01/schema/auto_result.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    results = _load_jsonl(run_dir / "auto_item_results.jsonl")
    raw = _load_jsonl(run_dir / "raw_outputs.jsonl")
    assert len(results) == len(raw) == 5
    for result in results:
        validator.validate(result)
    by_attempt = {result["attempt_id"]: result for result in results}
    assert by_attempt["fake-timeout"]["status"] == "error"
    assert by_attempt["fake-timeout"]["error_code"] == "timeout"
    assert by_attempt["fake-invalid"]["status"] == "invalid"
    assert by_attempt["fake-invalid"]["error_code"] == "invalid_output_type"
    assert by_attempt["fake-timeout"]["output"] is None
    assert by_attempt["fake-invalid"]["output"] is None


def test_fake_blocker_and_pending_semantic_review_are_both_preserved(
    fake_run: tuple[Path, FakeOutputProvider],
) -> None:
    run_dir, _provider = fake_run
    blocker = next(
        result
        for result in _load_jsonl(run_dir / "auto_item_results.jsonl")
        if result["attempt_id"] == "fake-blocker"
    )
    assert blocker["status"] == "blocker"
    assert {"think_leak", "ai_identity"} <= set(blocker["blocker_flags"])

    reviews = _load_jsonl(run_dir / "blocker_review.jsonl")
    assert len(reviews) == 1
    assert reviews[0]["attempt_id"] == "fake-blocker"
    assert reviews[0]["final_status"] == "pending"
    semantic_schema = _load_json(
        ROOT / "eval/chat01/schema/semantic_review.schema.json"
    )
    Draft202012Validator(
        semantic_schema, format_checker=FormatChecker()
    ).validate(reviews[0])


def test_run_manifest_proves_fake_only_execution_and_records_tool_hash(
    fake_run: tuple[Path, FakeOutputProvider],
) -> None:
    run_dir, _provider = fake_run
    run_manifest = _load_json(run_dir / "run_manifest.json")
    frozen = verify_manifest()
    assert run_manifest["mode"] == "fake_selftest"
    assert run_manifest["model"]["real_model_called"] is False
    assert run_manifest["model"]["gguf_sha256"] is None
    assert run_manifest["runtime"]["llama_cpp_version"] is None
    assert run_manifest["generation"]["network_allowed"] is False
    assert run_manifest["suite"]["manifest_sha256"] == frozen["freeze"]["manifest_sha256"]
    runner_path = ROOT / run_manifest["runtime"]["runner_path"]
    assert run_manifest["runtime"]["runner_sha256"] == sha256_file(runner_path)


def test_fake_run_does_not_modify_frozen_manifest(tmp_path: Path) -> None:
    before = MANIFEST_PATH.read_bytes()
    run_fake_selftest(
        reports_root=tmp_path,
        run_id="chat01g-no-freeze-overwrite",
        started_at=datetime(2026, 8, 5, 13, 0, tzinfo=timezone.utc),
    )
    assert MANIFEST_PATH.read_bytes() == before
    verify_manifest()


@pytest.mark.parametrize("run_id", ["../escape", "x", "bad id", "C:/report"])
def test_fake_runner_rejects_unsafe_or_invalid_run_ids(
    tmp_path: Path, run_id: str
) -> None:
    with pytest.raises(ValueError, match="stable ID"):
        run_fake_selftest(
            reports_root=tmp_path,
            run_id=run_id,
            started_at=datetime(2026, 8, 5, 13, 0, tzinfo=timezone.utc),
        )
