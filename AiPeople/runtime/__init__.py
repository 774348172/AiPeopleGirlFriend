from importlib import import_module

from ._selected_memory import SelectedMemoryFrame
from .contracts import Completed, Failed, ReplyEvent, TextDelta, TurnMetrics, UserMessage

__all__ = [
    "Completed",
    "Failed",
    "LocalRerankerPackage",
    "MemoryAwareReplyModel",
    "MemorySelectionPipeline",
    "RelationshipRuntime",
    "ReplyEvent",
    "RuntimeConfig",
    "SelectedMemoryFrame",
    "TextDelta",
    "TurnMetrics",
    "UserMessage",
]


_LAZY_EXPORTS = {
    "LocalRerankerPackage": ("runtime._reranker_assets", "LocalRerankerPackage"),
    "MemoryAwareReplyModel": ("runtime._memory_aware_model", "MemoryAwareReplyModel"),
    "MemorySelectionPipeline": (
        "runtime._memory_selection",
        "MemorySelectionPipeline",
    ),
    "RelationshipRuntime": ("runtime.relationship_runtime", "RelationshipRuntime"),
    "RuntimeConfig": ("runtime._settings", "RuntimeConfig"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'runtime' has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
