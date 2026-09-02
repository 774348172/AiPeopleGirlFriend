"""Evaluate clean Gemma 4 base versus the balanced counterfactual RPO Adapter."""
from __future__ import annotations

from pathlib import Path

import run_counterfactual_preference_medium_ab as evaluation


ROOT = Path(__file__).resolve().parents[1]
RPO_DATA_MANIFEST = (
    ROOT.parent
    / "AiPeopleCreate"
    / "训练数据"
    / "baiweixi_counterfactual_rpo_v1"
    / "manifest.json"
)
RPO_ADAPTER = (
    ROOT
    / "training_packages"
    / "training_package_baiweixi_gemma4_rpo"
    / "outputs"
    / "counterfactual_rpo_v1_2epoch"
)


def main() -> int:
    evaluation.ADAPTER = RPO_ADAPTER
    evaluation.EXPERIMENT_SCOPE = "clean_base_prompt_memory_vs_counterfactual_rpo"
    evaluation.PREFERENCE_ARM_LABEL = "B 同一裸基座 + RPO Adapter（DPO + chosen NLL）"
    evaluation.TRAINING_METHOD = "RPO (DPO + chosen NLL)"
    evaluation.TRAINING_ROWS = 320
    evaluation.TRAINING_DATA_MANIFEST_PATH = RPO_DATA_MANIFEST
    return evaluation.main()


if __name__ == "__main__":
    raise SystemExit(main())
