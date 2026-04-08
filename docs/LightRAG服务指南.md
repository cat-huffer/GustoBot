# LightRAG 服务指南

本文说明 GustoBot 中 **LightRAG** 的用途、索引目录、HTTP API、配置与排错。实现以当前代码为准：

- 服务：`gustobot/application/services/lightrag_service.py`
- 路由：`gustobot/interfaces/http/lightrag_router.py`（`prefix=/lightrag`，与 `API_V1_PREFIX` 组合）
- 配置：`gustobot/config/settings.py` 中 `LIGHTRAG_*`、`EMBEDDING_*`、`LLM_*`

LightRAG 既可 **独立通过 REST 调用**，也在主智能体图谱多工具流程中作为 **`customer_tools` 节点**（内部走同一套检索能力）。整体架构见 [项目架构.md](项目架构.md)；环境变量总表见 [环境变量与配置说明.md](环境变量与配置说明.md)。

---

## 1. 架构概要

### 1.1 构建期（常见：Docker 镜像构建）

将菜谱等文本经 LightRAG 管线处理，在 **`LIGHTRAG_WORKING_DIR`**（默认 `./data/lightrag`）生成持久化文件，例如：

| 文件 | 作用（概要） |
|------|----------------|
| `graph_chunk_entity_relation.graphml` | 块/实体/关系图结构 |
| `kv_store_doc_status.json` | 文档状态 |
| `kv_store_full_docs.json` | 全文档存储 |
| `kv_store_text_chunks.json` | 文本块 |
| `vdb_chunks.json` | 块向量库元数据（可含 `embedding_dim`） |
| `vdb_entities.json` | 实体向量 |
| `vdb_relationships.json` | 关系向量 |

具体体积随数据量变化；文档中的 MB 级数字仅为典型量级参考。

### 1.2 运行时

`LightRAGService` 在首次查询/插入前 `initialize()`：

1. 确保 `working_dir` 存在（不存在则创建空目录）。
2. 检查上述索引文件是否齐全；**缺失时仅打警告**，仍尝试 `initialize_storages()`（查询可能偏弱或异常，取决于库行为）。
3. **Embedding 维度**：优先读环境变量 `EMBEDDING_DIMENSION`；否则从 `vdb_*.json` 顶层字段 **`embedding_dim`** 推断；再不行则用 `settings.EMBEDDING_DIMENSION`（默认 1536）。
4. 使用 `settings` 中的 **LLM** 与 **Embedding** 调用 `openai_complete_if_cache` / `openai_embed`（兼容自建网关，见配置节）。

---

## 2. 检索模式

代码中 `SearchMode` 包含：

`naive`、`local`、`global`、`hybrid`、`mix`、`bypass`

| 模式 | 说明 | 典型用途 |
|------|------|----------|
| naive | 偏语义/向量检索 | 简单事实、关键词 |
| local | 局部图上下文 | 与具体实体强相关 |
| global | 全局图摘要类检索 | 宏观概括 |
| hybrid | 混合（默认推荐） | 平衡效果与延迟 |
| mix / bypass | 依 LightRAG 版本与内部语义而定 | 进阶或特殊路径；调用前建议对照官方文档 |

**调试接口** `POST /api/v1/lightrag/test-modes` 当前仅对 **`naive`、`local`、`global`、`hybrid`** 四种做循环对比，不包含 `mix`/`bypass`。

---

## 3. HTTP API 一览

基址：`{API根}/api/v1/lightrag`，例如 `http://localhost:8000/api/v1/lightrag`。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/query` | 非流式问答；请求体 **`stream` 必须为 `false`**，否则 400 |
| POST | `/query-stream` | SSE 流式；内部强制 `stream=True` |
| POST | `/insert` | 增量插入字符串文档列表 |
| GET | `/stats` | 索引文件存在性与体积、`initialized` 标志 |
| POST | `/test-modes?query=...` | 对四种主模式跑同一问题并返回对比 |

### 3.1 非流式查询

**`POST /api/v1/lightrag/query`**

请求体示例：

```json
{
  "query": "红烧肉怎么做？",
  "mode": "hybrid",
  "top_k": 10,
  "stream": false
}
```

响应为 `LightRAGQueryResponse`：`query`、`response`、`mode`、`metadata`（含 `top_k`、`working_dir` 等）。

### 3.2 流式查询（SSE）

**`POST /api/v1/lightrag/query-stream`**

- 响应 `Content-Type: text/event-stream`。
- 每段一般为 `data: <内容>\n\n`。
- 出错时可能推送 `data: [ERROR] ...\n\n`。
- 结束统一发送 `data: [DONE]\n\n`（无论成功与否，便于客户端收尾）。

**兼容说明**：若底层库在 `stream=True` 时返回普通字符串，服务会**包装为单次 yield**，仍保持 SSE 形态。

### 3.3 增量插入

**`POST /api/v1/lightrag/insert`**

```json
{
  "documents": [
    "红烧肉是一道经典的中华料理……",
    "宫保鸡丁起源于四川……"
  ]
}
```

`insert_documents` 内部对每条调用 `rag.ainsert`；`batch_size` 仅控制**日志进度**频率（默认 10），不是 HTTP 参数。

### 3.4 索引统计

**`GET /api/v1/lightrag/stats`**

返回 `working_dir`、`total_size_mb`、`files` 各文件 `exists` / `size_bytes` / `size_mb`、`initialized`（是否已执行过完整 `initialize()`）。

---

## 4. 快速验证

### 4.1 本地 curl

```bash
curl -X POST "http://localhost:8000/api/v1/lightrag/query" ^
  -H "Content-Type: application/json" ^
  -d "{\"query\": \"红烧肉怎么做？\", \"mode\": \"hybrid\", \"top_k\": 10, \"stream\": false}"
