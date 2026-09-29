"""V5 runtime-grounded REPLY contracts and offline generation loop."""

from .adapter import RuntimeGroundedReplyAdapter
from .conversational import (
    CONVERSATIONAL_MODE,
    STYLE_TASKS,
    evaluate_conversational_candidate,
    render_conversational_training_record,
    validate_conversational_audit,
    validate_conversational_scenario,
    validate_conversational_target,
)
from .contracts import (
    RuntimeGroundedContractError,
    validate_semantic_audit,
    validate_teacher_target,
)
from .gates import RuntimeGroundedGateReport, evaluate_runtime_grounded_candidate
from .projection import ProjectionContractError, project_judge_payload
from .pilot import PilotBuildReport, PilotPackageError, build_pilot_package
from .renderer import (
    GAME_REPLY_CONTEXT_RENDER_PROFILE,
    IGNORE_INDEX,
    build_current_assistant_example,
    render_game_reply_context_from_model_view,
    render_game_reply_training_record,
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
    "GAME_REPLY_CONTEXT_RENDER_PROFILE",
    "ProjectionContractError",
    "PilotBuildReport",
    "PilotPackageError",
    "RuntimeGroundedContractError",
    "RuntimeGroundedGateReport",
    "RuntimeGroundedReplyAdapter",
    "CONVERSATIONAL_MODE",
    "STYLE_TASKS",
    "evaluate_conversational_candidate",
    "render_conversational_training_record",
    "validate_conversational_audit",
    "validate_conversational_scenario",
    "validate_conversational_target",
    "ScenarioContractError",
    "build_current_assistant_example",
    "evaluate_runtime_grounded_candidate",
    "project_judge_payload",
    "build_pilot_package",
    "render_runtime_grounded_training_record",
    "render_game_reply_context_from_model_view",
    "render_game_reply_training_record",
    "scenario_contract_errors",
    "validate_semantic_audit",
    "validate_scenario",
    "validate_scenario_collection",
    "validate_teacher_target",
]
