from __future__ import annotations

import math
import struct

import pytest

from runtime._memory_vectors import BGE_DIMENSION, BGE_MODEL_ID, EncoderIdentity
from runtime._selector_query import (
    MAX_QUERY_TOKENS,
    SelectorEncoderMismatchError,
    SelectorQueryTooLongError,
    SelectorVectorIntegrityError,
    encode_selector_query,
    selector_encoder_profile,
)
from tests.selector_query._helpers import FakeBge, IDENTITY, make_context
from runtime._selector_query import build_selector_query


def test_query_is_counted_and_encoded_exactly_once_as_one_row() -> None:
    query = build_selector_query(make_context(history_texts=("问", "答")))
    backend = FakeBge()
    result = encode_selector_query(
        query,
        encoder=backend,
        tokenizer=backend,
        expected_encoder=IDENTITY,
    )
    assert backend.token_calls == [result.rendered_text]
    assert backend.encode_calls == [(result.rendered_text,)]
    assert result.token_count == 64
    assert len(result.vector) == BGE_DIMENSION
    assert result.vector == struct.unpack(f"<{BGE_DIMENSION}f", result.vector_blob)
    assert all(math.isfinite(value) for value in result.vector)
    assert math.isclose(math.sqrt(sum(value * value for value in result.vector)), 1.0, abs_tol=1e-5)
    assert len(result.vector_sha256) == 64


def test_encoder_or_tokenizer_identity_drift_is_rejected_before_encoding() -> None:
    query = build_selector_query(make_context())
    drift = EncoderIdentity(
        model_id=BGE_MODEL_ID,
        revision="different-revision",
        artifact_sha256="b" * 64,
        dimension=BGE_DIMENSION,
    )
    backend = FakeBge(identity=drift)
    with pytest.raises(SelectorEncoderMismatchError, match="identical identity"):
        encode_selector_query(
            query,
            encoder=backend,
            tokenizer=backend,
            expected_encoder=IDENTITY,
        )
    assert backend.encode_calls == []


def test_token_limit_is_enforced_before_encoder_call() -> None:
    query = build_selector_query(make_context())
    backend = FakeBge(token_count=MAX_QUERY_TOKENS + 1)
    with pytest.raises(SelectorQueryTooLongError, match="512-token"):
        encode_selector_query(
            query,
            encoder=backend,
            tokenizer=backend,
            expected_encoder=IDENTITY,
        )
    assert backend.encode_calls == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_vectors_are_rejected(bad: float) -> None:
    query = build_selector_query(make_context())
    backend = FakeBge()
    backend.encode = lambda texts: [[bad] * BGE_DIMENSION]
    with pytest.raises(SelectorVectorIntegrityError, match="non-finite"):
        encode_selector_query(
            query,
            encoder=backend,
            tokenizer=backend,
            expected_encoder=IDENTITY,
        )


def test_wrong_dimension_and_zero_vector_are_rejected() -> None:
    query = build_selector_query(make_context())
    backend = FakeBge()
    backend.encode = lambda texts: [[1.0] * (BGE_DIMENSION - 1)]
    with pytest.raises(SelectorVectorIntegrityError, match="dimension"):
        encode_selector_query(
            query,
            encoder=backend,
            tokenizer=backend,
            expected_encoder=IDENTITY,
        )
    backend.encode = lambda texts: [[0.0] * BGE_DIMENSION]
    with pytest.raises(SelectorVectorIntegrityError, match="zero"):
        encode_selector_query(
            query,
            encoder=backend,
            tokenizer=backend,
            expected_encoder=IDENTITY,
        )


def test_profile_is_mem05_cpu_bge_and_has_no_network_path() -> None:
    assert selector_encoder_profile() == {
        "model_id": "BAAI/bge-small-zh-v1.5",
        "dimension": 512,
        "pooling": "mean",
        "max_sequence_length": 512,
        "normalization": "runtime_l2",
        "dtype": "float32le",
        "device": "cpu",
        "network_access": "forbidden",
    }

