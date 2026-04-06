"""Knowledge base service implemented with LangChain primitives."""

import asyncio
from typing import Any, Dict, List, Optional
from uuid import uuid4

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger

from gustobot.config import settings
from .embeddings import OpenAICompatibleEmbeddings
from .vector_store import VectorStore
from .reranker import Reranker


class KnowledgeService:
    """面向「菜谱 / 文档」场景的向量知识库封装类。

    **它做什么**

    把长文本切成小段，为每段生成向量（embedding），存进向量数据库（默认 Milvus）；查询时
    把问题也变成向量，在库里找最相近的段落，必要时再用重排模型（``Reranker``）把结果排
    得更准。你通常只调用本类的公开方法，不必自己拼切分器、嵌入客户端和 Milvus 细节。

    **入库时数据怎么走**

    1. 原始字符串交给 ``RecursiveCharacterTextSplitter``，按配置块大小与重叠切成多条
       ``Document``。
    2. ``OpenAICompatibleEmbeddings`` 为每条内容生成向量。
    3. ``VectorStore`` 把向量与元数据（如 ``recipe_id``、标题等）一起写入集合。

    **查询时数据怎么走**

    1. 用户问题经同一套嵌入模型变成查询向量。
    2. 在向量库里做相似度检索；可用 ``filter_expr`` 限定元数据条件，用相似度阈值过滤
       明显不相关的命中。
    3. 若配置启用了 ``Reranker``：先多召回一些候选，再用重排模型筛到最相关的若干条，
       最终返回不超过 ``top_k`` 条。

    **该用哪个方法**

    - :meth:`ingest_text`：已有「一整段文字 + 元数据」，直接入库的底层入口。
    - :meth:`add_document`：通用文档，会帮你补全 ``title`` / ``name``，以及可选的
      ``recipe_id``（与分块 id 策略相关）。
    - :meth:`add_recipe` / :meth:`add_recipes_batch`：输入结构化菜谱字典，内部格式化成
      可读文本再入库。
    - :meth:`search`：语义检索主入口。
    - :meth:`delete_recipe`、:meth:`get_stats`、:meth:`clear`、:meth:`close`：按 id 删除、
      看集合统计、清空集合、释放连接。

    **和 async 的关系**

    嵌入、切分、向量库访问等多数是同步阻塞 IO；本类用 ``asyncio.to_thread`` 把它们放到
    线程里跑，这样 ``async def`` 方法不会长时间占住事件循环。
    """

    def __init__(
        self,
        *,
        vector_store: Optional[VectorStore] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
    ) -> None:
        """创建知识库服务：装配文本切分、嵌入、向量存储与重排组件。

        Args:
            vector_store: 自定义向量存储；省略时按 ``settings`` 连接默认 Milvus 集合。
            chunk_size: 单块最大字符数；省略时用 ``settings.KB_CHUNK_SIZE``。
            chunk_overlap: 相邻块重叠字符数；省略时用 ``settings.KB_CHUNK_OVERLAP``。
        """
        self.chunk_size = chunk_size or settings.KB_CHUNK_SIZE
        self.chunk_overlap = chunk_overlap or settings.KB_CHUNK_OVERLAP

        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", " "],
            length_function=len,  # 使用字符长度而不是 tiktoken
        )

        embedding_api_key = settings.EMBEDDING_API_KEY or settings.LLM_API_KEY
        self.embedder = OpenAICompatibleEmbeddings(
            model=settings.EMBEDDING_MODEL,
            api_key=embedding_api_key,
            base_url=settings.EMBEDDING_BASE_URL,
            dimension=settings.EMBEDDING_DIMENSION,
        )

        self.vector_store = vector_store or VectorStore(
            collection_name=settings.MILVUS_COLLECTION,
            host=settings.MILVUS_HOST,
            port=settings.MILVUS_PORT,
            dimension=settings.EMBEDDING_DIMENSION,
            index_type=settings.MILVUS_INDEX_TYPE,
            metric_type=settings.MILVUS_METRIC_TYPE,
        )

        self.reranker = Reranker()

        logger.info(
            "KnowledgeService initialised (chunk_size=%s, chunk_overlap=%s)",
            self.chunk_size,
            self.chunk_overlap,
        )

    # 入库主干：切块 → 批量嵌入 → 写入向量库。
    async def ingest_text(
        self,
        text: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """入库主干：切块 → 批量嵌入 → 写入向量库。

        空字符串或仅空白时直接返回 ``{"add_count": 0, "ids": []}``。切分、嵌入与落库在
        后台线程执行（``asyncio.to_thread``），避免阻塞事件循环。

        Args:
            text: 待入库的完整正文。
            metadata: 写入每个分块的元数据；会影响 :meth:`_store_documents` 中的逻辑 id
                与 ``chunk_id`` 生成。

        Returns:
            与 :meth:`_store_documents` 一致，至少含 ``add_count``、``ids``；失败或未写入时
            ``add_count`` 为 0。
        """
        if not text or not text.strip():
            return {"add_count": 0, "ids": []}

        documents = await asyncio.to_thread(self._split_into_documents, text, metadata or {})
        if not documents:
            return {"add_count": 0, "ids": []}

        embeddings = await asyncio.to_thread(
            self.embedder.embed_documents,
            [doc.page_content for doc in documents],
        )

        result = await asyncio.to_thread(self._store_documents, documents, embeddings)
        return result

    # 将任意文本写入向量库，并补齐常用元数据。
    async def add_document(
        self,
        *,
        doc_id: Optional[str],
        title: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """将任意文本写入向量库，并补齐常用元数据。

        若传入 ``metadata`` 则先浅拷贝再合并；在未显式提供时，用 ``title`` 填充 ``title``、
        ``name``；若提供 ``doc_id`` 且元数据中尚无 ``recipe_id``，则写入 ``recipe_id``。
        存储侧分块 id 会基于 ``recipe_id`` 生成（参见 :meth:`_store_documents`），再经
        :meth:`ingest_text` 分块、嵌入并入库。

        Args:
            doc_id: 可选的逻辑文档 id；当元数据中尚无 ``recipe_id`` 时作为其值写入。
            title: 展示用标题；用于 ``title``、``name`` 元数据键。
            content: 待切分、嵌入并持久化的正文。
            metadata: 与上述字段合并的额外元数据。

        Returns:
            至少成功写入一个分块时为 ``True``；正文为空或持久化失败时为 ``False``。
        """
        meta = metadata.copy() if metadata else {}
        meta.setdefault("title", title)
        meta.setdefault("name", title)
        if doc_id:
            meta.setdefault("recipe_id", doc_id)
        result = await self.ingest_text(content, metadata=meta)
        return result.get("add_count", 0) > 0

    # 将一条结构化菜谱格式化为文本后入库。
    async def add_recipe(self, recipe_id: str, recipe_data: Dict[str, Any]) -> bool:
        """将一条结构化菜谱格式化为文本后入库。

        先用 :meth:`_format_recipe_document` 生成多行中文描述，再附带 ``recipe_id``、菜名、
        分类、难度等元数据，委托 :meth:`ingest_text` 完成切块与向量写入。

        Args:
            recipe_id: 业务侧菜谱主键，写入元数据并参与分块 id 推导。
            recipe_data: 支持 ``name``、``category``、``steps``、``ingredients`` 等常见键；
                缺失的段落不会出现在文本中。

        Returns:
            至少成功写入一个向量分块为 ``True``；正文为空或底层写入失败为 ``False``。
        """
        document = self._format_recipe_document(recipe_data)
        metadata = {
            "recipe_id": recipe_id,
            "name": recipe_data.get("name", ""),
            "category": recipe_data.get("category", ""),
            "difficulty": recipe_data.get("difficulty", ""),
        }
        result = await self.ingest_text(document, metadata=metadata)
        return result.get("add_count", 0) > 0
    
    # 逐条调用 :meth:`add_recipe`，汇总成功与失败条数。
    async def add_recipes_batch(self, recipes: List[Dict[str, Any]]) -> Dict[str, int]:
        """逐条调用 :meth:`add_recipe`，汇总成功与失败条数。

        每条记录的 id 依次尝试 ``recipe["id"]``、``recipe["recipe_id"]``；皆无时生成随机
        UUID，保证可入库。

        Args:
            recipes: 菜谱字典列表，元素含义与 :meth:`add_recipe` 的 ``recipe_data`` 相同。

        Returns:
            字典含 ``success``、``error`` 以及 ``total``（输入列表长度）。
        """
        success_count = 0
        error_count = 0
        for recipe in recipes:
            recipe_id = recipe.get("id") or recipe.get("recipe_id") or str(uuid4())
            if await self.add_recipe(recipe_id, recipe):
                success_count += 1
            else:
                error_count += 1
        return {"success": success_count, "error": error_count, "total": len(recipes)}

    async def search(
        self,
        query: str,
        *,
        top_k: Optional[int] = None,
        similarity_threshold: Optional[float] = None,
        filter_expr: Optional[str] = None,
        filter_by_similarity: bool = True,
    ) -> List[Dict[str, Any]]:
        """对查询文本做向量检索，并按配置过滤、可选重排。

        典型顺序：查询嵌入 → ``vector_store.search`` 召回；若 ``filter_by_similarity`` 为真
        且配置了阈值，先用向量相似度 ``score`` 过滤。启用 ``Reranker`` 时会把召回上限提高到
        ``settings.RERANK_MAX_CANDIDATES``，精排后再按 ``rerank_score`` 阈值过滤，最后截断为
        ``top_k``。未启用重排且 ``filter_by_similarity`` 为假时，阈值在排序后阶段再应用
        （见实现中的分支）。

        Args:
            query: 用户问题或检索语句；空或纯空白返回 ``[]``。
            top_k: 最终返回条数上限；默认 ``settings.KB_TOP_K``。
            similarity_threshold: 相似度下限；``None`` 时用 ``settings.KB_SIMILARITY_THRESHOLD``。
            filter_expr: 向量库原生过滤表达式（如 Milvus 表达式），由 ``VectorStore`` 解析。
            filter_by_similarity: 控制相似度阈值在流水线前半段是否生效；与是否启用 reranker
                组合时行为不同，阅读本方法实现可对照。

        Returns:
            命中项字典列表，长度不超过 ``top_k``；字段名含 ``score``，启用重排时另有
            ``rerank_score`` 等（以实际返回为准）。
        """
        if not query or not query.strip():
            return []

        top_k = top_k or settings.KB_TOP_K
        similarity_threshold = (
            similarity_threshold if similarity_threshold is not None else settings.KB_SIMILARITY_THRESHOLD
        )

        # 如果启用 reranker，先召回更多候选文档
        recall_k = top_k
        if self.reranker.enabled:
            recall_k = settings.RERANK_MAX_CANDIDATES  # 召回更多文档用于重排

        embedding = await asyncio.to_thread(self.embedder.embed_query, query)
        results = await asyncio.to_thread(
            self.vector_store.search,
            embedding,
            recall_k,  # 使用更大的召回数量
            filter_expr,
        )

        candidates = results
        if filter_by_similarity and similarity_threshold is not None:
            candidates = [r for r in candidates if r.get("score", 0.0) >= similarity_threshold]

        # 使用 reranker 精排
        if candidates and self.reranker.enabled:
            candidates = await self.reranker.rerank(query, candidates, top_k)

        if self.reranker.enabled:
            candidates = [
                r
                for r in candidates
                if r.get("rerank_score", 0.0) >= settings.KB_RERANK_SCORE_THRESHOLD
            ]
        elif not filter_by_similarity and similarity_threshold is not None:
            candidates = [r for r in candidates if r.get("score", 0.0) >= similarity_threshold]

        return candidates[:top_k]

    async def delete_recipe(self, recipe_id: str) -> bool:
        """按菜谱 id 删除向量库中对应文档（委托 ``vector_store.delete_documents``）。

        Args:
            recipe_id: 与入库时元数据 ``recipe_id`` 一致的业务主键。

        Returns:
            底层删除是否成功。
        """
        return await asyncio.to_thread(self.vector_store.delete_documents, [recipe_id])

    async def get_stats(self) -> Dict[str, Any]:
        """返回集合统计，并合并当前切块参数与嵌入模型名等便于排查的信息。"""

        def _stats() -> Dict[str, Any]:
            """在线程中执行的同步统计函数，供 ``asyncio.to_thread`` 调用。"""
            stats = self.vector_store.get_collection_stats()
            stats.update(
                {
                    "chunk_size": self.chunk_size,
                    "chunk_overlap": self.chunk_overlap,
                    "embedding_model": settings.EMBEDDING_MODEL,
                }
            )
            return stats

        return await asyncio.to_thread(_stats)

    async def clear(self) -> bool:
        """清空当前向量集合（删除全部已存向量与关联数据，生产环境慎用）。"""
        return await asyncio.to_thread(self.vector_store.clear_collection)

    async def close(self) -> None:
        """关闭向量库连接等资源，服务生命周期结束时调用。"""
        await asyncio.to_thread(self.vector_store.close)

    def _split_into_documents(self, text: str, metadata: Dict[str, Any]) -> List[Document]:
        """用配置好的切分器把整段文本拆成多条 ``Document``。

        若切分结果为空（边界情况），则退回只含原文的一条 ``Document``，避免后续流水线无数据。

        Args:
            text: 原始正文。
            metadata: 会复制到每个分块的元数据（LangChain 切分时会带到各 chunk）。

        Returns:
            切分后的 ``Document`` 列表。
        """
        base_document = Document(page_content=text, metadata=metadata)
        chunks = self.splitter.split_documents([base_document])
        return chunks or [base_document]

    def _store_documents(
        self,
        documents: List[Document],
        embeddings: List[List[float]],
    ) -> Dict[str, Any]:
        """为每个分块生成唯一 ``chunk_id``、补齐 ``recipe_id`` 等，并批量写入向量库。

        ``base_id`` 优先取元数据 ``recipe_id``，否则 ``id``、``source``，再否则随机 hex；
        每条向量行的主键为 ``{base_id}_{index}``。

        Args:
            documents: 已切好的文档块。
            embeddings: 与 ``documents`` 等长的向量列表。

        Returns:
            含 ``add_count``、``ids``、``stored``；失败或输入为空时 ``add_count`` 为 0。
        """
        if not documents or not embeddings:
            return {"add_count": 0, "ids": [], "stored": False}

        ids: List[str] = []
        contents: List[str] = []
        metadatas: List[Dict[str, Any]] = []

        for index, doc in enumerate(documents):
            metadata = dict(doc.metadata or {})
            base_id = metadata.get("recipe_id") or metadata.get("id") or metadata.get("source") or uuid4().hex
            chunk_id = f"{base_id}_{index}"
            metadata.setdefault("recipe_id", base_id)
            metadata.setdefault("chunk_id", chunk_id)
            metadata.setdefault("name", metadata.get("name") or metadata.get("title") or "")

            ids.append(chunk_id)
            contents.append(doc.page_content)
            metadatas.append(metadata)

        success = self.vector_store.add_documents(
            ids=ids,
            embeddings=embeddings,
            documents=contents,
            metadatas=metadatas,
        )

        return {"add_count": len(ids) if success else 0, "ids": ids, "stored": success}

    @staticmethod
    def _format_recipe_document(recipe: Dict[str, Any]) -> str:
        """把菜谱字典拼成多行中文文本，便于嵌入与检索（菜名、分类、食材、步骤等）。

        只输出字典里存在的字段；列表型食材/步骤会格式化为可读行。无内容时返回空字符串。

        Args:
            recipe: 结构化菜谱，键名兼容 ``name``、``category``、``ingredients``、
                ``ingredient_list``、``steps``、``tips``、``nutrition`` 等。

        Returns:
            用换行连接的非空段落；可能为空字符串。
        """
        parts: List[str] = []
        name = recipe.get("name")
        if name:
            parts.append(f"菜名：{name}")

        category = recipe.get("category")
        if category:
            parts.append(f"分类：{category}")

        difficulty = recipe.get("difficulty")
        if difficulty:
            parts.append(f"难度：{difficulty}")

        time_cost = recipe.get("time") or recipe.get("cook_time")
        if time_cost:
            parts.append(f"耗时：{time_cost}")

        ingredients = recipe.get("ingredients") or recipe.get("ingredient_list")
        if ingredients:
            if isinstance(ingredients, list):
                formatted = "、".join(str(item) for item in ingredients)
            else:
                formatted = str(ingredients)
            parts.append(f"食材：{formatted}")

        steps = recipe.get("steps")
        if steps:
            if isinstance(steps, list):
                step_lines = [f"步骤{idx + 1}：{step}" for idx, step in enumerate(steps)]
                parts.extend(step_lines)
            else:
                parts.append(f"步骤：{steps}")

        tips = recipe.get("tips")
        if tips:
            parts.append(f"小贴士：{tips}")

        nutrition = recipe.get("nutrition")
        if nutrition:
            if isinstance(nutrition, dict):
                nutritions = [f"{k}: {v}" for k, v in nutrition.items()]
                parts.append("营养：" + "、".join(nutritions))
            else:
                parts.append(f"营养：{nutrition}")

        return "\n".join(parts)
