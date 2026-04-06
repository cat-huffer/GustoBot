"""
基于 Milvus 的向量存储封装，与 ``KnowledgeService`` 配合完成菜谱知识库的持久化与检索。

集合固定字段：主键 ``id``（一般为分块 id）、``embedding``、正文 ``content``，以及
``recipe_id`` / ``name`` / ``category`` / ``difficulty`` 等标量元数据，与入库时
``metadatas`` 的键一一对应。连接、建表、加载均在构造时完成；异常会记录日志并向上抛出
（初始化阶段）或在各方法内返回空/False。
"""

from typing import List, Dict, Any, Optional

from loguru import logger
from pymilvus import (
    connections,
    Collection,
    CollectionSchema,
    FieldSchema,
    DataType,
    utility,
)


class VectorStore:
    """Milvus 集合的生命周期管理：连接、建表/加载、增删查与统计。

    与 ``KnowledgeService`` 约定：
    ``add_documents`` 接收的 ``ids`` 为各向量行的主键；``search`` 返回字典含 ``score``
   （由 ``metric_type`` 决定含义，如 IP 下通常为内积相关分值）、``content`` 与嵌套
    ``metadata``。
    """

    def __init__(
        self,
        collection_name: str = "recipes",
        host: str = "localhost",
        port: int = 19530,
        dimension: int = 1536,
        index_type: str = "IVF_FLAT",
        metric_type: str = "IP",  # Inner Product (cosine similarity)
    ) -> None:
        """连接 Milvus，若集合已存在则加载，否则创建 Schema、索引并 ``load``。

        Args:
            collection_name: 集合名，默认 ``recipes``。
            host: Milvus 服务地址。
            port: gRPC 端口，默认 ``19530``。
            dimension: 向量维度，须与嵌入模型输出一致。
            index_type: 向量索引类型，如 ``IVF_FLAT``、``IVF_SQ8``、``HNSW`` 等。
            metric_type: 距离/相似度度量，如 ``IP``、``L2``、``COSINE``；影响 ``search``
                返回的 ``score`` 解释与建索引参数。

        Note:
            初始化失败会打错误日志并重新抛出异常；成功后会将 ``self.collection`` 设为已
            ``load`` 的 ``Collection`` 实例。
        """
        self.collection_name = collection_name
        self.host = host
        self.port = port
        self.dimension = dimension
        self.index_type = index_type
        self.metric_type = metric_type

        self.collection = None

        self._initialize()

    def _initialize(self) -> None:
        """建立 ``default`` 别名连接，加载或创建集合并 ``collection.load()``。"""
        try:
            # 连接到Milvus
            connections.connect(
                alias="default",
                host=self.host,
                port=self.port,
            )
            logger.info(f"Connected to Milvus at {self.host}:{self.port}")

            # 检查集合是否存在
            if utility.has_collection(self.collection_name):
                self.collection = Collection(self.collection_name)
                logger.info(f"Loaded existing collection: {self.collection_name}")
            else:
                # 创建新集合
                self._create_collection()
                logger.info(f"Created new collection: {self.collection_name}")

            # 加载集合到内存
            self.collection.load()

        except Exception as e:
            logger.error(f"Failed to initialize Milvus: {e}")
            raise

    def _create_collection(self) -> None:
        """按当前 ``dimension`` / ``index_type`` / ``metric_type`` 新建集合并为 ``embedding`` 建索引。

        Schema 与 :meth:`add_documents` 插入列顺序一致：``id``、``embedding``、``content``、
        ``recipe_id``、``name``、``category``、``difficulty``。
        """
        # 定义字段
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=256),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.dimension),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="recipe_id", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="name", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="difficulty", dtype=DataType.VARCHAR, max_length=128),
        ]

        # 创建集合schema
        schema = CollectionSchema(
            fields=fields,
            description="Recipe knowledge base collection",
        )

        # 创建集合
        self.collection = Collection(
            name=self.collection_name,
            schema=schema,
        )

        # 创建索引
        index_params = {
            "index_type": self.index_type,
            "metric_type": self.metric_type,
            "params": {"nlist": 128} if self.index_type == "IVF_FLAT" else {},
        }

        self.collection.create_index(
            field_name="embedding",
            index_params=index_params,
        )

        logger.info(f"Created index with type: {self.index_type}")

    def add_documents(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        documents: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """批量插入向量行：主键、向量、正文及四条元数据列。

        各列表须等长。``metadatas`` 省略时按空字典补全；``None`` 元数据字段在入库前会
        转为空字符串。插入后 ``flush`` 保证落盘可见。

        Args:
            ids: Milvus 主键，通常为 ``chunk_id``。
            embeddings: 与 ``ids`` 一一对应的浮点向量。
            documents: 分块正文，写入 ``content`` 列。
            metadatas: 每条可选包含 ``recipe_id``、``name``、``category``、``difficulty``。

        Returns:
            成功为 ``True``；任一步异常则记日志并返回 ``False``。
        """
        try:
            if not metadatas:
                metadatas = [{}] * len(ids)

            def _as_varchar(value: Any) -> str:
                """标量元数据转字符串，``None`` → 空串。"""
                if value is None:
                    return ""
                return str(value)

            # 准备插入数据
            entities = [
                ids,
                embeddings,
                documents,
                [_as_varchar(meta.get("recipe_id")) for meta in metadatas],
                [_as_varchar(meta.get("name")) for meta in metadatas],
                [_as_varchar(meta.get("category")) for meta in metadatas],
                [_as_varchar(meta.get("difficulty")) for meta in metadatas],
            ]

            # 插入数据
            self.collection.insert(entities)
            self.collection.flush()

            logger.info(f"Added {len(ids)} documents to Milvus")
            return True

        except Exception as e:
            logger.error(f"Failed to add documents: {e}")
            return False

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """单向量近似最近邻搜索，可选布尔表达式过滤标量字段。

        使用 IVF 系索引时通过 ``nprobe`` 等参数控制召回；返回列表中每条含 ``id``、
        ``content``、``score`` 以及扁平 ``metadata``（与 Schema 中 scalar 字段对应）。

        Args:
            query_embedding: 查询向量，长度须等于 ``dimension``。
            top_k: 最多返回命中条数。
            filter_expr: Milvus 布尔表达式，如 ``category == '家常菜'``；``None`` 表示全表检索。

        Returns:
            结构化结果列表；异常时记日志并返回 ``[]``。
        """
        try:
            # 搜索参数
            search_params = {
                "metric_type": self.metric_type,
                "params": {"nprobe": 10},
            }

            # 执行搜索
            results = self.collection.search(
                data=[query_embedding],
                anns_field="embedding",
                param=search_params,
                limit=top_k,
                expr=filter_expr,
                output_fields=["id", "content", "recipe_id", "name", "category", "difficulty"],
            )

            # 格式化结果
            formatted_results = []
            for hits in results:
                for hit in hits:
                    formatted_results.append(
                        {
                            "id": hit.entity.get("id"),
                            "content": hit.entity.get("content"),
                            "score": float(hit.score),
                            "metadata": {
                                "recipe_id": hit.entity.get("recipe_id"),
                                "name": hit.entity.get("name"),
                                "category": hit.entity.get("category"),
                                "difficulty": hit.entity.get("difficulty"),
                            },
                        }
                    )

            logger.info(f"Found {len(formatted_results)} results")
            return formatted_results

        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    def delete_documents(self, ids: List[str]) -> bool:
        """按主键字段 ``id`` 批量删除（表达式 ``id in [...]``）。

        注意：传入的字符串须与入库时的 Milvus 主键一致；若业务上只有 ``recipe_id``，
        需与调用方约定是否先解析出所有 ``chunk_id`` 再删。

        Args:
            ids: 待删除行的 ``id`` 列表。

        Returns:
            成功为 ``True``；失败记日志并返回 ``False``。
        """
        try:
            expr = f"id in {ids}"
            self.collection.delete(expr)
            self.collection.flush()

            logger.info(f"Deleted {len(ids)} documents")
            return True
        except Exception as e:
            logger.error(f"Failed to delete documents: {e}")
            return False

    def get_collection_stats(self) -> Dict[str, Any]:
        """返回集合名、实体条数、向量维度及索引/度量配置摘要。"""
        try:
            stats = self.collection.num_entities
            return {
                "name": self.collection_name,
                "document_count": stats,
                "dimension": self.dimension,
                "index_type": self.index_type,
                "metric_type": self.metric_type,
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {}

    def clear_collection(self) -> bool:
        """删除当前集合并按相同 Schema 重建、建索引、重新 ``load``。

        会丢失集合内全部数据，仅适合开发或全量重导场景。

        Returns:
            成功为 ``True``；失败记日志并返回 ``False``。
        """
        try:
            self.collection.drop()
            self._create_collection()
            self.collection.load()

            logger.info(f"Cleared collection: {self.collection_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to clear collection: {e}")
            return False

    def close(self) -> None:
        """断开 ``default`` 连接别名，释放客户端侧 Milvus 连接。"""
        try:
            connections.disconnect("default")
            logger.info("Disconnected from Milvus")
        except Exception as e:
            logger.error(f"Failed to disconnect: {e}")
