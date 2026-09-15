"""扫描本地 BIM 文档，建立轻量级字符级 TF-IDF 检索索引。"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .config import settings


@dataclass
class KnowledgeChunk:
    """可被问答上下文引用的一段文档文本。"""

    source: str
    location: str
    text: str


class KnowledgeIndex:
    """维护文档切片、向量化器和检索矩阵，并用锁保护并发访问。"""

    def __init__(self) -> None:
        self._chunks: list[KnowledgeChunk] = []
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix: Any = None
        self._documents = 0
        self._state = "idle"
        self._error: str | None = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        """索引中至少存在一个文本片段时才可用于检索。"""
        return self._matrix is not None and bool(self._chunks)

    def status(self) -> dict[str, Any]:
        """返回前端展示和健康检查所需的知识库状态。"""
        return {
            "state": self._state,
            "ready": self.ready,
            "documents": self._documents,
            "chunks": len(self._chunks),
            "error": self._error,
            "directories": [
                str(settings.knowledge_dir),
                str(settings.source_dir),
            ],
        }

    def refresh(self) -> None:
        """全量重建索引；索引失败不会导致整个 API 进程退出。"""
        with self._lock:
            self._state = "indexing"
            self._error = None
            try:
                chunks = self._collect_chunks()
                self._chunks = chunks
                if chunks:
                    # 中文字符 n-gram 不依赖分词词典，适合混合中英文 BIM 文档。
                    self._vectorizer = TfidfVectorizer(
                        analyzer="char",
                        ngram_range=(2, 4),
                        min_df=1,
                        max_features=100_000,
                        sublinear_tf=True,
                    )
                    self._matrix = self._vectorizer.fit_transform(
                        chunk.text for chunk in chunks
                    )
                else:
                    self._vectorizer = None
                    self._matrix = None
                self._state = "ready"
            except Exception as exc:  # Indexing should not take down the API.
                self._state = "error"
                self._error = str(exc)
                self._chunks = []
                self._vectorizer = None
                self._matrix = None

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """按余弦相似度返回最相关的文本片段。"""
        if not self.ready or not query.strip():
            return []
        with self._lock:
            if self._vectorizer is None or self._matrix is None:
                return []
            query_vector = self._vectorizer.transform([query])
            scores = cosine_similarity(query_vector, self._matrix).ravel()
            ranked = scores.argsort()[::-1][:limit]
            return [
                {
                    **asdict(self._chunks[index]),
                    "score": round(float(scores[index]), 4),
                }
                for index in ranked
                # 过低分结果通常与问题无关，过滤后减少上下文噪声。
                if scores[index] > 0.015
            ]

    def _collect_chunks(self) -> list[KnowledgeChunk]:
        """遍历所有支持的文档并转换为统一的知识片段。"""
        files = self._discover_files()
        self._documents = len(files)
        chunks: list[KnowledgeChunk] = []
        for path in files:
            if path.suffix.lower() == ".pdf":
                chunks.extend(self._read_pdf(path))
            else:
                text = path.read_text(encoding="utf-8", errors="replace")
                chunks.extend(self._chunk_text(text, path.name, "文本"))
        return chunks

    def _discover_files(self) -> list[Path]:
        """发现 knowledge/source 两个目录中的 PDF、TXT 和 Markdown 文件。"""
        files: list[Path] = []
        seen: set[Path] = set()
        for directory in (settings.knowledge_dir, settings.source_dir):
            if not directory.exists():
                continue
            for path in sorted(directory.rglob("*")):
                if (
                    path.is_file()
                    and path.suffix.lower() in {".pdf", ".txt", ".md"}
                    and path.resolve() not in seen
                ):
                    seen.add(path.resolve())
                    files.append(path)
        return files

    def _read_pdf(self, path: Path) -> list[KnowledgeChunk]:
        """逐页提取 PDF 文本，并保留页码作为引用位置。"""
        chunks: list[KnowledgeChunk] = []
        reader = PdfReader(str(path))
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            chunks.extend(
                self._chunk_text(text, path.name, f"第 {page_number} 页")
            )
        return chunks

    @staticmethod
    def _chunk_text(
        text: str,
        source: str,
        location: str,
        chunk_size: int = 1000,
        overlap: int = 120,
    ) -> list[KnowledgeChunk]:
        """按固定长度切分文本，并保留少量重叠以减少语义断裂。"""
        normalized = " ".join(text.split())
        if len(normalized) < 60:
            return []
        chunks: list[KnowledgeChunk] = []
        start = 0
        while start < len(normalized):
            end = min(start + chunk_size, len(normalized))
            if end < len(normalized):
                # 优先在中文句号或英文句点处结束，让上下文更完整。
                boundary = max(
                    normalized.rfind("。", start + chunk_size // 2, end),
                    normalized.rfind(".", start + chunk_size // 2, end),
                )
                if boundary > start:
                    end = boundary + 1
            chunks.append(
                KnowledgeChunk(
                    source=source,
                    location=location,
                    text=normalized[start:end],
                )
            )
            if end >= len(normalized):
                break
            start = max(end - overlap, start + 1)
        return chunks


# FastAPI 进程内共享的检索服务实例。
knowledge_index = KnowledgeIndex()
