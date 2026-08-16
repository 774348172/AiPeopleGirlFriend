from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol, runtime_checkable

from ._context import ReplyContext
from ._memory_vectors import (
    BGE_DIMENSION,
    BGE_MAX_SEQUENCE_LENGTH,
    BGE_MODEL_ID,
    BGE_POOLING,
    EmbeddingEncoder,
    EncoderIdentity,
)


SELECTOR_QUERY_SCHEMA_VERSION = "recall-selector-query-v1"
RECENT_DIALOGUE_TURNS = 4
MAX_RENDERED_QUERY_CHARS = 480
MAX_QUERY_TOKENS = BGE_MAX_SEQUENCE_LENGTH
QUERY_VECTOR_DTYPE = "float32le"

SelectorRole = Literal["user", "character"]
PartOfDay = Literal["night", "morning", "afternoon", "evening"]


class SelectorQueryError(RuntimeError):
    pass


class SelectorQueryTooLongError(SelectorQueryError):
    pass


class SelectorEncoderMismatchError(SelectorQueryError):
    pass


class SelectorVectorIntegrityError(SelectorQueryError):
    pass


@runtime_checkable
class SelectorTokenizer(Protocol):
    @property
    def identity(self) -> EncoderIdentity: ...

    def count_tokens(self, text: str) -> int: ...


@dataclass(frozen=True, slots=True)
class SelectorDialogueTurn:
    role: SelectorRole
    text: str

    def __post_init__(self) -> None:
        if self.role not in ("user", "character"):
            raise ValueError("selector dialogue role must be user or character")
        _require_normalized_text(self.text, "selector dialogue text", allow_empty=True)


@dataclass(frozen=True, slots=True)
class SelectorWorkingState:
    has_completed_exchange: bool
    unresolved_prior_user_turns: int

    def __post_init__(self) -> None:
        if not isinstance(self.has_completed_exchange, bool):
            raise TypeError("has_completed_exchange must be a bool")
        if (
            not isinstance(self.unresolved_prior_user_turns, int)
            or isinstance(self.unresolved_prior_user_turns, bool)
            or self.unresolved_prior_user_turns < 0
        ):
            raise ValueError("unresolved_prior_user_turns must be non-negative")


@dataclass(frozen=True, slots=True)
class SelectorTimeContext:
    local_datetime: str
    timezone: str
    weekday: int
    part_of_day: PartOfDay

    def __post_init__(self) -> None:
        _require_text(self.local_datetime, "local_datetime")
        _require_text(self.timezone, "timezone")
        if self.weekday not in range(1, 8):
            raise ValueError("weekday must use ISO values 1..7")
        if self.part_of_day not in ("night", "morning", "afternoon", "evening"):
            raise ValueError("invalid part_of_day")


@dataclass(frozen=True, slots=True)
class SelectorQuery:
    schema_version: str
    current_user_message: str
    recent_dialogue: tuple[SelectorDialogueTurn, ...]
    compact_working_state: SelectorWorkingState
    current_time_context: SelectorTimeContext

    def __post_init__(self) -> None:
        if self.schema_version != SELECTOR_QUERY_SCHEMA_VERSION:
            raise ValueError("unsupported selector query schema_version")
        _require_normalized_text(self.current_user_message, "current_user_message")
        if not isinstance(self.recent_dialogue, tuple):
            raise TypeError("recent_dialogue must be a tuple")
        if len(self.recent_dialogue) > RECENT_DIALOGUE_TURNS:
            raise ValueError("recent_dialogue exceeds the frozen turn limit")
        if any(not isinstance(turn, SelectorDialogueTurn) for turn in self.recent_dialogue):
            raise TypeError("recent_dialogue must contain SelectorDialogueTurn values")
        if not isinstance(self.compact_working_state, SelectorWorkingState):
            raise TypeError("compact_working_state has the wrong type")
        if not isinstance(self.current_time_context, SelectorTimeContext):
            raise TypeError("current_time_context has the wrong type")


