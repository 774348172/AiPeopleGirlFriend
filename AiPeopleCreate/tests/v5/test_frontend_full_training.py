import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from train_frontend_full_sft import validate_row
from prepare_frontend_full_training import normalize_contract
from build_game_reply_preview import candidates, export_row


def test_training_entry_rejects_accidental_history_supervision():
    scenario, target, _ = next(candidates())
    row = export_row(scenario, target, "你是白未晞。")
    validate_row(row)
    row["supervised_message_indexes"] = [2, len(row["messages"]) - 1]
    with pytest.raises(ValueError, match="Only final assistant"):
        validate_row(row)


def test_training_entry_rejects_legacy_profile():
    scenario, target, _ = next(candidates())
    row = export_row(scenario, target, "你是白未晞。")
    row["render_profile_id"] = "runtime-grounded-reply-v1"
    with pytest.raises(ValueError, match="Wrong training profile"):
        validate_row(row)


def test_contract_migration_does_not_mutate_frozen_source():
    scenario, _, _ = next(candidates())
    scenario["oracle_view"].pop("response_contract")
    frozen = copy.deepcopy(scenario)
    converted = normalize_contract(scenario)
    assert scenario == frozen
    assert converted["oracle_view"]["response_contract"]["unsupported_expansion_policy"] == "reject"
