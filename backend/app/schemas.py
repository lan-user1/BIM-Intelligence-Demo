"""定义聊天、知识库重建等 API 的请求和响应数据结构。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """发送给模型服务的单条历史消息。"""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    """聊天接口请求体，包含问题、历史记录和可选的模型上下文。"""

    question: str = Field(min_length=1, max_length=3000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    model_id: str | None = None
    model_context: dict | None = None


class ReindexResponse(BaseModel):
    """知识库重建接口返回的索引统计结果。"""

    status: str
    documents: int
    chunks: int
    message: str
