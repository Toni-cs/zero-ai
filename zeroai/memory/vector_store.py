"""轻量级向量存储 - 基于 numpy + sqlite

特性：
1. 零外部依赖：numpy + sqlite3（Python 内置），不强制 faiss/chromadb
2. Embedding 后端可选：
   - 优先用 OpenAI text-embedding-3-small（如果配置了 API Key）
   - 回退到 BGE-small-zh（如果安装了 sentence-transformers）
   - 最终回退到 TF-IDF 关键词向量（零依赖，纯 numpy 实现）
3. 持久化：sqlite 存原文 + 元数据，numpy 存向量到 .npy
4. 增量更新：支持 add/upsert/delete
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import sqlite3
import hashlib
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ============================================================================
# TF-IDF 向量化器（零依赖回退方案）
# ============================================================================

class TfidfVectorizer:
    """简易 TF-IDF 向量化器（纯 numpy 实现）

    用于没有 embedding API 时的回退方案。
    维度固定为 vocab_size，支持中英文混合。
    """

    def __init__(self, max_features: int = 2000, dim: int = 256):
        """初始化

        Args:
            max_features: 最大词汇表大小
            dim: 输出向量维度（通过随机投影降维）
        """
        self.max_features = max_features
        self.dim = dim
        self.vocab: Dict[str, int] = {}
        self.idf: Optional[np.ndarray] = None
        # 随机投影矩阵（固定种子保证可复现）
        rng = np.random.RandomState(42)
        self._projection: Optional[np.ndarray] = None
        self._fitted = False

    def _tokenize(self, text: str) -> List[str]:
        """简易分词：中文按字，英文按词（下划线标识符拆分为子词）

        例如 calculate_sum → ["calculate", "sum", "calculate_sum"]
        这样查询 "calculate sum" 也能匹配 calculate_sum。
        """
        import re
        tokens = []
        # 英文单词（含下划线标识符）
        for word in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text.lower()):
            tokens.append(word)
            # 拆分下划线标识符为子词
            if "_" in word and len(word) > 3:
                parts = word.split("_")
                for part in parts:
                    if part and len(part) > 1:
                        tokens.append(part)
        # 驼峰拆分
        for word in list(tokens):
            camel_parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)", word)
            if len(camel_parts) > 1:
                tokens.extend(p.lower() for p in camel_parts if len(p) > 1)
        # 中文单字
        for ch in text:
            if "\u4e00" <= ch <= "\u9fff":
                tokens.append(ch)
        return tokens

    def fit(self, texts: List[str]) -> "TfidfVectorizer":
        """拟合词汇表和 IDF"""
        from collections import Counter

        # 统计词频
        doc_freq: Counter = Counter()
        total_docs = len(texts)
        for text in texts:
            tokens = set(self._tokenize(text))
            for t in tokens:
                doc_freq[t] += 1

        # 取 top max_features
        top = doc_freq.most_common(self.max_features)
        self.vocab = {word: idx for idx, (word, _) in enumerate(top)}

        # 计算 IDF
        idf = np.zeros(len(self.vocab), dtype=np.float32)
        for word, idx in self.vocab.items():
            df = doc_freq[word]
            idf[idx] = np.log((total_docs + 1) / (df + 1)) + 1.0
        self.idf = idf

        # 初始化随机投影矩阵（vocab_size -> dim）
        vocab_size = len(self.vocab)
        if vocab_size > 0:
            rng = np.random.RandomState(42)
            self._projection = rng.randn(vocab_size, self.dim).astype(np.float32) / np.sqrt(self.dim)
        self._fitted = True
        return self

    def transform(self, text: str) -> np.ndarray:
        """将文本转为向量"""
        if not self._fitted or self._projection is None:
            return np.zeros(self.dim, dtype=np.float32)

        tokens = self._tokenize(text)
        tf = np.zeros(len(self.vocab), dtype=np.float32)
        for token in tokens:
            idx = self.vocab.get(token)
            if idx is not None:
                tf[idx] += 1.0

        # TF-IDF
        tfidf = tf * self.idf
        # 随机投影降维
        vec = tfidf @ self._projection
        # L2 归一化
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.astype(np.float32)

    def fit_transform(self, texts: List[str]) -> np.ndarray:
        """拟合并转换"""
        self.fit(texts)
        return np.stack([self.transform(t) for t in texts]) if texts else np.zeros((0, self.dim), dtype=np.float32)


# ============================================================================
# Embedding 后端选择
# ============================================================================

class EmbeddingBackend:
    """Embedding 后端：优先用 API，回退到 TF-IDF

    阶段 M.2 升级：
    - 支持注入外部 embed_func（来自 LLMClient.embed）
    - 支持自定义维度
    - 保留 TF-IDF 作为离线兜底
    """

    def __init__(self, model_key: str = "glm", api_key: str = "", base_url: str = "",
                 auto_config: bool = False,
                 embed_func: Optional[callable] = None,
                 embed_dim: int = 1024):
        """初始化

        Args:
            model_key: 模型标识（glm / openai）
            api_key: API Key（为空且 auto_config=True 时自动从项目配置读取）
            base_url: API 地址
            auto_config: 是否自动从 zeroai.core.constants.MODEL_CONFIGS 读取配置
                         True 时若 api_key 为空，会尝试从项目配置加载 GLM/OpenRouter Key
                         并遵循代理配置（PROXY_CONFIG）
            embed_func: 外部嵌入函数（阶段 M.2）
                        签名: async def func(texts: List[str]) -> List[List[float]]
                        优先级高于 api_key，注入后直接使用
            embed_dim: 嵌入向量维度（仅 embed_func 模式生效）
        """
        self.model_key = model_key
        self._tfidf: Optional[TfidfVectorizer] = None

        # 阶段 M.2：外部 embed_func 注入
        self._embed_func = embed_func
        self._embed_dim = embed_dim

        # 阶段 2.1：自动从项目配置读取 API Key 和代理
        if auto_config and not api_key and embed_func is None:
            try:
                from zeroai.core.constants import MODEL_CONFIGS
                from zeroai.core.secrets import _is_proxy_enabled, PROXY_CONFIG
                cfg = MODEL_CONFIGS.get(model_key, {})
                if _is_proxy_enabled():
                    # 代理模式：用代理 URL 和 Token
                    proxy_url = PROXY_CONFIG.get("base_url", "")
                    if proxy_url and not proxy_url.rstrip("/").endswith("/v1"):
                        proxy_url = proxy_url.rstrip("/") + "/v1"
                    base_url = proxy_url
                    api_key = PROXY_CONFIG.get("token", "")
                else:
                    # 直连模式：用 MODEL_CONFIGS 中的配置
                    base_url = cfg.get("base_url", base_url)
                    api_key = cfg.get("api_key", "")
            except Exception:
                pass  # 配置读取失败，回退到 TF-IDF

        self.api_key = api_key
        self.base_url = base_url
        self._api_available = bool(api_key) or embed_func is not None

    async def embed_texts(self, texts: List[str]) -> np.ndarray:
        """将文本列表转为向量矩阵

        Returns:
            shape=(len(texts), dim) 的 float32 矩阵
        """
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)

        # 阶段 M.2：优先使用外部 embed_func
        if self._embed_func is not None:
            try:
                vectors = await self._embed_func(texts)
                if vectors:
                    return np.array(vectors, dtype=np.float32)
            except Exception:
                self._embed_func = None  # 失败后回退
                self._api_available = False

        # 1. 尝试 API embedding
        if self._api_available:
            try:
                return await self._embed_via_api(texts)
            except Exception:
                self._api_available = False  # 失败后不再重试

        # 2. 回退到 TF-IDF
        return self._embed_via_tfidf(texts)

    @property
    def dim(self) -> int:
        """向量维度

        智谱 embedding-3 支持 256/512/1024/2048 维
        用 1024 维平衡精度和性能
        """
        if self._embed_func is not None:
            return self._embed_dim
        if self._api_available:
            return 1024  # 智谱 embedding-3，1024 维
        return 256  # TF-IDF 降维维度

    async def _embed_via_api(self, texts: List[str]) -> np.ndarray:
        """通过 OpenAI/GLM API 生成 embedding

        使用智谱 embedding-3 模型，1024 维

        客户端经 core.secrets 的统一工厂构造（代理启用时走代理），避免在这里
        再开一条绕过代理的旁路。若本实例显式配置了 base_url/api_key，则优先
        使用实例配置（用于自定义 embedding 端点）。
        """
        from openai import AsyncOpenAI

        if self.base_url or self.api_key:
            client = AsyncOpenAI(
                base_url=self.base_url or "https://open.bigmodel.cn/api/paas/v4/",
                api_key=self.api_key,
            )
        else:
            from ..core.secrets import _make_openai_client
            client = _make_openai_client("glm")
        # GLM embedding 模型：embedding-3，支持指定 dimensions
        model = "embedding-3"
        try:
            resp = await client.embeddings.create(
                model=model, input=texts, dimensions=1024
            )
        except TypeError:
            # 老版本 API 不支持 dimensions 参数，回退
            resp = await client.embeddings.create(model=model, input=texts)
        vectors = [item.embedding for item in resp.data]
        return np.array(vectors, dtype=np.float32)

    def _embed_via_tfidf(self, texts: List[str]) -> np.ndarray:
        """通过 TF-IDF 生成向量"""
        if self._tfidf is None:
            self._tfidf = TfidfVectorizer(dim=256)
            self._tfidf.fit(texts)
        return np.stack([self._tfidf.transform(t) for t in texts]) if texts else np.zeros((0, 256), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        """同步嵌入查询文本（用于检索时）

        阶段 M.2：当注入 embed_func 时，用同步 embed_sync 生成查询向量
        """
        # 阶段 M.2：如果有外部 embed_func，尝试同步调用
        if self._embed_func is not None:
            # embed_func 是异步的，同步场景下尝试事件循环
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # 已在事件循环中，无法同步调用，回退到零向量
                    # 这种情况下应该用 search_async
                    pass
                else:
                    vectors = loop.run_until_complete(self._embed_func([text]))
                    if vectors and len(vectors) > 0:
                        return np.array(vectors[0], dtype=np.float32)
            except RuntimeError:
                # 没有事件循环，尝试新建
                try:
                    import asyncio
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    vectors = loop.run_until_complete(self._embed_func([text]))
                    if vectors and len(vectors) > 0:
                        return np.array(vectors[0], dtype=np.float32)
                except Exception:
                    pass

        if self._tfidf is None:
            # 未拟合，用空向量
            return np.zeros(self.dim, dtype=np.float32)
        vec = self._tfidf.transform(text)
        # 如果查询词全不在 vocab 中，返回零向量（search 会过滤零分结果）
        return vec


# ============================================================================
# 向量存储
# ============================================================================

class VectorStore:
    """向量存储：sqlite 存原文 + numpy 存向量

    数据布局：
    - sqlite 表 chunks(id, doc_id, source, content, hash, created_at)
    - numpy .npy 存向量矩阵（按 chunk id 顺序）

    阶段 M.2 升级：
    - 可选 FAISS 索引加速（自动检测，有则用，无则回退 numpy）
    - 支持外部 embedding 函数注入
    """

    def __init__(self, db_path: str, embedding: Optional[EmbeddingBackend] = None):
        """初始化

        Args:
            db_path: sqlite 数据库路径
            embedding: Embedding 后端，为 None 时用默认 GLM
        """
        self.db_path = db_path
        self.embedding = embedding or EmbeddingBackend()
        self._lock = threading.Lock()
        self._init_db()
        # 阶段 M.2：可选 FAISS 索引
        self._faiss_index = None
        self._faiss_available = False
        try:
            import faiss  # type: ignore
            self._faiss_available = True
        except ImportError:
            pass  # FAISS 未安装，回退 numpy
        # 向量内存缓存（避免每次查询从磁盘加载）
        self._vectors_cache: Optional[np.ndarray] = None
        self._vectors_cache_mtime: float = 0.0
        # 向量内存缓存（避免每次查询从磁盘加载）
        self._vectors_cache: Optional[np.ndarray] = None
        self._vectors_cache_mtime: float = 0.0

    def _init_db(self) -> None:
        """初始化 sqlite 表结构"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(doc_id, source)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            # 阶段 2.4：记忆衰减所需字段（向后兼容，表已存在时 ALTER 添加）
            for col, typ, default in [
                ("access_count", "INTEGER", "0"),
                ("last_accessed", "REAL", "0"),
                ("importance", "REAL", "1.0"),
            ]:
                try:
                    conn.execute(
                        f"ALTER TABLE chunks ADD COLUMN {col} {typ} DEFAULT {default}"
                    )
                except sqlite3.OperationalError:
                    pass  # 字段已存在
            conn.commit()
            conn.close()

    def _vector_path(self) -> str:
        """向量文件路径"""
        return self.db_path + ".npy"

    def _load_vectors(self) -> np.ndarray:
        """加载向量矩阵（带内存缓存，避免每次查询从磁盘读取）"""
        vec_path = self._vector_path()
        if not os.path.exists(vec_path):
            return np.zeros((0, self.embedding.dim), dtype=np.float32)

        # 检查文件修改时间，只在文件变化时重新加载
        try:
            current_mtime = os.path.getmtime(vec_path)
            if self._vectors_cache is not None and current_mtime == self._vectors_cache_mtime:
                return self._vectors_cache
        except OSError:
            pass

        try:
            vectors = np.load(vec_path)
            self._vectors_cache = vectors
            self._vectors_cache_mtime = os.path.getmtime(vec_path)
            return vectors
        except Exception:
            pass
        return np.zeros((0, self.embedding.dim), dtype=np.float32)

    def _save_vectors(self, vectors: np.ndarray) -> None:
        """保存向量矩阵"""
        np.save(self._vector_path(), vectors)
        # 更新内存缓存
        self._vectors_cache = vectors
        try:
            self._vectors_cache_mtime = os.path.getmtime(self._vector_path())
        except OSError:
            pass
        # 更新内存缓存
        self._vectors_cache = vectors
        try:
            self._vectors_cache_mtime = os.path.getmtime(self._vector_path())
        except OSError:
            pass

    async def add(
        self,
        doc_id: str,
        source: str,
        content: str,
    ) -> bool:
        """添加或更新一个文档块

        Returns:
            True 表示新增/更新成功，False 表示内容未变化（hash 相同）
        """
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            # 检查是否已存在且内容相同
            cur = conn.execute(
                "SELECT id, content_hash FROM chunks WHERE doc_id=? AND source=?",
                (doc_id, source),
            )
            row = cur.fetchone()
            if row and row[1] == content_hash:
                conn.close()
                return False  # 内容未变化

            import time
            now = time.time()
            if row:
                # 更新
                conn.execute(
                    "UPDATE chunks SET content=?, content_hash=?, created_at=? WHERE id=?",
                    (content, content_hash, now, row[0]),
                )
                chunk_id = row[0]
            else:
                # 新增
                cur = conn.execute(
                    "INSERT INTO chunks (doc_id, source, content, content_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                    (doc_id, source, content, content_hash, now),
                )
                chunk_id = cur.lastrowid
            conn.commit()
            conn.close()

        # 生成向量并更新向量矩阵
        vec = await self.embedding.embed_texts([content])
        if vec.shape[0] == 0:
            return False

        vectors = self._load_vectors()
        # 确保向量维度一致
        if vectors.shape[1] != vec.shape[1]:
            # 维度变了（embedding 后端切换），清空重建
            vectors = np.zeros((0, vec.shape[1]), dtype=np.float32)

        # 扩展或替换
        while vectors.shape[0] < chunk_id:
            # 填充零向量对齐 id
            vectors = np.vstack([vectors, np.zeros((1, vectors.shape[1]), dtype=np.float32)])

        if vectors.shape[0] == chunk_id:
            vectors = np.vstack([vectors, vec])
        else:
            vectors[chunk_id] = vec[0]

        self._save_vectors(vectors)
        return True

    async def add_batch(
        self,
        items: List[Tuple[str, str, str]],  # (doc_id, source, content)
    ) -> int:
        """批量添加文档块

        Returns:
            新增/更新的数量
        """
        if not items:
            return 0
        count = 0
        # 批量生成向量
        contents = [item[2] for item in items]
        vectors = await self.embedding.embed_texts(contents)

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            import time
            now = time.time()
            for (doc_id, source, content), vec in zip(items, vectors):
                content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
                cur = conn.execute(
                    "SELECT id, content_hash FROM chunks WHERE doc_id=? AND source=?",
                    (doc_id, source),
                )
                row = cur.fetchone()
                if row and row[1] == content_hash:
                    continue  # 跳过未变化的

                if row:
                    conn.execute(
                        "UPDATE chunks SET content=?, content_hash=?, created_at=? WHERE id=?",
                        (content, content_hash, now, row[0]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO chunks (doc_id, source, content, content_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                        (doc_id, source, content, content_hash, now),
                    )
                count += 1
            conn.commit()
            conn.close()

        # 重建向量矩阵（简单实现，后续可优化为增量更新）
        if count > 0:
            await self._rebuild_vectors()
        return count

    async def _rebuild_vectors(self) -> None:
        """从 sqlite 重建向量矩阵

        重要：重建时重新 fit TF-IDF，确保 vocab 覆盖所有文档。
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute("SELECT id, content FROM chunks ORDER BY id")
            rows = cur.fetchall()
            conn.close()

        if not rows:
            self._save_vectors(np.zeros((0, self.embedding.dim), dtype=np.float32))
            return

        contents = [r[1] for r in rows]

        # 重新 fit TF-IDF（如果有新文档加入，vocab 需要更新）
        if hasattr(self.embedding, '_tfidf') and self.embedding._tfidf is not None:
            self.embedding._tfidf.fit(contents)

        vectors = await self.embedding.embed_texts(contents)

        # 确保向量矩阵与 chunk id 对齐
        max_id = max(r[0] for r in rows)
        aligned = np.zeros((max_id + 1, vectors.shape[1] if vectors.size else self.embedding.dim), dtype=np.float32)
        for (chunk_id, _), vec in zip(rows, vectors):
            aligned[chunk_id] = vec
        self._save_vectors(aligned)

    def search(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """检索最相关的文档块

        Args:
            query: 查询文本
            top_k: 返回前 k 个结果

        Returns:
            [{"doc_id": ..., "source": ..., "content": ..., "score": ...}, ...]
        """
        # 生成查询向量
        query_vec = self.embedding.embed_query(query)
        if query_vec is None or query_vec.size == 0:
            return []

        vectors = self._load_vectors()
        if vectors.shape[0] == 0:
            return []

        # 余弦相似度（向量已 L2 归一化）
        query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-8)
        vec_norms = np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-8
        normalized = vectors / vec_norms
        scores = normalized @ query_norm

        # 取 top_k
        top_indices = np.argsort(scores)[::-1][:top_k]
        results = []

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            now = time.time()
            for idx in top_indices:
                if scores[idx] <= 0:
                    continue
                cur = conn.execute(
                    "SELECT doc_id, source, content FROM chunks WHERE id=?",
                    (int(idx),),
                )
                row = cur.fetchone()
                if row:
                    results.append({
                        "doc_id": row[0],
                        "source": row[1],
                        "content": row[2],
                        "score": float(scores[idx]),
                    })
                    # 阶段 2.4：更新访问计数（记忆衰减用）
                    conn.execute(
                        "UPDATE chunks SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
                        (now, int(idx)),
                    )
            conn.commit()
            conn.close()

        return results

    def get_stats(self) -> Dict[str, Any]:
        """获取存储统计信息"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute("SELECT COUNT(*), COUNT(DISTINCT source) FROM chunks")
            total, sources = cur.fetchone()
            cur = conn.execute("SELECT source, COUNT(*) FROM chunks GROUP BY source")
            by_source = {row[0]: row[1] for row in cur.fetchall()}
            conn.close()

        vectors = self._load_vectors()
        return {
            "total_chunks": total,
            "total_sources": sources,
            "by_source": by_source,
            "vector_dim": vectors.shape[1] if vectors.size else 0,
            "vector_count": vectors.shape[0],
        }

    def clear(self) -> None:
        """清空所有数据"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("DELETE FROM chunks")
            conn.commit()
            conn.close()
        if os.path.exists(self._vector_path()):
            os.remove(self._vector_path())

    # ========================================================================
    # 阶段 2.3：BM25 关键词检索 + 混合检索（向量 + BM25 融合）
    # ========================================================================

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """通用分词（用于 BM25 检索）

        复用 TfidfVectorizer 的分词逻辑：英文按词+下划线拆分，中文按字
        """
        tokens = []
        for word in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text.lower()):
            tokens.append(word)
            if "_" in word and len(word) > 3:
                for part in word.split("_"):
                    if part and len(part) > 1:
                        tokens.append(part)
        for ch in text:
            if "\u4e00" <= ch <= "\u9fff":
                tokens.append(ch)
        return tokens

    def _bm25_search(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """BM25 关键词检索（零依赖实现）

        BM25 算法：
            score = Σ IDF(term) * (tf*(k1+1)) / (tf + k1*(1-b+b*|D|/avgdl))

        适用于精确关键词匹配，与向量语义检索互补。
        常见场景：函数名、类名、变量名等标识符精确匹配。
        """
        import math

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute("SELECT id, source, content FROM chunks ORDER BY id")
            rows = cur.fetchall()
            conn.close()

        if not rows:
            return []

        query_terms = self._tokenize(query)
        if not query_terms:
            return []

        docs = []
        for row in rows:
            doc_id, source, content = row
            terms = self._tokenize(content)
            docs.append({
                "id": doc_id, "source": source, "content": content,
                "terms": terms, "len": len(terms),
            })

        avgdl = sum(d["len"] for d in docs) / len(docs) if docs else 1
        k1, b = 1.5, 0.75
        N = len(docs)

        # IDF
        df: Dict[str, int] = {}
        for term in set(query_terms):
            df[term] = sum(1 for d in docs if term in d["terms"])
        idf = {
            term: math.log((N - df_val + 0.5) / (df_val + 0.5) + 1)
            for term, df_val in df.items()
        }

        scored = []
        for d in docs:
            score = 0.0
            tf: Dict[str, int] = {}
            for term in d["terms"]:
                tf[term] = tf.get(term, 0) + 1
            for term in query_terms:
                if term in tf and term in idf:
                    score += idf[term] * (tf[term] * (k1 + 1)) / (
                        tf[term] + k1 * (1 - b + b * d["len"] / max(avgdl, 1))
                    )
            if score > 0:
                scored.append({**d, "score": score})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return [
            {"doc_id": s["id"], "source": s["source"],
             "content": s["content"], "score": float(s["score"])}
            for s in scored[:top_k]
        ]

    @staticmethod
    def _adaptive_weights(query: str) -> Tuple[float, float]:
        """自适应权重：精确匹配偏 BM25，语义查询偏向量

        判断依据：
        - 包含代码标识符（import, def, class, 函数名等）→ 偏 BM25
        - 包引号包裹的精确字符串 → 偏 BM25
        - 自然语言长句 → 偏向量
        """
        query_lower = query.lower()
        # 精确匹配信号
        exact_signals = 0
        if re.search(r'\b(import|from|def|class|return|raise|assert)\b', query_lower):
            exact_signals += 1
        if re.search(r'`[^`]+`', query):  # 反引号包裹的代码
            exact_signals += 1
        if re.search(r'["\'][^"\']+["\']', query):  # 引号包裹的精确字符串
            exact_signals += 1
        if re.search(r'[a-zA-Z_][a-zA-Z0-9_]*\(\)', query):  # 函数调用
            exact_signals += 1

        # 语义查询信号
        semantic_signals = 0
        if len(query) > 30:  # 长句更可能是语义查询
            semantic_signals += 1
        if re.search(r'[怎么|如何|为什么|什么|哪些|是否|能否]', query):
            semantic_signals += 1

        if exact_signals > semantic_signals:
            return 0.3, 0.7  # 偏 BM25
        elif semantic_signals > exact_signals:
            return 0.8, 0.2  # 偏向量
        return 0.6, 0.4  # 默认

    def _rewrite_query(self, query: str) -> List[str]:
        """查询改写：生成多个改写 query 提高召回率

        策略：
        1. 原始 query
        2. 去除停用词的精简版
        3. 同义词替换（简易版）
        """
        queries = [query]

        # 精简版：去除常见停用词
        stopwords = {'的', '了', '是', '在', '我', '有', '和', '就', '不', '人', '都', '一', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没', '看', '好', '自己', '这', '那', 'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'can', 'need'}
        words = re.findall(r'[a-zA-Z]+|[\u4e00-\u9fff]+', query)
        kept = [w for w in words if w.lower() not in stopwords]
        if kept and len(kept) < len(words):
            queries.append(' '.join(kept))

        # 同义词替换（简易映射）
        synonym_map = {
            '快': '性能 速度', '慢': '性能 速度', '优化': '改进 性能',
            'bug': '错误 缺陷 问题', '问题': 'bug 错误 缺陷',
            '部署': '发布 上线', '测试': '验证 检查',
        }
        rewritten = query
        for orig, repl in synonym_map.items():
            if orig in query:
                rewritten = rewritten.replace(orig, repl)
        if rewritten != query:
            queries.append(rewritten)

        return queries[:3]  # 最多 3 个改写

    def hybrid_search(
        self,
        query: str,
        top_k: int = 5,
        vector_weight: float = 0.7,
        bm25_weight: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """混合检索：向量语义检索 + BM25 关键词检索 融合

        增强策略：
        1. 查询改写：生成多个改写 query，分别检索后合并
        2. 自适应权重：根据 query 类型自动调整 vector/bm25 权重
        3. Reciprocal Rank Fusion (RRF) 替代简单加权

        Args:
            query: 查询文本
            top_k: 返回数量
            vector_weight: 向量检索权重（0-1），0 表示自适应
            bm25_weight: BM25 权重（0-1），0 表示自适应

        Returns:
            [{"doc_id":..., "source":..., "content":..., "score":...,
              "vec_score":..., "bm25_score":...}, ...]
        """
        # 自适应权重（当调用方未指定时）
        if vector_weight == 0.7 and bm25_weight == 0.3:
            vector_weight, bm25_weight = self._adaptive_weights(query)

        # 查询改写
        rewritten_queries = self._rewrite_query(query)

        # 对每个改写 query 分别检索，用 RRF 融合
        rrf_k = 60  # RRF 常数
        rrf_scores: Dict[int, float] = {}
        content_map: Dict[int, Dict[str, Any]] = {}

        for rq in rewritten_queries:
            candidate_k = max(top_k * 2, 10)
            vec_results = self.search(rq, top_k=candidate_k)
            bm25_results = self._bm25_search(rq, top_k=candidate_k)

            # RRF 融合：score = sum(1 / (k + rank))
            for rank, r in enumerate(vec_results):
                doc_id = r["doc_id"]
                rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + vector_weight / (rrf_k + rank + 1)
                if doc_id not in content_map:
                    content_map[doc_id] = {"doc_id": r["doc_id"], "source": r["source"], "content": r["content"]}
            for rank, r in enumerate(bm25_results):
                doc_id = r["doc_id"]
                rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + bm25_weight / (rrf_k + rank + 1)
                if doc_id not in content_map:
                    content_map[doc_id] = {"doc_id": r["doc_id"], "source": r["source"], "content": r["content"]}

        # 按 RRF 分数排序
        sorted_ids = sorted(rrf_scores.items(), key=lambda x: -x[1])[:top_k]

        results = []
        for doc_id, score in sorted_ids:
            if doc_id in content_map:
                entry = dict(content_map[doc_id])
                entry["score"] = float(score)
                results.append(entry)

        return results

    async def search_async(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """异步检索：用 API embedding 生成查询向量（如果可用）

        与同步 search() 的区别：
        - search()：用 TF-IDF 生成查询向量（同步，零依赖）
        - search_async()：用 API embedding 生成查询向量（异步，精度更高）

        如果 API 不可用，回退到同步 search()
        """
        if not self.embedding._api_available:
            return self.search(query, top_k)

        # 用 API 生成查询向量
        try:
            query_vecs = await self.embedding.embed_texts([query])
            if query_vecs.shape[0] == 0:
                return self.search(query, top_k)
            query_vec = query_vecs[0]
        except Exception:
            return self.search(query, top_k)

        vectors = self._load_vectors()
        if vectors.shape[0] == 0 or vectors.shape[1] != query_vec.shape[0]:
            # 维度不匹配（可能 embedding 后端切换），回退
            return self.search(query, top_k)

        # 余弦相似度
        query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-8)
        vec_norms = np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-8
        normalized = vectors / vec_norms
        scores = normalized @ query_norm

        top_indices = np.argsort(scores)[::-1][:top_k]
        results = []

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            now = time.time()
            for idx in top_indices:
                if scores[idx] <= 0:
                    continue
                cur = conn.execute(
                    "SELECT doc_id, source, content FROM chunks WHERE id=?",
                    (int(idx),),
                )
                row = cur.fetchone()
                if row:
                    results.append({
                        "doc_id": row[0],
                        "source": row[1],
                        "content": row[2],
                        "score": float(scores[idx]),
                    })
                    conn.execute(
                        "UPDATE chunks SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
                        (now, int(idx)),
                    )
            conn.commit()
            conn.close()

        return results

    # ========================================================================
    # 阶段 2.4：记忆衰减策略（时间 + 访问频率衰减）
    # ========================================================================

    def apply_decay(
        self,
        time_decay_days: float = 30.0,
        frequency_boost: float = 0.1,
        min_importance: float = 0.1,
    ) -> int:
        """应用记忆衰减：降低旧且少用的记忆重要性

        衰减公式：
            importance = max(min_importance,
                             1.0 - days_since_access / time_decay_days
                                   + access_count * frequency_boost)

        重要性低于阈值的记忆会被标记（但不删除，可手动清理）

        Args:
            time_decay_days: 时间衰减周期（天），超过此周期未访问的记忆重要性降到最低
            frequency_boost: 每次访问的重要性加成
            min_importance: 最低重要性阈值

        Returns:
            被衰减的记忆数量（importance 下降的记忆数）
        """
        import math as _math

        now = time.time()
        decay_seconds = time_decay_days * 86400
        decayed_count = 0

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute(
                "SELECT id, access_count, last_accessed, importance FROM chunks"
            )
            rows = cur.fetchall()

            for row in rows:
                chunk_id, access_count, last_accessed, old_imp = row
                # 时间衰减：距上次访问的时间
                if last_accessed > 0:
                    days_since = (now - last_accessed) / 86400
                    time_factor = max(0.0, 1.0 - days_since / time_decay_days)
                else:
                    # 从未访问过的，按创建时间衰减（用 chunk_id 近似）
                    time_factor = 0.5

                # 频率加成
                freq_factor = min(access_count * frequency_boost, 0.5)

                new_imp = max(min_importance, time_factor + freq_factor)

                if new_imp < (old_imp if old_imp else 1.0):
                    conn.execute(
                        "UPDATE chunks SET importance = ? WHERE id = ?",
                        (new_imp, chunk_id),
                    )
                    decayed_count += 1

            conn.commit()
            conn.close()

        return decayed_count

    def prune_low_importance(
        self,
        threshold: float = 0.15,
        max_to_prune: int = 100,
    ) -> int:
        """清理重要性低于阈值的记忆（慎用，会删除数据）

        Args:
            threshold: 重要性阈值，低于此值的记忆将被删除
            max_to_prune: 最多清理数量（避免一次清理太多）

        Returns:
            实际清理的数量
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute(
                "SELECT id FROM chunks WHERE importance < ? ORDER BY importance ASC LIMIT ?",
                (threshold, max_to_prune),
            )
            ids_to_delete = [row[0] for row in cur.fetchall()]
            if ids_to_delete:
                placeholders = ",".join("?" * len(ids_to_delete))
                conn.execute(
                    f"DELETE FROM chunks WHERE id IN ({placeholders})",
                    tuple(ids_to_delete),
                )
                conn.commit()
            conn.close()

        if ids_to_delete:
            # 重建向量矩阵
            try:
                asyncio.get_event_loop().create_task(self._rebuild_vectors())
            except RuntimeError:
                # 没有事件循环，同步重建
                pass

        return len(ids_to_delete)

    def get_stats_enhanced(self) -> Dict[str, Any]:
        """获取增强统计信息（含访问统计和重要性分布）"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute("SELECT COUNT(*), COUNT(DISTINCT source) FROM chunks")
            total, sources = cur.fetchone()
            cur = conn.execute("SELECT source, COUNT(*) FROM chunks GROUP BY source")
            by_source = {row[0]: row[1] for row in cur.fetchall()}

            # 访问统计
            cur = conn.execute(
                "SELECT COUNT(*), AVG(access_count), MAX(access_count), AVG(importance) FROM chunks"
            )
            stat_row = cur.fetchone()
            access_stats = {
                "avg_access_count": float(stat_row[1] or 0),
                "max_access_count": int(stat_row[2] or 0),
                "avg_importance": float(stat_row[3] or 1.0),
            }

            # 重要性分布
            cur = conn.execute(
                "SELECT "
                "SUM(CASE WHEN importance >= 0.7 THEN 1 ELSE 0 END) as high, "
                "SUM(CASE WHEN importance >= 0.3 AND importance < 0.7 THEN 1 ELSE 0 END) as mid, "
                "SUM(CASE WHEN importance < 0.3 THEN 1 ELSE 0 END) as low "
                "FROM chunks"
            )
            dist_row = cur.fetchone()
            importance_dist = {
                "high (>=0.7)": int(dist_row[0] or 0),
                "mid (0.3-0.7)": int(dist_row[1] or 0),
                "low (<0.3)": int(dist_row[2] or 0),
            }
            conn.close()

        vectors = self._load_vectors()
        return {
            "total_chunks": total,
            "total_sources": sources,
            "by_source": by_source,
            "vector_dim": vectors.shape[1] if vectors.size else 0,
            "vector_count": vectors.shape[0],
            "access_stats": access_stats,
            "importance_dist": importance_dist,
        }


# ============================================================================
# 单例管理
# ============================================================================

_vector_store_instance: Optional[VectorStore] = None


def get_vector_store(db_path: Optional[str] = None) -> VectorStore:
    """获取 VectorStore 单例

    Args:
        db_path: 数据库路径，为 None 时用默认路径 ~/.zeroai/memory.db
    """
    global _vector_store_instance
    if _vector_store_instance is None or db_path is not None:
        if db_path is None:
            home = os.path.expanduser("~")
            zeroai_dir = os.path.join(home, ".zeroai")
            os.makedirs(zeroai_dir, exist_ok=True)
            db_path = os.path.join(zeroai_dir, "memory.db")
        _vector_store_instance = VectorStore(db_path)
    return _vector_store_instance


def reset_vector_store() -> None:
    """重置单例"""
    global _vector_store_instance
    _vector_store_instance = None
