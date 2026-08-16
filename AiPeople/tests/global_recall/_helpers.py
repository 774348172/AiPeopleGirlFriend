from __future__ import annotations

import hashlib
import struct
from array import array

from runtime._memory_vectors import (
    BGE_DIMENSION,
    ProjectionGeneration,
    SelectorView,
    VectorMatrixSnapshot,
)
from runtime._selector_query import SelectorQueryEncoding, build_selector_query
from tests.selector_query._helpers import IDENTITY, make_context


GENERATION = ProjectionGeneration(
    generation_id="generation-1",
    view_profile_id="mem05-selector-view-v1",
    encoder=IDENTITY,
    created_at="2026-08-08T00:00:00Z",
    completed_at="2026-08-08T00:00:01Z",
)


def basis(index: int, value: float = 1.0) -> tuple[float, ...]:
    vector = [0.0] * BGE_DIMENSION
    vector[index] = value
    return tuple(vector)


def make_query(vector: tuple[float, ...] | None = None) -> SelectorQueryEncoding:
    values = vector or basis(0)
    blob = struct.pack(f"<{BGE_DIMENSION}f", *values)
    query = build_selector_query(make_context())
    return SelectorQueryEncoding(
        query=query,
        query_sha256="1" * 64,
        rendered_text="query",
        rendered_text_sha256="2" * 64,
        encoder=IDENTITY,
        token_count=10,
        vector=values,
        vector_blob=blob,
        vector_sha256=hashlib.sha256(blob).hexdigest(),
    )


def make_view(
    memory_id: str,
    ordinal: int,
    *,
    version: int = 1,
    kind: str = "statement",
) -> SelectorView:
    text = f"{memory_id}:{ordinal}"
    return SelectorView(
        memory_id=memory_id,
        memory_version=version,
        view_ordinal=ordinal,
        view_kind=kind,
        text=text,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        source_revision=hashlib.sha256(memory_id.encode()).hexdigest(),
    )


def make_snapshot(
    rows: tuple[tuple[SelectorView, tuple[float, ...]], ...],
    *,
    generation: ProjectionGeneration | None = GENERATION,
) -> VectorMatrixSnapshot:
    values = array("f")
    for _, vector in rows:
        values.extend(vector)
    return VectorMatrixSnapshot(
        generation=generation,
        views=tuple(view for view, _ in rows),
        values=values,
        rows=len(rows),
        dimension=BGE_DIMENSION if generation is not None else 0,
    )

