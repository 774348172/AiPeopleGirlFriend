from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    data_dir: Path
    database_name: str = "relationship.sqlite3"
    max_input_chars: int = 8192
    context_size: int = 4096
    reply_reserve_tokens: int = 256
    context_safety_margin_tokens: int = 256
    recall_target_tokens: int = 512
    recent_verbatim_target_tokens: int = 1536
    recall_candidate_limit: int = 20
    recall_evidence_limit: int = 6
    recall_excerpt_chars: int = 240
    context_budget_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir))
        if not self.database_name or Path(self.database_name).name != self.database_name:
            raise ValueError("database_name must be a plain file name")
        if self.max_input_chars <= 0:
            raise ValueError("max_input_chars must be positive")
        for name in (
            "context_size",
            "reply_reserve_tokens",
            "context_safety_margin_tokens",
            "recall_target_tokens",
            "recent_verbatim_target_tokens",
            "recall_candidate_limit",
            "recall_evidence_limit",
            "recall_excerpt_chars",
            "context_budget_version",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if (
            self.reply_reserve_tokens + self.context_safety_margin_tokens
            >= self.context_size
        ):
            raise ValueError("reply reserve and safety margin must leave prompt capacity")
        if self.recall_target_tokens > self.maximum_prompt_tokens:
            raise ValueError("recall_target_tokens exceeds prompt capacity")
        if self.recent_verbatim_target_tokens > self.maximum_prompt_tokens:
            raise ValueError("recent_verbatim_target_tokens exceeds prompt capacity")

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_name

    @property
    def maximum_prompt_tokens(self) -> int:
        return (
            self.context_size
            - self.reply_reserve_tokens
            - self.context_safety_margin_tokens
        )
