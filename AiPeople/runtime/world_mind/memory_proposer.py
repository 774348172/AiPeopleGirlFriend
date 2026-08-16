from __future__ import annotations

import asyncio
import json
from pathlib import Path

from runtime._memory_contracts import (
    MemoryProposalDraft,
)
from runtime.adapters.llama_cpp import GenerationOptions

from .memory_contracts import HeroineMemoryProposeRequest
from .model_failures import (
    ClassifiedModelError,
    ModelAttemptObserver,
    classify_model_failure,
    record_attempt,
)
from .real_model_gateway import WorldMindChatBackend, WorldMindModelIdentity
from .short_protocol import (
    R2_SYSTEM_BOUNDARY,
    compile_r2,
    r2_payload,
)

MEMORY_PROPOSE = "MEMORY_PROPOSE"
SCHEMA_PATH = Path(__file__).parents[1] / "schemas" / "heroine_memory_r2.schema.json"

MEMORY_PROPOSE_SYSTEM_BOUNDARY = R2_SYSTEM_BOUNDARY


class V6MemoryProposeError(ClassifiedModelError):
    pass


class LlamaCppHeroineMemoryProposer:
    def __init__(
        self,
        backend: WorldMindChatBackend,
        *,
        identity: WorldMindModelIdentity,
        character_prompt: str,
        timeout_seconds: float = 50.0,
        retries: int = 0,
        attempt_observer: ModelAttemptObserver | None = None,
        fail_closed: bool = True,
    ) -> None:
        if not isinstance(backend, WorldMindChatBackend):
            raise TypeError("backend must implement WorldMindChatBackend")
        if not isinstance(identity, WorldMindModelIdentity):
            raise TypeError("identity must be WorldMindModelIdentity")
        if not isinstance(character_prompt, str) or not character_prompt.strip():
            raise ValueError("character_prompt cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if retries not in {0, 1, 2}:
            raise ValueError("retries must be between 0 and 2")
        self.backend = backend
        self.identity = identity
        self.character_prompt = (
            "[后台角色边界]\n"
            f"当前女主角 character_id={identity.character_id}，"
            f"唯一世界 world_id={identity.world_id}。\n"
            "只从输入中的当前女主独立记忆库和事件证据提出候选。"
        )
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.attempt_observer = attempt_observer
        self.fail_closed = fail_closed
        self.last_failure: V6MemoryProposeError | None = None
        self._schema = _load_schema()

    async def propose(
        self, request: HeroineMemoryProposeRequest
    ) -> tuple[MemoryProposalDraft, ...]:
        self._validate_identity(request)
        request_id = _request_id(request)
        payload = r2_payload(request)
        messages = (
            {
                "role": "system",
                "content": f"{self.character_prompt}\n\n{MEMORY_PROPOSE_SYSTEM_BOUNDARY}",
            },
            {
                "role": "user",
                "content": json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        )
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "heroine_memory_r2",
                "strict": True,
                "schema": self._schema,
            },
        }
        last_error: BaseException | None = None
        for attempt in range(self.retries + 1):
            attempt_id = f"{request_id}:{MEMORY_PROPOSE}:{attempt}"
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    text = await self.backend.complete_chat(
                        request_id=attempt_id,
                        messages=messages,
                        options=GenerationOptions(
                            max_tokens=180,
                            temperature=0.15,
                            top_p=0.85,
                            repeat_penalty=1.05,
                        ),
                        response_format=response_format,
                    )
                drafts = compile_r2(_decode_result(text), request)
                self.last_failure = None
                record_attempt(
                    self.attempt_observer,
                    request_id=request_id,
                    mode=MEMORY_PROPOSE,
                    attempt=attempt,
                )
                return drafts
            except asyncio.CancelledError as error:
                record_attempt(
                    self.attempt_observer,
                    request_id=request_id,
                    mode=MEMORY_PROPOSE,
                    attempt=attempt,
                    error=error,
                )
                raise
            except BaseException as error:
                last_error = error
                record_attempt(
                    self.attempt_observer,
                    request_id=request_id,
                    mode=MEMORY_PROPOSE,
                    attempt=attempt,
                    error=error,
                )
        assert last_error is not None
        failure = V6MemoryProposeError(
            "MEMORY_PROPOSE failed after retries",
            code=classify_model_failure(last_error),
            mode=MEMORY_PROPOSE,
        )
        self.last_failure = failure
        if self.fail_closed:
            return ()
        raise failure from last_error

    def _validate_identity(self, request: HeroineMemoryProposeRequest) -> None:
        if (
            request.owner_character_id != self.identity.character_id
            or request.world_id != self.identity.world_id
        ):
            raise V6MemoryProposeError(
                "memory proposer identity does not match repository",
                code="model_contract_invalid",
                mode=MEMORY_PROPOSE,
            )


def _request_id(request: HeroineMemoryProposeRequest) -> str:
    event_ids = ",".join(event.event_id for event in request.source_events)
    return f"{request.save_id}:{request.owner_character_id}:{event_ids}"


def _decode_result(text: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise
    if not isinstance(value, dict):
        raise ValueError("MEMORY_PROPOSE output must be an object")
    return value


def _load_schema() -> dict[str, object]:
    value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("MEMORY_PROPOSE schema must be an object")
    return value
