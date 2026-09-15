"""封装兼容 OpenAI/DeepSeek 协议的异步大模型调用。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from .config import settings


class LLMService:
    """按需创建异步客户端，并提供普通回答和流式回答两种调用方式。"""

    def __init__(self) -> None:
        # 延迟创建客户端，避免应用启动时因缺少密钥或网络而失败。
        self._client: AsyncOpenAI | None = None

    def status(self) -> dict[str, Any]:
        """返回前端健康检查所需的安全配置摘要，不暴露 API Key。"""
        return {
            "configured": settings.ai_configured,
            "provider": "DeepSeek / OpenAI compatible",
            "model": settings.model_name,
            "api_base": settings.api_base,
        }

    def client(self) -> AsyncOpenAI:
        """获取复用的异步客户端，并在配置缺失时给出明确错误。"""
        if not settings.ai_configured:
            raise RuntimeError(
                "DeepSeek API key is not configured. Set DEEPSEEK_API_KEY in backend/.env."
            )
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.api_key,
                base_url=settings.api_base,
                timeout=90,
                max_retries=1,
            )
        return self._client

    async def complete(self, messages: list[dict[str, str]]) -> str:
        """一次性获取完整回答。"""
        response = await self.client().chat.completions.create(
            model=settings.model_name,
            messages=messages,
            temperature=settings.temperature,
        )
        return response.choices[0].message.content or ""

    async def stream(
        self, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        """逐段产出模型生成的文本 token。"""
        stream = await self.client().chat.completions.create(
            model=settings.model_name,
            messages=messages,
            temperature=settings.temperature,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            content = chunk.choices[0].delta.content
            if content:
                yield content


# 进程内单例，复用连接池和 SDK 客户端。
llm_service = LLMService()