```

（Linux/macOS 将 `^` 换为 `\` 或写成单行。）

### 4.2 Docker 内检查索引目录

服务名以你仓库 `docker-compose.yml` 为准（原文档中的 `server` 仅为示例）：

```bash
docker compose exec <服务名> ls -lh /app/data/lightrag
```

### 4.3 脚本

仓库提供 `scripts/test_lightrag_service.py`，可在容器内执行用于联调（路径与 Python 模块方式见脚本头部说明）。

---

## 5. 应用内调用（Python）

```python
import asyncio
from gustobot.application.services.lightrag_service import (
    get_lightrag_service,
    LightRAGQueryRequest,
)

async def main():
    service = get_lightrag_service()
    await service.initialize()

    r = await service.query_structured(
        LightRAGQueryRequest(
            query="麻婆豆腐怎么做？",
            mode="hybrid",
            top_k=5,
            stream=False,
        )
    )
    print(r.response)

    stats = service.get_index_stats()
    print(stats["total_size_mb"], "MB")

    await service.cleanup()

asyncio.run(main())
```

单例通过 `get_lightrag_service()` 获取，默认 `working_dir`、`default_mode`、`default_top_k` 来自 `settings`。

---

## 6. 配置说明

### 6.1 与 LightRAG 直接相关的 `settings`

| 变量 | 含义 |
|------|------|
| `LIGHTRAG_WORKING_DIR` | 索引与工作目录，默认 `./data/lightrag` |
| `LIGHTRAG_RETRIEVAL_MODE` | 默认检索模式字符串 |
| `LIGHTRAG_TOP_K` | 默认 top_k |
| `LIGHTRAG_MAX_TOKEN_SIZE` | 嵌入函数 `max_token_size` |
| `LIGHTRAG_ENABLE_NEO4J` / `LIGHTRAG_ENABLE_MILVUS` | 与 LightRAG 存储后端相关的开关（是否生效取决于所用 LightRAG 版本与构建方式） |
| `INIT_LIGHTRAG_ON_BUILD` | 是否在镜像构建阶段跑初始化 |
| `LIGHTRAG_INIT_LIMIT` | 构建时初始化文档条数上限，`None` 表示不限制 |

### 6.2 LLM 与 Embedding

查询与插入依赖：

- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`（及 OpenAI 兼容别名，见 `settings`）
- `EMBEDDING_MODEL`、`EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`
- **`EMBEDDING_DIMENSION`**：与索引一致；**未设置时优先从 `vdb_*.json` 推断**，避免维度不匹配。

`.env` 示例（按需删减）：

```bash
LIGHTRAG_WORKING_DIR=./data/lightrag
LIGHTRAG_RETRIEVAL_MODE=hybrid
LIGHTRAG_TOP_K=10
LIGHTRAG_MAX_TOKEN_SIZE=4096

INIT_LIGHTRAG_ON_BUILD=true
LIGHTRAG_INIT_LIMIT=

LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=1536
```

---

## 7. 故障排查

| 现象 | 建议 |
|------|------|
| 查询空、弱或告警「索引文件不存在」 | 检查 `LIGHTRAG_WORKING_DIR` 下 7 个核心文件是否齐全；重新执行构建期初始化或本地运行 `scripts/init_lightrag.py` |
| 500 / dimension mismatch | 对齐 **构建索引时** 与 **运行时** 的 embedding 模型与维度；设置 `EMBEDDING_DIMENSION` 或重建索引 |
| 流式中断 | 加大客户端超时；查看服务端日志；确认代理/网关是否缓冲 SSE |
| OOM | 缩小并发、降低 `top_k`、增加容器内存限制 |
| `/query` 返回 400 | 确认未将 `stream: true` 发到 `/query`，应改用 `/query-stream` |

---

## 8. 与其他模块的关系

- **Neo4j 菜谱图谱**：结构化关系查询、Cypher；LightRAG 侧重文本+图增强检索与问答，二者互补（对比可参考下表）。

| 维度 | LightRAG | Neo4j 菜谱 KG |
|------|----------|----------------|
| 数据形态 | 文档块 + 向量 + 图文件 | 实体与关系 |
| 典型入口 | `/api/v1/lightrag/*`、Agent `customer_tools` | `/api/v1/knowledge/*`、图谱多工具 |
| 更新 | `insert` 增量 | 图谱导入 / Cypher |

---

## 9. 最佳实践（简）

1. 生产环境默认 **`hybrid`**，再按延迟与效果微调。  
2. **构建期**生成索引、**运行期**只加载，避免在请求路径做重索引。  
3. 变更 embedding 模型后应 **重建索引** 并同步 `EMBEDDING_DIMENSION`。  
4. 定期备份 `LIGHTRAG_WORKING_DIR` 目录。  
5. 需要实时输出时优先 **SSE** 接口。

---

## 10. 参考链接与源码

- LightRAG 上游：<https://github.com/HKUDS/LightRAG>  
- 官方文档：<https://lightrag.readthedocs.io/>  
- 本项目：`gustobot/application/services/lightrag_service.py`、`gustobot/interfaces/http/lightrag_router.py`、`gustobot/main.py`（路由挂载）

---

*若 API 或 `SearchMode` 与当前分支不一致，以 `lightrag_router.py` 与 `lightrag_service.py` 为准并更新本节。*
