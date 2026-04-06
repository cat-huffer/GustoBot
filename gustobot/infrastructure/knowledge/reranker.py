"""
检索结果重排（rerank）：在向量召回后，用专用模型按「与查询的相关性」重新排序。

支持多种服务商（Cohere、Jina、Voyage）及可配置的自定义 HTTP API（如 DashScope / BGE
等兼容格式）。行为由 ``settings`` 中的 ``RERANK_*`` 项控制；未启用或配置不完整时
``Reranker`` 会以 ``enabled=False`` 运行，调用方仍可安全调用 :meth:`Reranker.rerank`，
此时直接返回截断后的原始列表。
"""

import asyncio
import httpx
from typing import Any, Dict, List

from loguru import logger

from gustobot.config import settings


class Reranker:
    """查询-文档相关性重排器，供 ``KnowledgeService.search`` 在向量召回后精排使用。

    ``provider`` 取值 ``custom`` / ``cohere`` / ``jina`` / ``voyage`` 时走不同实现；
    Cohere 使用官方同步 SDK，在 :meth:`rerank` 内通过 ``asyncio.to_thread`` 调用以免阻塞
    事件循环；其余多为 ``httpx.AsyncClient`` 异步请求。
    """

    def __init__(self) -> None:
        """从全局配置读取开关、服务商、URL、模型、密钥与超时等。

        若 ``RERANK_ENABLED`` 为假，或启用但缺少 ``provider`` / ``api_key``，会将
        ``self.enabled`` 置为 ``False`` 并打日志，避免后续调用误连网。
        """
        self.enabled = settings.RERANK_ENABLED
        self.provider = settings.RERANK_PROVIDER.lower() if settings.RERANK_PROVIDER else None
        self.base_url = settings.RERANK_BASE_URL
        self.endpoint = settings.RERANK_ENDPOINT
        self.model = settings.RERANK_MODEL
        self.api_key = settings.RERANK_API_KEY
        self.top_n = settings.RERANK_TOP_N
        self.timeout = settings.RERANK_TIMEOUT

        if not self.enabled:
            logger.info("Reranker disabled via config")
            return

        if not self.provider or not self.api_key:
            logger.warning("Reranker enabled but missing provider or API key, disabling")
            self.enabled = False
            return

        logger.info(
            "Reranker initialized: provider=%s, model=%s, base_url=%s",
            self.provider,
            self.model,
            self.base_url,
        )

    # 按与 ``query`` 的相关性对 ``documents`` 重排，并截断为至多 ``top_k`` 条。
    async def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """按与 ``query`` 的相关性对 ``documents`` 重排，并截断为至多 ``top_k`` 条。

        未启用、输入为空或服务商不支持时，直接返回 ``documents[:top_k]``。请求异常时
        记录错误并同样回退到向量顺序截断，保证检索链路不中断。

        Args:
            query: 用户查询文本。
            documents: 向量检索得到的字典列表，通常含 ``content`` 或 ``document`` 正文。
            top_k: 返回条数上限。

        Returns:
            重排后的文档列表（可能带 ``rerank_score``）；失败或未启用时顺序与输入一致，
            仅截取前 ``top_k`` 条。
        """
        if not self.enabled or not documents:
            return documents[:top_k]

        try:
            if self.provider == "custom":
                ranked = await self._custom_rerank(query, documents, top_k)
            elif self.provider == "cohere":
                ranked = await asyncio.to_thread(self._cohere_rerank, query, documents, top_k)
            elif self.provider == "jina":
                ranked = await self._jina_rerank(query, documents, top_k)
            elif self.provider == "voyage":
                ranked = await self._voyage_rerank(query, documents, top_k)
            else:
                logger.warning(f"Unsupported reranker provider: {self.provider}")
                return documents[:top_k]

            if ranked:
                return ranked
        except Exception as exc:
            logger.error("Reranker failed: %s", exc, exc_info=True)

        return documents[:top_k]

    async def _custom_rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """调用 ``RERANK_BASE_URL`` + ``RERANK_ENDPOINT`` 的自定义重排接口（如 DashScope 格式）。

        请求体为 ``model`` + ``input.query`` / ``input.documents`` + ``parameters``（含
        ``return_documents``、``top_n``）。响应解析 ``output.results``，每项含 ``index`` 与
        ``relevance_score`` 或 ``score``。未出现在结果中的原文档会按原顺序追加在末尾，
        再统一截断为 ``top_k``。

        Args:
            query: 查询字符串。
            documents: 待重排文档；正文取自 ``content`` 或 ``document`` 键。
            top_k: 返回长度上限。

        Returns:
            带 ``rerank_score`` 的文档列表；无 ``base_url`` 或响应无结果时回退 ``documents[:top_k]``。
        """
        if not self.base_url:
            logger.error("Custom reranker requires RERANK_BASE_URL")
            return documents[:top_k]

        # 准备文档内容
        texts = [doc.get("content") or doc.get("document") or "" for doc in documents]

        # 构建请求
        url = f"{self.base_url.rstrip('/')}{self.endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # DashScope API 格式
        payload = {
            "model": self.model,
            "input": {
                "query": query,
                "documents": texts,
            },
            "parameters": {
                "return_documents": True,
                "top_n": min(self.top_n or top_k, len(documents)),
            }
        }

        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        # 解析响应 (DashScope 格式: output.results)
        output = data.get("output", {})
        results = output.get("results", [])

        if not results:
            logger.warning("Custom reranker returned no results")
            return documents[:top_k]

        # 重新排序文档
        reranked = []
        for item in results:
            idx = item.get("index")
            score = item.get("relevance_score") or item.get("score")

            if idx is not None and 0 <= idx < len(documents):
                doc = dict(documents[idx])
                if score is not None:
                    doc["rerank_score"] = float(score)
                reranked.append(doc)

        # 添加未被重排的文档
        seen_ids = {doc.get("chunk_id") or doc.get("id") for doc in reranked}
        for doc in documents:
            doc_id = doc.get("chunk_id") or doc.get("id")
            if doc_id not in seen_ids:
                reranked.append(doc)

        return reranked[:top_k]

    async def _jina_rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """调用 Jina AI ``/v1/rerank`` 异步重排。

        ``top_n`` 取 ``min(RERANK_TOP_N 或 top_k, len(documents))``；默认模型可为
        ``jina-reranker-v1-base-en``。响应 ``results`` 交给 :meth:`_process_rerank_results`。
        """
        texts = [doc.get("content") or doc.get("document") or "" for doc in documents]

        url = "https://api.jina.ai/v1/rerank"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model or "jina-reranker-v1-base-en",
            "query": query,
            "documents": texts,
            "top_n": min(self.top_n or top_k, len(documents)),
        }

        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        results = data.get("results", [])
        return self._process_rerank_results(results, documents, top_k)

    async def _voyage_rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """调用 Voyage AI ``/v1/rerank`` 异步重排。

        请求字段为 ``top_k``（与 Jina 的 ``top_n`` 不同）；响应 ``data`` 列表同样经
        :meth:`_process_rerank_results` 合并回原始文档并写 ``rerank_score``。
        """
        texts = [doc.get("content") or doc.get("document") or "" for doc in documents]

        url = "https://api.voyageai.com/v1/rerank"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model or "rerank-lite-1",
            "query": query,
            "documents": texts,
            "top_k": min(self.top_n or top_k, len(documents)),
        }

        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        results = data.get("data", [])
        return self._process_rerank_results(results, documents, top_k)

    def _cohere_rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """使用 Cohere 官方 SDK 同步重排（由上层 ``to_thread`` 调用）。

        依赖 ``cohere`` 包；未安装时抛出 ``RuntimeError``。将 ``response.results`` 转为
        ``index`` + ``relevance_score`` 列表后交给 :meth:`_process_rerank_results`。
        """
        try:
            from cohere import Client as CohereClient
        except ImportError as exc:
            raise RuntimeError("cohere package not installed") from exc

        client = CohereClient(self.api_key)
        texts = [doc.get("content") or doc.get("document") or "" for doc in documents]

        response = client.rerank(
            query=query,
            documents=texts,
            top_n=min(self.top_n or top_k, len(documents)),
            model=self.model or "rerank-english-v3.0",
        )

        results = [
            {"index": r.index, "relevance_score": r.relevance_score}
            for r in response.results
        ]

        return self._process_rerank_results(results, documents, top_k)

    # 把各厂商返回的 ``index`` + 分数列表合并为带 ``rerank_score`` 的文档列表。
    def _process_rerank_results(
        self,
        results: List[Dict[str, Any]],
        documents: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """把各厂商返回的 ``index`` + 分数列表合并为带 ``rerank_score`` 的文档列表。

        按 ``results`` 顺序拷贝 ``documents[index]`` 并写入分数；未出现在结果中的文档
        按 ``chunk_id`` 或 ``id`` 去重后追加到末尾，保证不丢召回条，最后再 ``[:top_k]``。
        """
        reranked = []

        for item in results:
            idx = item.get("index")
            score = item.get("relevance_score") or item.get("score")

            if idx is not None and 0 <= idx < len(documents):
                doc = dict(documents[idx])
                if score is not None:
                    doc["rerank_score"] = float(score)
                reranked.append(doc)

        # 添加未被重排的文档
        seen_ids = {doc.get("chunk_id") or doc.get("id") for doc in reranked}
        for doc in documents:
            doc_id = doc.get("chunk_id") or doc.get("id")
            if doc_id not in seen_ids:
                reranked.append(doc)

        return reranked[:top_k]
