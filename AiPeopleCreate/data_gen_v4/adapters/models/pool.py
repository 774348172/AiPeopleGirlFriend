"""ModelAdapter 实现（《数据生成器v4设计》§7.4）。

- OpenAICompatModelAdapter：OpenAI 兼容 HTTP，失败显式抛 provider_error，不回退 mock。
  T4 错误分类（2026-08-06）：计费/鉴权/环境（401/402/403/缺依赖）标 retryable=False
  fail-fast；限流/5xx/超时/空内容标 retryable=True 交给 CallExecutor 重试。
- StrictTestModelAdapter：按 spec 的 response script 返回/抛错；仅供测试，产物不进 release。
- ModelPool：adapter 注册表 + config 解析。
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from data_gen_v4.core.errors import V4Error, V4ErrorCode


# 不可重试的 HTTP 状态：重试无意义（计费/鉴权/请求非法）
_NON_RETRYABLE_STATUS = {400, 401, 402, 403, 404, 405, 422}


def _classify_api_error(error: Exception) -> bool:
    """按异常结构分类是否可重试（不依赖 openai 包类型，防环境缺包场景）。"""
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status not in _NON_RETRYABLE_STATUS
    name = type(error).__name__.lower()
    # openai 包内的典型瞬态/非瞬态类型名
    if any(token in name for token in ("authentication", "permission", "badrequest", "notfound")):
        return False
    return True  # 连接/超时/限流/服务端错误等默认可重试


class OpenAICompatModelAdapter:
    """OpenAI 兼容模型适配器。spec 字段：
    model / base_url / api_key_env / temperature / max_tokens / seed / messages。
    """

    def __init__(self, *, base_url: str | None = None,
                 api_key_env: str = "OPENAI_API_KEY", default_model: str = "gpt-4o-mini") -> None:
        self._base_url = base_url
        self._api_key_env = api_key_env
        self._default_model = default_model
        self._client = None
        self._client_lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []

    def _ensure_client(self, base_url: str | None, api_key: str) -> Any:
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    try:
                        from openai import OpenAI

                        # timeout=120s：长请求挂起时快速失败重试，防止 worker 无限等待
                        # （曾出现 API 请求挂起 >10 分钟导致整批白跑 2 小时）
                        self._client = OpenAI(
                            base_url=base_url or self._base_url,
                            api_key=api_key,
                            timeout=120.0,
                        )
                    except Exception as error:  # noqa: BLE001
                        # 客户端初始化失败 = 环境问题（缺包/配置错），重试无意义
                        raise V4Error(
                            f"OpenAI 客户端初始化失败: {error}",
                            code=V4ErrorCode.PROVIDER_ERROR,
                            retryable=False,
                        ) from error
        return self._client

    def generate(self, spec: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(spec)
        api_key = os.getenv(spec.get("api_key_env", self._api_key_env), "")
        if not api_key:
            raise V4Error("未配置 API key（显式失败，不回退 mock）", code=V4ErrorCode.PROVIDER_ERROR, retryable=False)
        client = self._ensure_client(spec.get("base_url"), api_key)
        messages = spec.get("messages")
        if not isinstance(messages, list) or not messages:
            raise V4Error("ModelCallSpec 缺少 messages", code=V4ErrorCode.PROVIDER_ERROR, retryable=False)
        try:
            # ModelCallSpec.model 是对象（§7.4：provider/model/revision 逻辑标识）；
            # 真实 API 模型名以字符串 spec 或 adapter 配置为准
            model_spec = spec.get("model")
            if isinstance(model_spec, str) and model_spec:
                model_name = model_spec
            else:
                model_name = self._default_model
            kwargs: dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "temperature": spec.get("temperature", 0.7),
                "max_tokens": spec.get("max_tokens", 1024),
            }
            if isinstance(spec.get("extra_body"), dict):  # provider 特定参数（如禁思考）
                kwargs["extra_body"] = spec["extra_body"]
            seed = spec.get("seed")
            if isinstance(seed, int):  # 真实 API 只接受整数 seed；字符串（如 "42:sim"）不传
                kwargs["seed"] = seed
            response = client.chat.completions.create(**kwargs)
        except Exception as error:  # noqa: BLE001
            retryable = _classify_api_error(error)
            raise V4Error(
                f"模型调用失败: {error}",
                code=V4ErrorCode.PROVIDER_ERROR,
                retryable=retryable,
            ) from error
        message = response.choices[0].message
        content = getattr(message, "content", None) or ""
        if not content:
            # reasoning 模型（如 deepseek）可能把正文放在 reasoning_content
            content = getattr(message, "reasoning_content", None) or ""
        if not content:
            raise V4Error("模型返回空内容", code=V4ErrorCode.PROVIDER_ERROR, retryable=True)
        return {
            "content": content,
            "finish_reason": str(getattr(response.choices[0], "finish_reason", "")),
        }


class StrictTestModelAdapter:
    """严格测试适配器：按调用次序从 response script 返回或抛错。

    script 元素为 {"content": str, "finish_reason": str} 或异常实例。
    超出 script 长度时抛 provider_error（不静默循环）。
    """

    def __init__(self, script: list[Any] | None = None) -> None:
        self.script: list[Any] = script or []
        self.calls: list[dict[str, Any]] = []

    def generate(self, spec: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(spec)
        index = len(self.calls) - 1
        if index >= len(self.script):
            raise V4Error(
                f"StrictTestModelAdapter 调用超出 script（{len(self.script)} 次）",
                code=V4ErrorCode.PROVIDER_ERROR,
            )
        entry = self.script[index]
        if isinstance(entry, Exception):
            raise entry
        return {
            "content": entry.get("content", ""),
            "finish_reason": entry.get("finish_reason", "stop"),
        }


@dataclass
class ModelPool:
    """adapter 注册表：按 spec 的 adapter_id（缺省 default_id）解析。"""

    adapters: dict[str, Any] = field(default_factory=dict)
    default_id: str = "openai-compat"

    def register(self, adapter_id: str, adapter: Any) -> None:
        self.adapters[adapter_id] = adapter

    def resolve(self, spec: dict[str, Any]) -> Any:
        adapter_id = spec.get("adapter_id", self.default_id)
        adapter = self.adapters.get(adapter_id)
        if adapter is None:
            raise V4Error(
                f"model adapter 未注册: {adapter_id}", code=V4ErrorCode.PROVIDER_ERROR
            )
        return adapter
