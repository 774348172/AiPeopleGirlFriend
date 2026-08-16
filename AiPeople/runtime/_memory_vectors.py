from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
import uuid
from array import array
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from ._memory_contracts import (
    MEMORY_INDEXABLE_STATUSES,
    MemoryRepresentation,
    canonical_memory_json,
    memory_representation_from_json,
)


BGE_MODEL_ID = "BAAI/bge-small-zh-v1.5"
BGE_DIMENSION = 512
BGE_MAX_SEQUENCE_LENGTH = 512
BGE_POOLING = "mean"
VIEW_PROFILE_ID = "mem05-selector-view-v1"
VECTOR_DTYPE = "float32le"
INDEXABLE_STATUSES = frozenset(MEMORY_INDEXABLE_STATUSES)


class MemoryVectorError(RuntimeError):
    pass


class EncoderAssetError(MemoryVectorError):
    pass


class VectorIntegrityError(MemoryVectorError):
    pass


@dataclass(frozen=True, slots=True)
class EncoderIdentity:
    model_id: str
    revision: str
    artifact_sha256: str
    dimension: int

    def __post_init__(self) -> None:
        if self.model_id != BGE_MODEL_ID:
            raise EncoderAssetError(f"encoder model_id must be {BGE_MODEL_ID}")
        if not self.revision.strip():
            raise EncoderAssetError("encoder revision must be explicit")
        if len(self.artifact_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.artifact_sha256
        ):
            raise EncoderAssetError("encoder artifact SHA256 is invalid")
        if self.dimension <= 0:
            raise EncoderAssetError("encoder dimension must be positive")


@runtime_checkable
class EmbeddingEncoder(Protocol):
    @property
    def identity(self) -> EncoderIdentity: ...

    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


@dataclass(frozen=True, slots=True)
class SelectorView:
    memory_id: str
    memory_version: int
    view_ordinal: int
    view_kind: str
    text: str
    text_sha256: str
    source_revision: str


@dataclass(frozen=True, slots=True)
class ProjectionGeneration:
    generation_id: str
    view_profile_id: str
    encoder: EncoderIdentity
    created_at: str
    completed_at: str


@dataclass(frozen=True, slots=True)
class ProjectionSyncResult:
    generation: ProjectionGeneration
    embedded_memories: int
    invalidated_memories: int
    rebuilt: bool


@dataclass(frozen=True, slots=True)
class VectorMatrixSnapshot:
    generation: ProjectionGeneration | None
    views: tuple[SelectorView, ...]
    values: array
    rows: int
    dimension: int

    def row(self, index: int) -> tuple[float, ...]:
        if index < 0 or index >= self.rows:
            raise IndexError(index)
        start = index * self.dimension
        return tuple(self.values[start : start + self.dimension])


def build_selector_views(memory: MemoryRepresentation) -> tuple[SelectorView, ...]:
    if memory.status not in INDEXABLE_STATUSES:
        return ()
    source_revision = hashlib.sha256(canonical_memory_json(memory)).hexdigest()
    candidates: list[tuple[str, str]] = [("statement", _clean_text(memory.statement))]
    if memory.temporal.source_text:
        candidates.append(("temporal", _clean_text(memory.temporal.source_text)))
    candidates.extend(("evidence", _clean_text(item.excerpt)) for item in memory.evidence)

    unique: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, text in candidates:
        key = (kind, text)
        if text and key not in seen:
            seen.add(key)
            unique.append(key)
    return tuple(
        SelectorView(
            memory_id=memory.memory_id,
            memory_version=memory.version,
            view_ordinal=ordinal,
            view_kind=kind,
            text=text,
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            source_revision=source_revision,
        )
        for ordinal, (kind, text) in enumerate(unique)
    )


class LocalBgeEncoder:
    """CPU-only BGE adapter that accepts an explicit local model directory."""

    def __init__(
        self,
        asset_path: str | Path,
        *,
        revision: str,
        loader: Callable[[Path], object] | None = None,
    ) -> None:
        path = Path(asset_path).resolve()
        if not path.is_dir():
            raise EncoderAssetError("local BGE asset directory does not exist")
        _validate_bge_asset_profile(path)
        self._asset_path = path
        self._identity = EncoderIdentity(
            model_id=BGE_MODEL_ID,
            revision=revision,
            artifact_sha256=_directory_sha256(path),
            dimension=BGE_DIMENSION,
        )
        self._loader = loader or _load_sentence_transformer
        self._backend: object | None = None

    @property
    def identity(self) -> EncoderIdentity:
        return self._identity

    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not texts:
            return ()
        if self._backend is None:
            self._backend = self._loader(self._asset_path)
        encode = getattr(self._backend, "encode", None)
        if not callable(encode):
            raise EncoderAssetError("local BGE backend does not provide encode()")
        result = encode(
            list(texts),
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=False,
            normalize_embeddings=False,
        )
        return result


