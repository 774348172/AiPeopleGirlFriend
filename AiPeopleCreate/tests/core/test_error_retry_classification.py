"""T4：错误分类与重试语义（2026-08-06）。

覆盖：
- CallExecutor：retryable=False fail-fast（1 次调用即失败）；retryable=True 重试至上限；
- _classify_api_error：402/401 等不可重试，429/5xx 可重试（不依赖 openai 包）；
- reply._parse_object：JSON 前后夹带说明文字的容错恢复。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from data_gen_v4.adapters.models.pool import _classify_api_error
from data_gen_v4.adapters.modes.reply import _parse_object
from data_gen_v4.core.engine import CallExecutor
from data_gen_v4.core.errors import V4Error, V4ErrorCode


class _FakePool:
    """按脚本抛错/返回的假 pool。"""

    def __init__(self, script: list) -> None:
        self.script = script
        self.calls = 0

    def resolve(self, spec: dict):
        return self

    def generate(self, spec: dict):
        self.calls += 1
        entry = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(entry, Exception):
            raise entry
        return entry


class _Sink:
    def append(self, record, key):
        pass


class _Header:
    run_id = "run-test"
    plan_id = "plan-test"
    record_id = "rec-test"

    def with_record(self, record_type):
        return self


def _executor(pool, max_failures=3):
    return CallExecutor(
        pool, _Sink(), _Header(), max_consecutive_failures=max_failures, retry_base_delay_seconds=0
    )


def test_non_retryable_infra_error_fails_fast_with_one_call():
    pool = _FakePool([V4Error("402 计费错误", code=V4ErrorCode.PROVIDER_ERROR, retryable=False)])
    executor = _executor(pool)
    with pytest.raises(V4Error, match="不可重试"):
        executor.call({"prompt_hash": "h"}, stage="semantic", attempt_no=1)
    assert pool.calls == 1  # fail-fast：不重试


def test_retryable_infra_error_retries_until_success():
    pool = _FakePool(
        [
            V4Error("429 限流", code=V4ErrorCode.PROVIDER_ERROR, retryable=True),
            V4Error("5xx 服务端", code=V4ErrorCode.PROVIDER_ERROR, retryable=True),
            {"content": "ok", "finish_reason": "stop"},
        ]
    )
    executor = _executor(pool)
    result = executor.call({"prompt_hash": "h"}, stage="semantic", attempt_no=1)
    assert result["content"] == "ok"
    assert pool.calls == 3


def test_retryable_infra_error_exhausts_then_raises():
    pool = _FakePool([V4Error("持续失败", code=V4ErrorCode.PROVIDER_ERROR, retryable=True)])
    executor = _executor(pool)
    with pytest.raises(V4Error, match="3 次"):
        executor.call({"prompt_hash": "h"}, stage="semantic", attempt_no=1)
    assert pool.calls == 3


def test_classify_api_error_by_status_code():
    class FakeHttpError(Exception):
        status_code = 402

    class FakeRateLimit(Exception):
        status_code = 429

    class FakeServerError(Exception):
        status_code = 500

    class FakeAuth(Exception):
        status_code = 401

    assert _classify_api_error(FakeHttpError()) is False
    assert _classify_api_error(FakeAuth()) is False
    assert _classify_api_error(FakeRateLimit()) is True
    assert _classify_api_error(FakeServerError()) is True


def test_classify_api_error_by_type_name_without_openai():
    class AuthenticationError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    assert _classify_api_error(AuthenticationError()) is False
    assert _classify_api_error(APITimeoutError()) is True


def test_parse_object_recovers_json_with_surrounding_prose():
    text = "好的，以下是 JSON：\n{\"a\": 1, \"b\": [2, 3]}\n希望你喜欢。"
    assert _parse_object(text, "测试") == {"a": 1, "b": [2, 3]}


def test_parse_object_still_rejects_empty_or_garbage():
    from data_gen_v4.adapters.modes.reply import _ModeFailureSignal

    with pytest.raises(_ModeFailureSignal):
        _parse_object("", "测试")
    with pytest.raises(_ModeFailureSignal):
        _parse_object("完全没有 JSON 内容", "测试")
