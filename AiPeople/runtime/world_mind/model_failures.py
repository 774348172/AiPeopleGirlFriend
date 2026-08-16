from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Callable


MODEL_SERVICE_UNAVAILABLE = "model_service_unavailable"
MODEL_TIMEOUT = "model_timeout"
MODEL_CANCELLED = "model_cancelled"
MODEL_EMPTY_OUTPUT = "model_empty_output"
MODEL_INVALID_JSON = "model_invalid_json"
MODEL_SCHEMA_INVALID = "model_schema_invalid"
MODEL_CONTRACT_INVALID = "model_contract_invalid"
MODEL_REQUEST_INVALID = "model_request_invalid"
MODEL_UNKNOWN_FAILURE = "model_unknown_failure"


@dataclass(frozen=True, slots=True)
class ModelAttemptAudit:
    request_id: str
    mode: str
    attempt: int
    success: bool
    failure_code: str | None


ModelAttemptObserver = Callable[[ModelAttemptAudit], None]


class ClassifiedModelError(RuntimeError):
    def __init__(self, message: str, *, code: str, mode: str) -> None:
        super().__init__(message)
        self.code = code
        self.mode = mode


def classify_model_failure(error: BaseException) -> str:
    if isinstance(error, asyncio.CancelledError):
        return MODEL_CANCELLED
    if isinstance(error, TimeoutError):
        return MODEL_TIMEOUT
    if isinstance(error, json.JSONDecodeError):
        return MODEL_INVALID_JSON
    name = type(error).__name__.lower()
    message = str(error).lower()
    if "timeout" in name or "timed out" in message:
        return MODEL_TIMEOUT
    if "notready" in name or "unavailable" in name or "connection" in message:
        return MODEL_SERVICE_UNAVAILABLE
    if "empty" in message and "output" in message:
        return MODEL_EMPTY_OUTPUT
    if "json" in message:
        return MODEL_INVALID_JSON
    if (
        "http 400" in message
        or "bad request" in message
        or "invalid structured output" in message
        or "context window" in message
    ):
        return MODEL_REQUEST_INVALID
    if (
        "snapshot" in message
        or "identity" in message
        or "version" in message
        or "escaped" in message
        or "outside" in message
    ):
        return MODEL_CONTRACT_INVALID
    if "schema" in message or "fields do not match" in message:
        return MODEL_SCHEMA_INVALID
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code
    return MODEL_UNKNOWN_FAILURE


def record_attempt(
    observer: ModelAttemptObserver | None,
    *,
    request_id: str,
    mode: str,
    attempt: int,
    error: BaseException | None = None,
) -> None:
    if observer is None:
        return
    observer(
        ModelAttemptAudit(
            request_id=request_id,
            mode=mode,
            attempt=attempt,
            success=error is None,
            failure_code=None if error is None else classify_model_failure(error),
        )
    )