class MemoryVectorStore:
    def __init__(
        self,
        connection: sqlite3.Connection,
        encoder: EmbeddingEncoder,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        view_profile_id: str = VIEW_PROFILE_ID,
    ) -> None:
        if not isinstance(encoder, EmbeddingEncoder):
            raise TypeError("encoder must implement EmbeddingEncoder")
        if not view_profile_id.strip():
            raise ValueError("view_profile_id must not be empty")
        self._connection = connection
        self._encoder = encoder
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._view_profile_id = view_profile_id

    @classmethod
    def from_memory_store(
        cls,
        memory_store: object,
        encoder: EmbeddingEncoder,
        **kwargs: object,
    ) -> "MemoryVectorStore":
        connection = getattr(memory_store, "_connection", None)
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("memory_store must be a runtime MemoryStore")
        return cls(connection, encoder, **kwargs)

    def active_generation(self) -> ProjectionGeneration | None:
        row = self._connection.execute(
            """
            SELECT g.* FROM memory_vector_projection_state s
            JOIN memory_vector_generations g
              ON g.generation_id=s.active_generation_id
            WHERE s.singleton_id=1
            """
        ).fetchone()
        return _generation_from_row(row) if row is not None else None

    def sync_incremental(self) -> ProjectionSyncResult:
        generation = self.active_generation()
        if generation is None or not self._generation_matches(generation):
            return self.rebuild_all()

        current = self._current_memories()
        projected = {
            str(row[0]): (int(row[1]), str(row[2]))
            for row in self._connection.execute(
                """
                SELECT memory_id, memory_version, source_revision
                FROM memory_selector_views
                WHERE generation_id=? AND view_ordinal=0
                """,
                (generation.generation_id,),
            )
        }
        eligible = {
            memory.memory_id: memory
            for memory in current
            if memory.status in INDEXABLE_STATUSES
        }
        stale_ids = set(projected) - set(eligible)
        changed = []
        for memory_id, memory in eligible.items():
            revision = hashlib.sha256(canonical_memory_json(memory)).hexdigest()
            if projected.get(memory_id) != (memory.version, revision):
                changed.append(memory)

        encoded = {memory.memory_id: self._encode_memory(memory) for memory in changed}
        if not stale_ids and not changed:
            return ProjectionSyncResult(generation, 0, 0, False)

        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._assert_current_sources(changed)
            active = self.active_generation()
            if active != generation:
                raise MemoryVectorError("active vector generation changed during sync")
            for memory_id in sorted(stale_ids | set(encoded)):
                connection.execute(
                    "DELETE FROM memory_selector_views WHERE generation_id=? AND memory_id=?",
                    (generation.generation_id, memory_id),
                )
            for memory in changed:
                self._insert_encoded(generation.generation_id, encoded[memory.memory_id])
            connection.execute(
                "UPDATE memory_vector_projection_state SET updated_at=? WHERE singleton_id=1",
                (_utc_z(self._clock()),),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return ProjectionSyncResult(generation, len(changed), len(stale_ids), False)

    def rebuild_all(
        self, *, fault_hook: Callable[[str], None] | None = None
    ) -> ProjectionSyncResult:
        memories = [
            memory
            for memory in self._current_memories()
            if memory.status in INDEXABLE_STATUSES
        ]
        encoded = [self._encode_memory(memory) for memory in memories]
        if fault_hook is not None:
            fault_hook("encoded")
        now = _utc_z(self._clock())
        generation_id = f"memory-vectors-{self._id_factory()}"
        identity = self._encoder.identity
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._assert_current_sources(memories)
            connection.execute(
                """
                INSERT INTO memory_vector_generations (
                    generation_id, view_profile_id, encoder_model_id,
                    encoder_revision, encoder_artifact_sha256, dimension,
                    dtype, normalized, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'float32le', 1, ?, ?)
                """,
                (
                    generation_id,
                    self._view_profile_id,
                    identity.model_id,
                    identity.revision,
                    identity.artifact_sha256,
                    identity.dimension,
                    now,
                    now,
                ),
            )
            for item in encoded:
                self._insert_encoded(generation_id, item)
            if fault_hook is not None:
                fault_hook("staged")
            connection.execute(
                """
                INSERT INTO memory_vector_projection_state (
                    singleton_id, active_generation_id, updated_at
                ) VALUES (1, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    active_generation_id=excluded.active_generation_id,
                    updated_at=excluded.updated_at
                """,
                (generation_id, now),
            )
            connection.execute(
                "DELETE FROM memory_vector_generations WHERE generation_id<>?",
                (generation_id,),
            )
            if fault_hook is not None:
                fault_hook("activated")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        generation = self.active_generation()
        if generation is None:
            raise MemoryVectorError("rebuilt vector generation was not activated")
        return ProjectionSyncResult(generation, len(memories), 0, True)

    def matrix_snapshot(self) -> VectorMatrixSnapshot:
        generation = self.active_generation()
        if generation is None:
            return VectorMatrixSnapshot(None, (), array("f"), 0, 0)
        rows = self._connection.execute(
            """
            SELECT v.*, e.dimension, e.dtype, e.normalized,
                   e.vector_blob, e.vector_sha256
            FROM memory_selector_views v
            JOIN memory_embeddings e USING (generation_id, memory_id, view_ordinal)
            JOIN memories m ON m.memory_id=v.memory_id
            WHERE v.generation_id=?
              AND m.status IN ('active', 'disputed')
              AND m.version=v.memory_version
            ORDER BY v.memory_id, v.view_ordinal
            """,
            (generation.generation_id,),
        ).fetchall()
        values = array("f")
        views = []
        for row in rows:
            blob = bytes(row["vector_blob"])
            _validate_vector_blob(
                blob,
                vector_sha256=str(row["vector_sha256"]),
                dimension=int(row["dimension"]),
                expected_dimension=generation.encoder.dimension,
                dtype=str(row["dtype"]),
                normalized=int(row["normalized"]),
            )
            values.extend(struct.unpack(f"<{generation.encoder.dimension}f", blob))
            views.append(
                SelectorView(
                    memory_id=str(row["memory_id"]),
                    memory_version=int(row["memory_version"]),
                    view_ordinal=int(row["view_ordinal"]),
                    view_kind=str(row["view_kind"]),
                    text=str(row["view_text"]),
                    text_sha256=str(row["text_sha256"]),
                    source_revision=str(row["source_revision"]),
                )
            )
        return VectorMatrixSnapshot(
            generation,
            tuple(views),
            values,
            len(views),
            generation.encoder.dimension,
        )

    def _generation_matches(self, generation: ProjectionGeneration) -> bool:
        return (
            generation.view_profile_id == self._view_profile_id
            and generation.encoder == self._encoder.identity
        )

    def _current_memories(self) -> tuple[MemoryRepresentation, ...]:
        rows = self._connection.execute(
            "SELECT representation_json FROM memories ORDER BY memory_id"
        ).fetchall()
        return tuple(memory_representation_from_json(str(row[0])) for row in rows)

    def _assert_current_sources(
        self, memories: Sequence[MemoryRepresentation]
    ) -> None:
        for memory in memories:
            row = self._connection.execute(
                "SELECT status, version, representation_json FROM memories WHERE memory_id=?",
                (memory.memory_id,),
            ).fetchone()
            if (
                row is None
                or str(row["status"]) not in INDEXABLE_STATUSES
                or int(row["version"]) != memory.version
                or hashlib.sha256(str(row["representation_json"]).encode("utf-8")).hexdigest()
                != hashlib.sha256(canonical_memory_json(memory)).hexdigest()
            ):
                raise MemoryVectorError("memory source changed while vectors were encoded")

    def _encode_memory(
        self, memory: MemoryRepresentation
    ) -> tuple[tuple[SelectorView, bytes, str], ...]:
        views = build_selector_views(memory)
        raw_vectors = self._encoder.encode([view.text for view in views])
        if len(raw_vectors) != len(views):
            raise VectorIntegrityError("encoder returned the wrong row count")
        rows = []
        for view, raw in zip(views, raw_vectors, strict=True):
            values = _normalize_vector(raw, self._encoder.identity.dimension)
            blob = struct.pack(f"<{len(values)}f", *values)
            rows.append((view, blob, hashlib.sha256(blob).hexdigest()))
        return tuple(rows)

    def _insert_encoded(
        self,
        generation_id: str,
        rows: tuple[tuple[SelectorView, bytes, str], ...],
    ) -> None:
        for view, blob, vector_sha256 in rows:
            self._connection.execute(
                """
                INSERT INTO memory_selector_views (
                    generation_id, memory_id, memory_version, view_ordinal,
                    view_kind, view_text, text_sha256, source_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generation_id,
                    view.memory_id,
                    view.memory_version,
                    view.view_ordinal,
                    view.view_kind,
                    view.text,
                    view.text_sha256,
                    view.source_revision,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO memory_embeddings (
                    generation_id, memory_id, memory_version, view_ordinal,
                    dimension, dtype, normalized, vector_blob, vector_sha256
                ) VALUES (?, ?, ?, ?, ?, 'float32le', 1, ?, ?)
                """,
                (
                    generation_id,
                    view.memory_id,
                    view.memory_version,
                    view.view_ordinal,
                    self._encoder.identity.dimension,
                    blob,
                    vector_sha256,
                ),
            )


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _normalize_vector(raw: Sequence[float], dimension: int) -> tuple[float, ...]:
    if len(raw) != dimension:
        raise VectorIntegrityError("encoder vector dimension mismatch")
    values = tuple(float(value) for value in raw)
    if not all(math.isfinite(value) for value in values):
        raise VectorIntegrityError("encoder vector contains a non-finite value")
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 0.0:
        raise VectorIntegrityError("encoder vector has zero or invalid L2 norm")
    return tuple(value / norm for value in values)


def _validate_vector_blob(
    blob: bytes,
    *,
    vector_sha256: str,
    dimension: int,
    expected_dimension: int,
    dtype: str,
    normalized: int,
) -> None:
    if dtype != VECTOR_DTYPE or normalized != 1:
        raise VectorIntegrityError("stored vector profile is invalid")
    if dimension != expected_dimension or len(blob) != dimension * 4:
        raise VectorIntegrityError("stored vector dimension is invalid")
    if hashlib.sha256(blob).hexdigest() != vector_sha256:
        raise VectorIntegrityError("stored vector SHA256 mismatch")
    values = struct.unpack(f"<{dimension}f", blob)
    if not all(math.isfinite(value) for value in values):
        raise VectorIntegrityError("stored vector contains a non-finite value")
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-5):
        raise VectorIntegrityError("stored vector is not L2-normalized")


