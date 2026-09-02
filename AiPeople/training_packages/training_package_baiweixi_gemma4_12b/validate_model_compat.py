from __future__ import annotations

import json
from pathlib import Path

from accelerate import init_empty_weights
from safetensors import safe_open
from transformers import Gemma4Config, Gemma4ForConditionalGeneration


ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT.parent / "models" / "Gemma-4-12B-it"


def is_language_key(key: str) -> bool:
    return key.startswith("model.language_model.") or key.startswith("lm_head.")


def main() -> None:
    config = Gemma4Config.from_pretrained(MODEL_PATH)
    with init_empty_weights():
        model = Gemma4ForConditionalGeneration(config)

    expected = {key for key in model.state_dict() if is_language_key(key)}
    with safe_open(MODEL_PATH / "model.safetensors", framework="pt", device="cpu") as handle:
        checkpoint = {key for key in handle.keys() if is_language_key(key)}

    # Tied embeddings can appear as an expected state key without a separately
    # stored tensor. Every other language tensor must match exactly.
    allowed_missing = {"lm_head.weight"}
    missing = sorted(expected - checkpoint - allowed_missing)
    unexpected = sorted(checkpoint - expected)
    result = {
        "expected_language_keys": len(expected),
        "checkpoint_language_keys": len(checkpoint),
        "allowed_missing_tied_keys": sorted((expected - checkpoint) & allowed_missing),
        "missing_language_keys": missing,
        "unexpected_language_keys": unexpected,
        "passed": not missing and not unexpected,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise RuntimeError("Gemma 4 language checkpoint is incompatible with the training class")


if __name__ == "__main__":
    main()
