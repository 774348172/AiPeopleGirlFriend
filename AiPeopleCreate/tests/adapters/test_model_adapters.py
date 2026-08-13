"""ModelAdapter 与 ModelPool：显式失败语义、严格测试 seam、注册表。"""
from __future__ import annotations

import pytest

from data_gen_v4.adapters.models.pool import (
    ModelPool,
    OpenAICompatModelAdapter,
    StrictTestModelAdapter,
)
from data_gen_v4.core.errors import V4Error, V4ErrorCode


def test_strict_test_adapter_returns_script_in_order():
    adapter = StrictTestModelAdapter(
        [{"content": "甲", "finish_reason": "stop"}, {"content": "乙"}]
    )
    assert adapter.generate({})["content"] == "甲"
    assert adapter.generate({})["content"] == "乙"
    assert len(adapter.calls) == 2


def test_strict_test_adapter_raises_when_script_exhausted():
    adapter = StrictTestModelAdapter([{"content": "甲"}])
    adapter.generate({})
    with pytest.raises(V4Error) as error:
        adapter.generate({})
    assert error.value.code == V4ErrorCode.PROVIDER_ERROR


def test_strict_test_adapter_can_raise_configured_error():
    adapter = StrictTestModelAdapter([TimeoutError("模拟超时")])
    with pytest.raises(TimeoutError):
        adapter.generate({})


def test_model_pool_resolves_registered_adapter():
    pool = ModelPool(adapters={"test": StrictTestModelAdapter()})
    resolved = pool.resolve({"adapter_id": "test"})
    assert isinstance(resolved, StrictTestModelAdapter)


def test_model_pool_default_adapter():
    pool = ModelPool(adapters={"openai-compat": object()})
    assert pool.resolve({}) is pool.adapters["openai-compat"]


def test_model_pool_missing_adapter_fails():
    pool = ModelPool(adapters={})
    with pytest.raises(V4Error):
        pool.resolve({"adapter_id": "ghost"})


def test_openai_compat_fails_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = OpenAICompatModelAdapter()
    with pytest.raises(V4Error) as error:
        adapter.generate({"messages": [{"role": "user", "content": "hi"}]})
    assert error.value.code == V4ErrorCode.PROVIDER_ERROR
    assert "mock" in str(error.value)  # 显式失败声明，不回退 mock


def test_openai_compat_requires_messages():
    adapter = OpenAICompatModelAdapter()
    with pytest.raises(V4Error):
        adapter.generate({"messages": []})