@dataclass(frozen=True, slots=True)
class SelectorQueryEncoding:
    query: SelectorQuery
    query_sha256: str
    rendered_text: str
    rendered_text_sha256: str
    encoder: EncoderIdentity
    token_count: int
    vector: tuple[float, ...]
    vector_blob: bytes
    vector_sha256: str
    dtype: str = QUERY_VECTOR_DTYPE
    normalized: bool = True


def build_selector_query(context: ReplyContext) -> SelectorQuery:
    if not isinstance(context, ReplyContext):
        raise TypeError("context must be a ReplyContext")
    activation = context.working_activation
    current_message = _normalize_text(context.current_user_text)
    if not current_message:
        raise ValueError("current_user_message must not be empty")

    unresolved_prior = sum(
        event_id != context.current_user_event_id
        for event_id in activation.unresolved_user_event_ids
    )
    working_state = SelectorWorkingState(
        has_completed_exchange=(
            activation.last_completed_user_event_id is not None
            and activation.last_completed_character_event_id is not None
        ),
        unresolved_prior_user_turns=unresolved_prior,
    )
    now = activation.current_time
    time_context = SelectorTimeContext(
        local_datetime=now.isoformat(timespec="seconds"),
        timezone=activation.current_timezone,
        weekday=now.isoweekday(),
        part_of_day=_part_of_day(now.hour),
    )
    turns = tuple(
        SelectorDialogueTurn(
            role="user" if message.role == "user" else "character",
            text=_normalize_text(message.text),
        )
        for message in context.history_messages[-RECENT_DIALOGUE_TURNS:]
    )
    query = SelectorQuery(
        schema_version=SELECTOR_QUERY_SCHEMA_VERSION,
        current_user_message=current_message,
        recent_dialogue=turns,
        compact_working_state=working_state,
        current_time_context=time_context,
    )
    while len(render_selector_query_text(query)) > MAX_RENDERED_QUERY_CHARS:
        if not query.recent_dialogue:
            raise SelectorQueryTooLongError(
                "selector query exceeds the frozen character budget without history"
            )
        query = replace(query, recent_dialogue=query.recent_dialogue[1:])
    return query


def selector_query_to_dict(query: SelectorQuery) -> dict[str, object]:
    _require_query(query)
    return {
        "schema_version": query.schema_version,
        "current_user_message": query.current_user_message,
        "recent_dialogue": [
            {"role": turn.role, "text": turn.text}
            for turn in query.recent_dialogue
        ],
        "compact_working_state": {
            "has_completed_exchange": query.compact_working_state.has_completed_exchange,
            "unresolved_prior_user_turns": (
                query.compact_working_state.unresolved_prior_user_turns
            ),
        },
        "current_time_context": {
            "local_datetime": query.current_time_context.local_datetime,
            "timezone": query.current_time_context.timezone,
            "weekday": query.current_time_context.weekday,
            "part_of_day": query.current_time_context.part_of_day,
        },
    }


