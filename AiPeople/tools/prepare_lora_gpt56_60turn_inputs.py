from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_baiweixi_60turn_player_simulation as simulation


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare an Oracle-free 60-turn LoRA vs GPT input package"
    )
    parser.add_argument("output_dir")
    args = parser.parse_args()

    if len(simulation.TURNS) != 60:
        raise RuntimeError(f"expected 60 turns, got {len(simulation.TURNS)}")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    input_path = output_dir / "shared_inputs.jsonl"
    records = [
        {
            "ordinal": ordinal,
            "phase": turn["phase"],
            "system": simulation._system(turn),
            "player": turn["player"],
        }
        for ordinal, turn in enumerate(simulation.TURNS, start=1)
    ]
    _write_jsonl(input_path, records)

    manifest = {
        "schema_version": 1,
        "experiment": "lora-vs-gpt56sol-internal-60turn",
        "created_at": datetime.now().astimezone().isoformat(),
        "turn_count": len(records),
        "input_path": input_path.name,
        "input_sha256": _sha256(input_path),
        "oracle_withheld": True,
        "excluded_fields": [
            "expectation",
            "required_groups",
            "forbidden_facts",
            "previous_model_outputs",
            "manual_reviews",
        ],
        "history_contract": (
            "Each arm receives its own latest six user/assistant messages. "
            "The current user message is appended after that history."
        ),
    }
    manifest_path = output_dir / "input_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"OUTPUT_DIR={output_dir}")
    print(f"INPUT_PATH={input_path}")
    print(f"INPUT_SHA256={manifest['input_sha256']}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
