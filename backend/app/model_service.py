"""维护 IFC/RVT 模型注册表、解析缓存和上传文件。"""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from .config import settings
from .ifc_parser import model_to_cache_json, parse_ifc


@dataclass(frozen=True)
class ModelInfo:
    """模型注册表中的文件元数据。"""

    id: str
    file_name: str
    path: Path
    file_size: int
    format: str
    source: str
    parseable: bool

    def as_dict(self, parsed: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "format": self.format,
            "source": self.source,
            "parseable": self.parseable,
            "parsed": parsed,
        }


class ModelService:
    """提供模型扫描、详情解析、构件查询和文件上传能力。"""

    def __init__(self) -> None:
        # _models 保存文件索引，_cache 保存已解析结果，_locks 防止同一模型重复解析。
        self._models: dict[str, ModelInfo] = {}
        self._cache: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._registry_lock = threading.Lock()
        self.refresh()

    def refresh(self) -> None:
        """重新扫描内置示例目录和上传目录，替换当前模型注册表。"""
        with self._registry_lock:
            self._models = {}
            self._scan_directory(settings.source_dir, source="sample", prefix="sample")
            self._scan_directory(settings.upload_dir, source="upload", prefix="upload")

    def _scan_directory(self, directory: Path, source: str, prefix: str) -> None:
        """将目录中的 IFC/RVT 文件注册为稳定的模型 ID。"""
        if not directory.exists():
            return
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in {".ifc", ".rvt"}:
                continue
            model_id = f"{prefix}-{_slug(path.stem)}-{path.suffix[1:].lower()}"
            self._models[model_id] = ModelInfo(
                id=model_id,
                file_name=path.name,
                path=path,
                file_size=path.stat().st_size,
                format=path.suffix.removeprefix(".").upper(),
                source=source,
                parseable=path.suffix.lower() == ".ifc",
            )

    def list_models(self) -> list[dict[str, Any]]:
        """返回按来源和文件名排序的模型列表。"""
        return [
            model.as_dict(parsed=self.has_cached(model.id))
            for model in sorted(
                self._models.values(),
                key=lambda item: (item.source != "sample", item.file_name),
            )
        ]

    def has_cached(self, model_id: str) -> bool:
        """内存缓存或磁盘缓存任一存在即视为已解析。"""
        return model_id in self._cache or self._cache_path(model_id).exists()

    def get_info(self, model_id: str) -> ModelInfo:
        """获取模型元数据，不存在时转换为 404。"""
        model = self._models.get(model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="Model not found.")
        return model

    def get_model(self, model_id: str, refresh: bool = False) -> dict[str, Any]:
        """获取完整解析结果，并按文件大小、校验和和修改时间复用缓存。"""
        model = self.get_info(model_id)
        if not model.parseable:
            raise HTTPException(
                status_code=422,
                detail=(
                    "RVT is a proprietary binary format. Export it to IFC from "
                    "Revit before uploading."
                ),
            )

        lock = self._locks.setdefault(model_id, threading.Lock())
        with lock:
            # 内存缓存命中时无需访问磁盘。
            if not refresh and model_id in self._cache:
                return self._cache[model_id]

            cache_path = self._cache_path(model_id)
            if not refresh and cache_path.exists():
                try:
                    import json

                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                    stat = model.path.stat()
                    # 只有源文件未变化时才使用磁盘缓存。
                    if (
                        cached.get("file_size") == stat.st_size
                        and cached.get("checksum")
                        and cached.get("source_mtime_ns") == stat.st_mtime_ns
                    ):
                        self._cache[model_id] = cached
                        return cached
                except (OSError, ValueError):
                    pass

            try:
                parsed = parse_ifc(model.path, model_id)
            except (OSError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            parsed["source_mtime_ns"] = model.path.stat().st_mtime_ns
            # 同步更新内存缓存并持久化，供下次进程启动直接复用。
            self._cache[model_id] = parsed
            cache_path.write_text(model_to_cache_json(parsed), encoding="utf-8")
            return parsed

    def get_elements(
        self,
        model_id: str,
        *,
        query: str = "",
        element_type: str | None = None,
        storey_id: int | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """在主模型结果上执行查询、类型和楼层过滤，再按偏移分页。"""
        from .ifc_parser import element_matches

        model = self.get_model(model_id)
        matches = [
            element
            for element in model["elements"]
            if element_matches(element, query, element_type, storey_id)
        ]
        total = len(matches)
        return {
            "items": matches[offset : offset + limit],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    async def save_upload(self, upload: UploadFile) -> dict[str, Any]:
        """分块写入上传文件，并在超过大小限制时删除不完整文件。"""
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in {".ifc", ".rvt"}:
            raise HTTPException(
                status_code=415,
                detail="Only IFC and RVT files are accepted.",
            )

        max_bytes = settings.max_upload_mb * 1024 * 1024
        clean_name = Path(upload.filename or f"model{suffix}").name
        destination = settings.upload_dir / f"{uuid.uuid4().hex[:10]}-{clean_name}"
        bytes_written = 0
        try:
            with destination.open("wb") as handle:
                # 边读边计数，避免把大文件一次性放入内存。
                while chunk := await upload.read(1024 * 1024):
                    bytes_written += len(chunk)
                    if bytes_written > max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"File exceeds the {settings.max_upload_mb} MB limit.",
                        )
                    handle.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

        self.refresh()
        # 刷新注册表后按真实路径找回系统生成的模型 ID。
        model_id = next(
            (
                model.id
                for model in self._models.values()
                if model.path == destination
            ),
            None,
        )
        if model_id is None:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Failed to register upload.")
        return self.get_info(model_id).as_dict(parsed=False)

    def _cache_path(self, model_id: str) -> Path:
        """生成模型 ID 对应的磁盘缓存文件路径。"""
        return settings.cache_dir / f"{_slug(model_id)}.json"


# 将模型 ID 收敛为文件名安全、长度受限的字符串。
def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower()
    return value[:100] or "model"


# FastAPI 进程内共享的模型服务实例。
model_service = ModelService()