def canonical_selector_query_json(query: SelectorQuery) -> str:
    return json.dumps(
        selector_query_to_dict(query),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def selector_query_sha256(query: SelectorQuery) -> str:
    return hashlib.sha256(canonical_selector_query_json(query).encode("utf-8")).hexdigest()


def render_selector_query_text(query: SelectorQuery) -> str:
    _require_query(query)
    dialogue = [
        f"- role={turn.role}; text={turn.text}" for turn in query.recent_dialogue
    ] or ["- none"]
    state = query.compact_working_state
    time_context = query.current_time_context
    return "\n".join(
        (
            f"schema_version={query.schema_version}",
            f"current_user_message={query.current_user_message}",
            "recent_dialogue:",
            *dialogue,
            "compact_working_state:",
            f"- has_completed_exchange={str(state.has_completed_exchange).lower()}",
            f"- unresolved_prior_user_turns={state.unresolved_prior_user_turns}",
            "current_time_context:",
            f"- local_datetime={time_context.local_datetime}",
            f"- timezone={time_context.timezone}",
            f"- weekday={time_context.weekday}",
            f"- part_of_day={time_context.part_of_day}",
        )
    )


def encode_selector_query(
    query: SelectorQuery,
    *,
    encoder: EmbeddingEncoder,
    tokenizer: SelectorTokenizer,
    expected_encoder: EncoderIdentity,
) -> SelectorQueryEncoding:
    _require_query(query)
    if not isinstance(encoder, EmbeddingEncoder):
        raise TypeError("encoder must implement EmbeddingEncoder")
    if not isinstance(tokenizer, SelectorTokenizer):
        raise TypeError("tokenizer must implement SelectorTokenizer")
    if (
        encoder.identity != expected_encoder
        or tokenizer.identity != expected_encoder
        or expected_encoder.model_id != BGE_MODEL_ID
        or expected_encoder.dimension != BGE_DIMENSION
    ):
        raise SelectorEncoderMismatchError(
            "query encoder, tokenizer and active vector generation must have identical identity"
        )

    rendered = render_selector_query_text(query)
    if len(rendered) > MAX_RENDERED_QUERY_CHARS:
        raise SelectorQueryTooLongError("selector query exceeds the frozen character budget")
    token_count = tokenizer.count_tokens(rendered)
    if not isinstance(token_count, int) or isinstance(token_count, bool) or token_count <= 0:
        raise SelectorQueryError("tokenizer returned an invalid token count")
    if token_count > MAX_QUERY_TOKENS:
        raise SelectorQueryTooLongError("selector query exceeds the 512-token BGE limit")

    rows = encoder.encode((rendered,))
    if len(rows) != 1:
        raise SelectorVectorIntegrityError("encoder must return exactly one query vector")
    vector = _normalized_float32(rows[0], expected_encoder.dimension)
    blob = struct.pack(f"<{len(vector)}f", *vector)
    rendered_sha256 = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return SelectorQueryEncoding(
        query=query,
        query_sha256=selector_query_sha256(query),
        rendered_text=rendered,
        rendered_text_sha256=rendered_sha256,
        encoder=expected_encoder,
        token_count=token_count,
        vector=vector,
        vector_blob=blob,
        vector_sha256=hashlib.sha256(blob).hexdigest(),
    )


def selector_encoder_profile() -> dict[str, object]:
    return {
        "model_id": BGE_MODEL_ID,
        "dimension": BGE_DIMENSION,
        "pooling": BGE_POOLING,
        "max_sequence_length": BGE_MAX_SEQUENCE_LENGTH,
        "normalization": "runtime_l2",
        "dtype": QUERY_VECTOR_DTYPE,
        "device": "cpu",
        "network_access": "forbidden",
    }


def _normalized_float32(raw: Sequence[float], dimension: int) -> tuple[float, ...]:
    if len(raw) != dimension:
        raise SelectorVectorIntegrityError("encoder vector dimension mismatch")
    values = tuple(float(value) for value in raw)
    if not all(math.isfinite(value) for value in values):
        raise SelectorVectorIntegrityError("encoder vector contains a non-finite value")
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 0.0:
        raise SelectorVectorIntegrityError("encoder vector has zero or invalid L2 norm")
    normalized = tuple(value / norm for value in values)
    float32 = struct.unpack(f"<{dimension}f", struct.pack(f"<{dimension}f", *normalized))
    float32_norm = math.sqrt(math.fsum(value * value for value in float32))
    if not math.isclose(float32_norm, 1.0, rel_tol=1e-5, abs_tol=1e-5):
        raise SelectorVectorIntegrityError("float32 query vector is not L2-normalized")
    return tuple(float32)


def _normalize_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("selector query text must be a string")
    return " ".join(value.split())


def _part_of_day(hour: int) -> PartOfDay:
    if hour < 6:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def _require_query(query: SelectorQuery) -> None:
    if not isinstance(query, SelectorQuery):
        raise TypeError("query must be a SelectorQuery")


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be empty")


def _require_normalized_text(value: str, field: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{field} must not be empty")
    if value != _normalize_text(value):
        raise ValueError(f"{field} must use deterministic whitespace normalization")
