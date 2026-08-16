from __future__ import annotations

import json
from pathlib import Path

from runtime._memory_propose import (
    MemoryProposeRequest,
    memory_propose_request_from_dict,
)


ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "eval" / "memory_propose"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def valid_cases() -> list[dict[str, object]]:
    return load_json(EVAL_DIR / "fixtures" / "valid" / "mode_cases_v1.json")


def request_dict() -> dict[str, object]:
    return valid_cases()[0]["request"]


def request() -> MemoryProposeRequest:
    return memory_propose_request_from_dict(request_dict())
