"""V4 标准错误码与异常层级（《数据生成器v4设计》§13.2）。

错误码与阶段 0 冻结的 record_v4.schema.json 中 FailureRecord.error_code 枚举一致。
异常携带 code，可机械映射为 FailureRecord。
"""
from __future__ import annotations

from enum import Enum


class V4ErrorCode(str, Enum):
    PACKAGE_INVALID = "package_invalid"
    PACKAGE_INCOMPATIBLE = "package_incompatible"
    SOURCE_SNAPSHOT_FAILED = "source_snapshot_failed"
    PRECONDITION_FAILED = "precondition_failed"
    MODE_NOT_REGISTERED = "mode_not_registered"
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    TRUNCATED_OUTPUT = "truncated_output"
    PARSE_ERROR = "parse_error"
    SCHEMA_ERROR = "schema_error"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    POLICY_MISMATCH = "policy_mismatch"
    STYLE_FAILURE = "style_failure"
    NO_ACCEPTABLE_CANDIDATE = "no_acceptable_candidate"

    # 内部错误（不直接映射为 failure record，用于编程错误）
    INTERNAL = "internal_error"


class V4Error(RuntimeError):
    """所有 V4 错误的基类。

    retryable（2026-08-06 T4 引入）：
    - False：不可重试（环境/计费/请求非法，重试无意义，应 fail-fast）；
    - True：可重试（限流/5xx/超时/空内容等瞬态）；
    - None：按错误码默认推断（多数错误码默认 False）。
    """

    code: V4ErrorCode = V4ErrorCode.INTERNAL

    def __init__(
        self,
        message: str,
        *,
        code: V4ErrorCode | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.retryable = retryable


class PackageInvalidError(V4Error):
    code = V4ErrorCode.PACKAGE_INVALID


class PackageIncompatibleError(V4Error):
    code = V4ErrorCode.PACKAGE_INCOMPATIBLE


class SourceSnapshotFailedError(V4Error):
    code = V4ErrorCode.SOURCE_SNAPSHOT_FAILED


class PreconditionFailedError(V4Error):
    code = V4ErrorCode.PRECONDITION_FAILED


class ModeNotRegisteredError(V4Error):
    code = V4ErrorCode.MODE_NOT_REGISTERED


class SchemaValidationError(V4Error):
    code = V4ErrorCode.SCHEMA_ERROR


class IdempotencyConflictError(V4Error):
    """同一幂等键提交了不同内容。"""

    code = V4ErrorCode.INTERNAL


class LockMismatchError(V4Error):
    """plan/lock 与运行记录不一致，禁止 resume。"""

    code = V4ErrorCode.PACKAGE_INCOMPATIBLE


class RecordNotFoundError(V4Error):
    code = V4ErrorCode.INTERNAL


class SinkLockedError(V4Error):
    """sink 已被其他写者占用（单写者约束）。"""

    code = V4ErrorCode.INTERNAL
