from __future__ import annotations

from collections.abc import Callable

from .contracts import RuntimeSessionIdentity
from .memory_contracts import HeroineMemoryProposer
from .memory_repository import HeroineMemoryRepositoryFactory
from .persistence import R1MemoryJob, WorldMindStore


class R1MemoryExecutionError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class R1MemoryWorker:
    def __init__(
        self,
        *,
        store: WorldMindStore,
        repository_factory: HeroineMemoryRepositoryFactory,
        proposer_provider: Callable[[RuntimeSessionIdentity], HeroineMemoryProposer],
    ) -> None:
        self.store = store
        self.repository_factory = repository_factory
        self.proposer_provider = proposer_provider

    async def run_memory_job(self, job: R1MemoryJob) -> None:
        self.store.validate_r1_memory_job_sources(job)
        repository = self.repository_factory.open(job.session)
        proposer = self.proposer_provider(job.session)
        if not isinstance(proposer, HeroineMemoryProposer):
            raise R1MemoryExecutionError(
                "memory_proposer_not_configured",
                retryable=False,
            )
        try:
            proposals = await repository.propose(
                (job.user_event_id, job.assistant_event_id),
                proposer,
                participant_ids=(
                    job.session.protagonist_id,
                    job.session.active_character_id,
                ),
            )
            failure = getattr(proposer, "last_failure", None)
            if failure is not None:
                raise R1MemoryExecutionError(
                    getattr(failure, "code", "memory_propose_failed"),
                    retryable=_retryable_failure(getattr(failure, "code", "")),
                )
            memories = tuple(repository.materialize(proposal) for proposal in proposals)
            if memories:
                repository.commit_r1_job(job.job_id, memories)
            else:
                self.store.complete_r1_memory_job(job.job_id)
        except R1MemoryExecutionError:
            raise
        except Exception as error:
            code = getattr(error, "code", type(error).__name__)
            raise R1MemoryExecutionError(
                str(code),
                retryable=_retryable_failure(str(code)),
            ) from error


def _retryable_failure(code: str) -> bool:
    return code not in {
        "model_contract_invalid",
        "model_request_invalid",
        "model_schema_invalid",
        "model_invalid_json",
        "HeroineMemoryOwnershipError",
        "HeroineMemoryEvidenceError",
        "HeroineMemoryContractError",
        "memory_proposer_not_configured",
    }