def _generation_from_row(row: sqlite3.Row) -> ProjectionGeneration:
    return ProjectionGeneration(
        generation_id=str(row["generation_id"]),
        view_profile_id=str(row["view_profile_id"]),
        encoder=EncoderIdentity(
            model_id=str(row["encoder_model_id"]),
            revision=str(row["encoder_revision"]),
            artifact_sha256=str(row["encoder_artifact_sha256"]),
            dimension=int(row["dimension"]),
        ),
        created_at=str(row["created_at"]),
        completed_at=str(row["completed_at"]),
    )


def _directory_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    if not files:
        raise EncoderAssetError("local BGE asset directory is empty")
    for item in files:
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with item.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def _validate_bge_asset_profile(path: Path) -> None:
    modules_path = path / "modules.json"
    sentence_config_path = path / "sentence_bert_config.json"
    try:
        modules = json.loads(modules_path.read_text(encoding="utf-8"))
        sentence_config = json.loads(sentence_config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EncoderAssetError(
            "local BGE asset is missing valid sentence-transformers profile files"
        ) from error
    pooling_modules = [
        item for item in modules
        if isinstance(item, dict) and str(item.get("type", "")).endswith(".Pooling")
    ]
    if len(pooling_modules) != 1:
        raise EncoderAssetError("local BGE asset must contain one pooling module")
    pooling_path = path / str(pooling_modules[0].get("path", "")) / "config.json"
    try:
        pooling = json.loads(pooling_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EncoderAssetError("local BGE pooling config is missing or invalid") from error
    if (
        pooling.get("word_embedding_dimension") != BGE_DIMENSION
        or pooling.get("pooling_mode_mean_tokens") is not True
        or pooling.get("pooling_mode_cls_token") is not False
        or sentence_config.get("max_seq_length") != BGE_MAX_SEQUENCE_LENGTH
    ):
        raise EncoderAssetError(
            "local BGE asset must use 512 dimensions, mean pooling, and max_seq_length 512"
        )


def _load_sentence_transformer(path: Path) -> object:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise EncoderAssetError(
            "sentence-transformers is required to load the local BGE asset"
        ) from error
    return SentenceTransformer(
        str(path),
        device="cpu",
        trust_remote_code=False,
        local_files_only=True,
    )


def _utc_z(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return an aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
