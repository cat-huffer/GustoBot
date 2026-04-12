"""
知识库模块用的文本嵌入（embedding）封装。

使用官方 ``openai`` 客户端调用 OpenAI 兼容的 ``/embeddings`` 接口，可对接 OpenAI、
DashScope（通义千问）等只要参数子集兼容的服务。此处**不**依赖 LangChain 的 Embeddings
抽象，便于为只支持部分 OpenAI 参数的厂商微调请求体（例如 DashScope 对空输入较严格）。
"""

from typing import List, Optional, Sequence

from loguru import logger
from openai import OpenAI
import httpx


class OpenAICompatibleEmbeddings:
    """OpenAI 兼容嵌入 API 的薄封装，供向量入库与检索调用。

    对外暴露 :meth:`embed_documents`（批量）与 :meth:`embed_query`（单条），内部统一走
    :meth:`_embed`，按 ``max_batch_size`` 分批请求并保证返回向量与输入顺序一致。
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        dimension: Optional[int] = None,
        max_batch_size: int = 64,
        request_timeout: Optional[float] = 60.0,
    ) -> None:
        """初始化底层 ``OpenAI`` 客户端。

        若环境中 ``ALL_PROXY`` 等代理配置不被 httpx 支持（如 ``socks://``），构造客户端可能
        抛 ``ValueError``；此时会降级为 ``trust_env=False`` 的 ``httpx.Client``，避免因代理
        导致知识库完全不可用。

        Args:
            model: 嵌入模型名，随服务商而定。
            api_key: API 密钥；省略时使用 SDK 默认读取环境变量的行为。
            base_url: API 根地址；省略时一般为官方 OpenAI。
            dimension: 期望向量维度；仅用于与实际返回长度不一致时打日志告警，不截断向量。
            max_batch_size: 单次 ``embeddings.create`` 最多提交的文本条数。
            request_timeout: 单次 HTTP 请求超时（秒）；``None`` 表示使用客户端默认。
        """
        self.model = model
        self.dimension = dimension
        self.max_batch_size = max_batch_size
        self.request_timeout = request_timeout

        client_kwargs = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url

        try:
            # 底层 OpenAI 客户端。用来发 HTTP 请求、调 API 的程序对象
            self._client = OpenAI(**client_kwargs)
        except ValueError as exc:
            # 部分环境将 ALL_PROXY 设为 httpx 不支持的协议（例如 socks://...），
            # 会导致 httpx（以及基于它的 OpenAI 客户端）在构造时崩溃。
            # 回退为忽略环境变量代理的客户端，避免知识库完全不可用。
            if "Unknown scheme for proxy URL" in str(exc):
                logger.warning("Invalid proxy env detected; disabling trust_env for OpenAI client: %s", exc)
                self._client = OpenAI(**client_kwargs, http_client=httpx.Client(trust_env=False))
            else:
                raise
        logger.info(
            "Initialised OpenAI-compatible embeddings client (model=%s, base_url=%s)",
            model,
            base_url or "https://api.openai.com/v1",
        )

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        """批量将文本转为向量列表，顺序与 ``texts`` 一致。

        Args:
            texts: 任意可迭代字符串序列。

        Returns:
            与 ``texts`` 等长的浮点向量列表；详见 :meth:`_embed` 对空串、失败回退的说明。
        """
        return self._embed(texts)

    def embed_query(self, text: str) -> List[float]:
        """将单条查询文本转为一条向量（检索侧常用）。

        Args:
            text: 用户问题或查询句。

        Returns:
            一维浮点列表；输入异常或 API 无返回时可能为空列表，调用方需自行兜底。
        """
        embeddings = self._embed([text])
        return embeddings[0] if embeddings else []

    def _embed(self, texts: Sequence[str]) -> List[List[float]]:
        """核心嵌入逻辑：去空白、分批请求、校验条数并按原索引写回结果。

        - 空输入直接返回 ``[]``。
        - 单条为空白时替换为单个空格，避免部分厂商对空 ``input`` 报错，同时用
          ``index_map`` 保持与原始 ``texts`` 下标对齐。
        - 按 ``max_batch_size`` 切片调用 ``embeddings.create``。
        - 若配置了 ``dimension`` 且与返回长度不一致，仅记录警告，不修改向量。
        - 若某位置最终仍无向量，用零向量填充，维度取自首条成功向量或 ``dimension``。

        Args:
            texts: 待嵌入字符串序列。

        Returns:
            与 ``texts`` 等长的向量列表。

        Raises:
            RuntimeError: API 返回的向量条数与请求条数不一致时。
            Exception: 其它上游错误经日志后原样抛出。
        """
        if not texts:
            return []

        ordered_inputs: List[str] = []
        index_map: List[int] = []
        for idx, text in enumerate(texts):
            value = (text or "").strip()
            if not value:
                # DashScope 对空字符串会报错，使用单空格保持顺序
                value = " "
            ordered_inputs.append(value)
            index_map.append(idx)

        embeddings_flat: List[List[float]] = []
        try:
            for start in range(0, len(ordered_inputs), self.max_batch_size):
                batch = ordered_inputs[start:start + self.max_batch_size]
                response = self._client.embeddings.create(
                    model=self.model,
                    input=batch,
                    timeout=self.request_timeout,
                )

                for item in response.data:
                    vector = list(item.embedding)
                    if self.dimension and len(vector) != self.dimension:
                        logger.warning(
                            "Embedding dimension mismatch: expected=%s actual=%s",
                            self.dimension,
                            len(vector),
                        )
                    embeddings_flat.append(vector)

        except Exception as exc:  # pragma: no cover - surface upstream
            logger.exception("Embedding request failed: %s", exc)
            raise

        if len(embeddings_flat) != len(ordered_inputs):
            logger.error(
                "Embedding count mismatch (expected %s, received %s)",
                len(ordered_inputs),
                len(embeddings_flat),
            )
            raise RuntimeError("Embedding API returned unexpected number of vectors")

        result: List[List[float]] = [[] for _ in texts]
        for mapped_idx, vector in zip(index_map, embeddings_flat):
            result[mapped_idx] = vector

        if any(not vec for vec in result):
            fallback_dim = len(embeddings_flat[0]) if embeddings_flat else (self.dimension or 0)
            zero_vec = [0.0] * fallback_dim
            for idx, vec in enumerate(result):
                if not vec:
                    result[idx] = zero_vec

        return result
