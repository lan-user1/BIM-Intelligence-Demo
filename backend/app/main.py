"""FastAPI 入口，提供模型、知识库和 AI 问答相关接口。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .config import settings
from .knowledge import knowledge_index
from .llm import llm_service
from .model_service import model_service
from .rules import RuleEngine
from .schemas import ChatRequest, ReindexResponse


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动时后台建立知识索引，关闭时取消未完成的后台任务。"""
    index_task = asyncio.create_task(run_in_threadpool(knowledge_index.refresh))
    try:
        yield
    finally:
        if not index_task.done():
            index_task.cancel()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="IFC model analysis and DeepSeek-powered BIM assistant.",
    lifespan=lifespan,
)

app.add_middleware(
    # 允许 Vite 开发服务器或配置的前端域名跨域访问。
    CORSMiddleware,
    allow_origins=list(settings.frontend_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """返回服务状态、AI 配置摘要和当前模型数量。"""
    return {
        "status": "ok",
        "app": settings.app_name,
        "ai": llm_service.status(),
        "models": len(model_service.list_models()),
    }


@app.get("/api/models")
async def list_models() -> dict[str, Any]:
    """列出扫描到的 IFC/RVT 模型。"""
    models = model_service.list_models()
    return {"items": models, "total": len(models)}


@app.get("/api/models/{model_id}")
async def get_model(model_id: str) -> dict[str, Any]:
    """获取完整解析结果；解析可能在磁盘缓存命中时直接返回。"""
    return await run_in_threadpool(model_service.get_model, model_id)


@app.get("/api/models/{model_id}/elements")
async def get_elements(
    model_id: str,
    q: str = "",
    element_type: str | None = None,
    storey_id: int | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """分页查询构件，支持名称、类型和楼层过滤。"""
    return await run_in_threadpool(
        model_service.get_elements,
        model_id,
        query=q,
        element_type=element_type,
        storey_id=storey_id,
        offset=offset,
        limit=limit,
    )


@app.get("/api/models/{model_id}/content")
async def get_model_content(model_id: str) -> FileResponse:
    """以原始文件形式返回模型，供浏览器端 WebIFC 解析。"""
    info = model_service.get_info(model_id)
    return FileResponse(
        info.path,
        media_type="application/octet-stream",
        filename=info.file_name,
    )


@app.post("/api/models/upload", status_code=201)
async def upload_model(file: UploadFile = File(...)) -> dict[str, Any]:
    """保存上传文件，并尝试立即解析 IFC 以返回首屏统计信息。"""
    info = await model_service.save_upload(file)
    if info["parseable"]:
        try:
            model = await run_in_threadpool(model_service.get_model, info["id"])
            info["parsed"] = True
            info["statistics"] = model["statistics"]
        except HTTPException as exc:
            info["parse_error"] = exc.detail
    return info


@app.get("/api/knowledge/status")
async def knowledge_status() -> dict[str, Any]:
    """获取本地文档索引状态。"""
    return knowledge_index.status()


@app.post("/api/knowledge/reindex", response_model=ReindexResponse)
async def reindex_knowledge() -> dict[str, Any]:
    """同步重建知识索引，失败时返回 500 和具体错误。"""
    await run_in_threadpool(knowledge_index.refresh)
    status = knowledge_index.status()
    if status["state"] == "error":
        raise HTTPException(status_code=500, detail=status["error"])
    return {
        "status": status["state"],
        "documents": status["documents"],
        "chunks": status["chunks"],
        "message": "Knowledge index rebuilt.",
    }


def _rule_answer(
    model_id: str, question: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """规则引擎先行:命中返回 (规则结果, 模型数据),未命中返回 None。"""
    model = model_service.get_model(model_id)
    result = RuleEngine(model).ask(question)
    if result is None:
        return None
    return result, model


def _rule_meta(
    request: ChatRequest, result: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """组装规则引擎回答的 meta 事件:回答模式、证据、来源与备注。"""
    return {
        "mode": "rule",
        "model_id": request.model_id,
        "sources": [
            {"type": "ifc", "title": model["file_name"], "model_id": model["id"]}
        ],
        "evidence": result["evidence"][:20],
        "note": result["note"],
    }


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict[str, Any]:
    """非流式问答接口：规则引擎命中直接返回，否则调用大模型。"""
    rule_hit = None
    if request.model_id:
        try:
            rule_hit = await run_in_threadpool(
                _rule_answer, request.model_id, request.question
            )
        except HTTPException:
            # 模型解析失败时让 LLM 分支自行处理并返回同样的错误。
            rule_hit = None
    if rule_hit is not None:
        result, model = rule_hit
        return {
            "answer": result["answer"],
            "mode": "rule",
            "value": result["value"],
            "evidence": result["evidence"][:20],
            "note": result["note"],
            "sources": [
                {"type": "ifc", "title": model["file_name"], "model_id": model["id"]}
            ],
            "model_id": request.model_id,
        }
    if not settings.ai_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "DeepSeek API key is not configured. Copy backend/.env.example "
                "to backend/.env and set DEEPSEEK_API_KEY."
            ),
        )
    context = await run_in_threadpool(
        _build_context,
        request.question,
        request.model_id,
        request.model_context,
    )
    answer = await llm_service.complete(_build_messages(request, context))
    return {
        "answer": answer,
        "mode": "llm",
        "sources": context["sources"],
        "model_id": request.model_id,
    }


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """通过 SSE 返回元信息、文本 token 和完成/错误事件。"""
    rule_hit = None
    if request.model_id:
        try:
            rule_hit = await run_in_threadpool(
                _rule_answer, request.model_id, request.question
            )
        except HTTPException:
            rule_hit = None

    if rule_hit is not None:
        result, model = rule_hit
        meta = _rule_meta(request, result, model)

        async def event_stream():
            # 规则引擎回答:meta 后一次性发送完整文本。
            yield _sse("meta", meta)
            yield _sse("token", {"text": result["answer"]})
            yield _sse("done", {})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if not settings.ai_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "DeepSeek API key is not configured. Copy backend/.env.example "
                "to backend/.env and set DEEPSEEK_API_KEY."
            ),
        )

    context = await run_in_threadpool(
        _build_context,
        request.question,
        request.model_id,
        request.model_context,
    )
    messages = _build_messages(request, context)

    async def event_stream():
        # 先发送来源和模型信息，再逐 token 发送回答内容。
        yield _sse(
            "meta",
            {
                "mode": "llm",
                "model": settings.model_name,
                "model_id": request.model_id,
                "sources": context["sources"],
            },
        )
        try:
            async for token in llm_service.stream(messages):
                yield _sse("token", {"text": token})
            yield _sse("done", {})
        except Exception as exc:  # The stream has already started, report in-band.
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # 禁用代理和浏览器缓存，保证 token 实时到达。
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _build_context(
    question: str,
    model_id: str | None,
    submitted_model_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装模型摘要、匹配构件和本地知识片段，作为 AI 回答依据。"""
    model_context: dict[str, Any] | None = None
    matched_elements: list[dict[str, Any]] = []
    if model_id:
        model = model_service.get_model(model_id)
        from .ifc_parser import search_elements

        matched_elements = search_elements(model["elements"], question)
        model_context = {
            "id": model["id"],
            "file_name": model["file_name"],
            "schema": model["schema"],
            "project": model["project"],
            "statistics": model["statistics"],
            "units": model["units"],
            "storeys": model["storeys"],
            "category_counts": model["category_counts"],
            "element_counts": model["element_counts"][:40],
            "top_properties": model["top_properties"],
            "quantity_totals": model["quantity_totals"],
            "materials": {
                "count": len(model.get("materials", [])),
                "names": [m.get("name") for m in model.get("materials", [])][:30],
            },
            "matched_elements": matched_elements,
        }
    if submitted_model_context:
        # 前端上下文仅保留允许字段，并限制体积以防提示词过长。
        submitted_context = _sanitize_submitted_context(submitted_model_context)
        if model_context is None:
            model_context = submitted_context
        else:
            model_context["submitted_context"] = submitted_context

    if not knowledge_index.ready:
        knowledge_index.refresh()
    knowledge = knowledge_index.search(question, limit=5)
    sources = []
    if model_context:
        sources.append(
            {
                "type": "ifc",
                "title": model_context["file_name"],
                "model_id": model_context["id"],
            }
        )
    sources.extend(
        {
            "type": "document",
            "title": item["source"],
            "location": item["location"],
            "score": item["score"],
        }
        for item in knowledge
    )
    return {
        "model": model_context,
        "knowledge": knowledge,
        "sources": sources,
    }


def _sanitize_submitted_context(payload: dict[str, Any]) -> dict[str, Any]:
    """过滤客户端传入的模型上下文，并在超长时降级为核心摘要。"""
    allowed_keys = {
        "id",
        "file_name",
        "file_size",
        "format",
        "schema",
        "project",
        "statistics",
        "storeys",
        "element_counts",
        "category_counts",
        "units",
        "quantity_totals",
        "top_properties",
    }
    context = {key: value for key, value in payload.items() if key in allowed_keys}
    encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= 40_000:
        return context
    return {
        "id": context.get("id"),
        "file_name": context.get("file_name"),
        "format": context.get("format"),
        "schema": context.get("schema"),
        "project": context.get("project"),
        "statistics": context.get("statistics"),
        "summary_note": "客户端模型详情超过请求限制，已仅保留核心摘要。",
    }


def _build_messages(
    request: ChatRequest,
    context: dict[str, Any],
) -> list[dict[str, str]]:
    """把系统约束、模型上下文、最近历史和当前问题拼成模型消息。"""
    model_json = json.dumps(
        context["model"],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    knowledge_text = "\n\n".join(
        f"[资料 {index}: {item['source']} {item['location']}]\n{item['text']}"
        for index, item in enumerate(context["knowledge"], start=1)
    )
    context_text = (
        f"当前 IFC 模型结构化解析结果：\n{model_json}\n\n"
        f"可能相关的资料片段：\n{knowledge_text or '无'}"
    )
    if len(context_text) > settings.max_context_chars:
        # 严格控制提示词长度，避免超出模型上下文窗口。
        context_text = context_text[: settings.max_context_chars] + "\n[上下文已截断]"

    system_prompt = (
        "你是专业的 BIM/IFC 数据助手，请按以下铁律回答：\n"
        "1. 只能依据下方提供的 IFC 解析结果和资料片段回答，禁止编造任何数字、名称或 GlobalId。\n"
        "2. 上下文里没有的数据，明确说明缺少什么，不要猜测。\n"
        "3. 涉及具体构件时引用其 GlobalId 或 IFC 类型、楼层、名称；先给结论，再列证据。\n"
        "4. 回答使用简洁中文，数字和单位要准确。\n\n"
        f"{context_text}"
    )
    history = [
        {"role": message.role, "content": message.content}
        for message in request.history[-10:]
    ]
    return [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": request.question},
    ]


def _sse(event: str, data: dict[str, Any]) -> str:
    """按照 SSE 协议序列化一个事件。"""
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "docs": "/docs",
        "health": "/api/health",
    }
