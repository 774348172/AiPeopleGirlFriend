from __future__ import annotations

import hashlib
from dataclasses import dataclass

from runtime._memory_vectors import BGE_MODEL_ID, EncoderIdentity
from tests.memory_commit._helpers import append_evidence, batch, begin, memory_for, open_ledger


@dataclass
class FakeEncoder:
    revision: str = "test-revision-1"
    artifact_seed: str = "test-asset-1"
    dimension: int = 4

    @property
    def identity(self) -> EncoderIdentity:
        return EncoderIdentity(
            model_id=BGE_MODEL_ID,
            revision=self.revision,
            artifact_sha256=hashlib.sha256(self.artifact_seed.encode()).hexdigest(),
            dimension=self.dimension,
        )

    def encode(self, texts):
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append([float(digest[index] + 1) for index in range(self.dimension)])
        return vectors


def commit_memory(ledger, *, memory_id="memory-1", run_id="proposal-run-1"):
    event = append_evidence(ledger, request_id=f"request-{run_id}")
    store = ledger.memory_store()
    begin(store, event, proposal_run_id=run_id, key=f"begin-{run_id}")
    memory = memory_for(event, memory_id=memory_id, proposal_run_id=run_id)
    store.commit_batch(batch(memory, proposal_run_id=run_id), idempotency_key=f"commit-{run_id}")
    return event, store


def activate(store, event, memory_id="memory-1"):
    return store.decide_memory(
        memory_id=memory_id,
        to_status="active",
        reason_code="evidence_validated",
        idempotency_key=f"activate-{memory_id}",
        source_event_id=event.event_id,
    )


__all__ = [
    "FakeEncoder",
    "activate",
    "append_evidence",
    "commit_memory",
    "memory_for",
    "open_ledger",
]
