from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .build_cases import ROOT, sha256_file
from .prepare_review import ASSIGNMENTS_PATH, CONTRACT_PATH


REVEALED_CONTRACT_PATH = CONTRACT_PATH.with_name("revealed_review_contract.json")


def build() -> dict[str, Any]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["contract_id"] = "diagnostic120-human40-revealed-review-v1"
    contract["review_mode"] = "post_submission_reveal"
    contract["privacy"] = {
        "public_only": False,
        "private_assignment_access": True,
        "reveal_supported": True,
    }
    contract["model_labels"] = {
        "qwen3-4b-base-q4_k_m": "基座",
        "qinweixi-v2500-final-q4_k_m": "V2500",
    }
    contract["assets"] = [
        *contract["assets"],
        {
            "role": "assignments",
            "path": str(ASSIGNMENTS_PATH.relative_to(ROOT)).replace("\\", "/"),
            "bytes": ASSIGNMENTS_PATH.stat().st_size,
            "sha256": sha256_file(ASSIGNMENTS_PATH),
        },
    ]
    REVEALED_CONTRACT_PATH.write_text(
        json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return contract


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
