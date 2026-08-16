from __future__ import annotations

import hashlib

from runtime._memory_vectors import BGE_DIMENSION, BGE_MODEL_ID, EncoderIdentity


class BgeSizedFakeEncoder:
    def __init__(self) -> None:
        self.encode_calls: list[tuple[str, ...]] = []
        self._identity = EncoderIdentity(
            model_id=BGE_MODEL_ID,
            revision="memory-e2e-fake-bge-v1",
            artifact_sha256=hashlib.sha256(b"memory-e2e-fake-bge-v1").hexdigest(),
            dimension=BGE_DIMENSION,
        )

    @property
    def identity(self) -> EncoderIdentity:
        return self._identity

    def encode(self, texts):
        batch = tuple(texts)
        self.encode_calls.append(batch)
        return [
            [
                float(hashlib.sha256(text.encode("utf-8")).digest()[index % 32] + 1)
                for index in range(BGE_DIMENSION)
            ]
            for text in batch
        ]

    def count_tokens(self, text: str) -> int:
        return min(512, max(1, len(text)))

