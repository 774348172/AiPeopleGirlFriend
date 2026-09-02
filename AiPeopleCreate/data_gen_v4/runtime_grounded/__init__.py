"""V5 runtime-grounded REPLY contracts and offline generation loop."""

from .adapter import RuntimeGroundedReplyAdapter
from .contracts import (
    RuntimeGroundedContractError,
    validate_semantic_audit,
    validate_teacher_target,
)
from .gates import RuntimeGroundedGateReport, evaluate_runtime_grounded_candidate
from .projection import ProjectionContractError, project_judge_payload
from .pilot import PilotBuildReport, PilotPackageError, build_pilot_package
from .renderer import (
    IGNORE_INDEX,
    build_current_assistant_example,
    render_runtime_grounded_training_record,
)
from .scenario import (
    ScenarioContractError,
    scenario_contract_errors,
    validate_scenario,
    validate_scenario_collection,
)

__all__ = [
    "IGNORE_INDEX",
    "ProjectionContractError",
    "PilotBuildReport",
    "PilotPackageError",
    "RuntimeGroundedContractError",
    "RuntimeGroundedGateReport",
    "RuntimeGroundedReplyAdapter",
    "ScenarioContractError",
    "build_current_assistant_example",
    "evaluate_runtime_grounded_candidate",
    "project_judge_payload",
    "build_pilot_package",
    "render_runtime_grounded_training_record",
    "scenario_contract_errors",
    "validate_semantic_audit",
    "validate_scenario",
    "validate_scenario_collection",
    "validate_teacher_target",
]
