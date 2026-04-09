# `gustobot/` 包目录架构说明

**数据流（一句话）**：HTTP 收请求 → `application` 组 LangGraph → `infrastructure` 连库与向量；图谱多工具子图在 `kg_sub_graph/agentic_rag_agents`。

---

## 分层一览

| 层 | 目录 | 职责（一句话） |
|----|------|----------------|
| 入口 | `main.py` | FastAPI、路由、DB 初始化、静态资源、启动 |
| 配置 | `config/` | `settings` 与环境变量 |
| 接口 | `interfaces/http/` | REST 与 Pydantic 模型 |
| 应用 | `application/` | 智能体图、工具节点、服务、提示词 |
| 基建 | `infrastructure/` | 日志、ORM、CRUD、向量/图谱/重排 |
| 领域 | `domain/` | 领域模型 |
| 其它 | `crawler/` | 离线爬虫，非在线主链路 |

---

## 阅读约定

- 路径均以包根 **`gustobot/`** 为起点（写作时省略该前缀）。
- **`__init__.py`**：未单独展开时，表示「包标识 + 导出子模块/`__all__`」；若下文写了具体导出内容，以该节为准。
- 下列 **「文件概述」** 按仓库当前实现归纳；个别空文件标注为占位。
- 下节 **目录树** 展示文件夹嵌套关系；**不含** `__pycache__`。更细的「每个文件一句话」仍见后文各章表格。

---

## 完整目录树（层级结构，便于建立整体印象）

### 包根 `gustobot/`

```
gustobot/
├── __init__.py
├── main.py                          # FastAPI 入口
├── config/                          # 全局配置
├── domain/                          # 领域模型
│   └── models/
├── interfaces/                      # HTTP 接口层
│   └── http/
│       ├── knowledge_router.py
│       ├── lightrag_router.py
│       ├── models/                  # API Pydantic 模型
│       └── v1/                      # 版本化 REST：chat / sessions / upload
├── application/                     # 应用与智能体
│   ├── prompts/
│   ├── services/                    # LightRAG、LLM、Redis、搜索服务
│   └── agents/                      # 见下「agents」子树
├── infrastructure/                  # 日志、DB、知识、持久化、工具
│   ├── core/
│   ├── knowledge/
│   │   └── recipe_kg/               # 菜谱图谱问答；含 dicts/ 词典文件
│   ├── persistence/
│   │   ├── db/models/
│   │   └── crud/
│   └── tools/
└── crawler/                         # 离线爬虫
```

### `interfaces/http/`（API 层，逐文件展开）

```
interfaces/http/
├── __init__.py
├── knowledge_router.py
├── lightrag_router.py
├── models/
│   ├── __init__.py
│   ├── chat.py
│   ├── chat_session.py
│   ├── chat_message.py
│   ├── chat_history.py
│   └── user.py
└── v1/
    ├── __init__.py
    ├── chat.py
    ├── sessions.py
    └── upload.py
```

### `application/`（除 agents 外）

```
application/
├── __init__.py
├── prompts/
│   ├── __init__.py
│   └── search_prompts.py
└── services/
    ├── __init__.py
    ├── lightrag_service.py
    ├── llm_client.py
    ├── redis_cache.py
    └── search_service.py
```

### `application/agents/`（智能体根）

```
application/agents/
├── __init__.py
├── lg_builder.py                    # 主 LangGraph 装配（总入口）
├── lg_prompts.py
├── lg_states.py
├── utils.py
├── main.py                          # CLI 调试入口
├── kb_tools/
│   ├── __init__.py
│   ├── node.py
│   └── prompts.py
├── text2sql/                        # 独立 Text2SQL 图；`components/*` 多为对 agentic 实现的再导出
│   ├── __init__.py
│   ├── workflow.py
│   ├── state.py
│   ├── models.py
│   ├── utils.py
│   └── components/
│       ├── __init__.py
│       ├── schema_retrieval/
│       │   ├── __init__.py
│       │   └── node.py
│       ├── sql_generation/
│       │   ├── __init__.py
│       │   ├── node.py
│       │   └── prompts.py
│       ├── sql_validation/
│       │   ├── __init__.py
│       │   ├── node.py
│       │   └── validators.py
│       └── sql_execution/
│           ├── __init__.py
│           └── node.py
└── kg_sub_graph/
    ├── __init__.py
    ├── kg_neo4j_conn.py
    ├── kg_tools_list.py
    ├── kg_states.py
    ├── kg_builder.py                # 可能为空；装配以 lg_builder + agentic 为准
    ├── multi_tools.py
    ├── prompts/
    │   ├── __init__.py
    │   ├── kg_prompts.py
    │   └── schema_utils.py
    ├── planner/
    │   ├── __init__.py
    │   └── planner_node.py
    ├── ps_genai_agents/
    │   ├── __init__.py
    │   └── components/
    │       ├── __init__.py
    │       └── guardrails/
    │           ├── __init__.py
    │           └── prompts.py
    └── agentic_rag_agents/          # 见下「最深」子树
```

### `agentic_rag_agents/`（图谱多工具子图，嵌套最深）

```
agentic_rag_agents/
├── __init__.py
├── agent.py                         # LangGraph Studio 调试
├── agent_cooking_assistant.py
├── constants.py
├── exceptions.py
├── utils/
│   ├── __init__.py
│   └── config.py
├── embeddings/
│   ├── __init__.py
│   └── embedder_protocol.py
├── retrievers/
│   ├── __init__.py
│   └── cypher_examples/
│       ├── __init__.py
│       ├── base.py
│       ├── recipe_retriever.py
│       ├── dynamic_schema_retriever.py
│       └── vector_store/
│           ├── __init__.py
│           └── neo4j_vector_example_retriever.py
├── ingest/
│   ├── __init__.py
│   └── cypher_examples/
│       ├── __init__.py
│       ├── models.py
│       ├── utils.py
│       └── ingest_neo4j.py
├── workflows/
│   ├── __init__.py
│   ├── multi_agent/
│   │   ├── __init__.py
│   │   ├── multi_tool.py            # 主多工具图 ★
│   │   ├── edges.py
│   │   ├── text2cypher_with_viz_and_follow_ups.py
│   │   ├── text2cypher.py           # 可能为空（占位）
│   │   └── text2cypher_with_visualization.py
│   └── single_agent/
│       ├── __init__.py
│       ├── text2cypher.py
│       └── visualization.py
├── ui/
│   ├── __init__.py
│   └── components/
│       ├── __init__.py
│       ├── chat.py
│       └── sidebar.py
└── components/                      # 根上另有 __init__.py；子目录多为 __init__.py + node.py（± prompts/models），详表见后文
    ├── __init__.py
    ├── state.py
    ├── models.py
    ├── guardrails/
    ├── planner/
    ├── tool_selection/
    ├── cypher_tools/
    ├── predefined_cypher/           # cypher_dict、descriptions、node、utils
    ├── text2cypher/                 # generation / validation / correction / execution
    │   └── validation/utils/        # cypher_extractors、regex、utils
    ├── text2sql/                    # 与独立 text2sql 共用实现
    │   ├── domain_knowledge.py
    │   ├── schema_retrieval/
    │   ├── query_analysis/
    │   ├── sql_generation/
    │   ├── sql_validation/
    │   ├── sql_execution/
    │   ├── visualization/
    │   └── formatting/
    ├── customer_tools/              # LightRAG
    ├── gather_cypher/
    ├── gather_visualizations/
    ├── summarize/
    ├── final_answer/
    ├── validate_final_answer/
    ├── visualize/                   # generate_chart、generate_details、validate、correct_details
    ├── errors/tool_selection/
    └── utils/                       # regex_patterns、utils
```

### `infrastructure/knowledge/recipe_kg/`（含词典资源）

```
infrastructure/knowledge/recipe_kg/
├── __init__.py
├── neo4j_qa_service.py
├── qa_pipeline_orchestrator.py
├── query_parser_service.py
├── question_intent_classifier.py
├── answer_search_engine.py
├── graph_database_client.py
├── graph_importer_service.py
├── graph_cache_loader.py
├── recipe_json_parser.py
├── fuzzy_matcher.py
└── dicts/                           # 文本词典（意图/实体等，供解析或匹配）
    ├── caixi.txt
    ├── deny.txt
    ├── gongyi.txt
    ├── haoshi.txt
    ├── kouwei.txt
    ├── leixing.txt
    ├── material.txt
    ├── recipe.txt
    └── yongliang.txt
```

### `infrastructure/` 其余（与树对应）

```
infrastructure/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── logger.py
│   ├── database.py
│   ├── hashing.py
│   ├── middleware.py
│   └── security.py
├── knowledge/
│   ├── __init__.py
│   ├── embeddings.py
│   ├── vector_store.py
│   ├── reranker.py
│   ├── knowledge_service.py
│   ├── recipe_import.py
│   └── recipe_kg/                 # 见上一节完整树（含 dicts/*.txt）
├── persistence/
│   ├── __init__.py
│   ├── db/
│   │   ├── __init__.py
│   │   └── models/
│   │       ├── __init__.py
│   │       ├── user.py
│   │       ├── chat_session.py
│   │       ├── chat_message.py
│   │       ├── chat_history.py
│   │       ├── conversation.py
│   │       └── message.py
│   └── crud/
│       ├── __init__.py
│       ├── base.py
│       ├── chat_history.py
│       ├── conversation.py
│       ├── crud_chat_message.py
│       └── crud_chat_session.py
└── tools/
    ├── __init__.py
    ├── definitions.py
    └── search.py
```

### `config/`、`domain/`、`crawler/`（浅层，与表格一致）

```
config/
├── __init__.py
└── settings.py

domain/
├── __init__.py
└── models/
    └── __init__.py

crawler/
├── __init__.py
├── cli.py
└── wikipedia.py
```

**阅读方式**：上面各 **目录树** 负责「嵌套到哪一层、文件夹之间什么关系」；后面各章 **表格** 负责「每个文件具体干什么」。`agentic_rag_agents/components/` 下子目录多、文件模式重复（多为 `__init__.py` + `node.py` ± `prompts.py`），树中已折叠，**逐文件说明请看文档后半对应小节**。

### `components/text2cypher/` 与 `components/text2sql/`（深层展开示例）

其余 `components/*` 子目录结构类似，可按此对照；**每一文件的职责仍以表格为准**。

```
components/text2cypher/
├── __init__.py
├── models.py
├── schema.py
├── state.py
├── text2sql_tool.py
├── generation/
│   ├── __init__.py
│   ├── node.py
│   └── prompts.py
├── validation/
│   ├── __init__.py
│   ├── node.py
│   ├── models.py
│   ├── prompts.py
│   ├── validators.py
│   └── utils/
│       ├── __init__.py
│       ├── cypher_extractors.py
│       ├── regex_patterns.py
│       └── utils.py
├── correction/
│   ├── __init__.py
│   ├── node.py
│   └── prompts.py
└── execution/
    ├── __init__.py
    └── node.py

components/text2sql/
├── __init__.py
├── domain_knowledge.py
├── schema_retrieval/
│   ├── __init__.py
│   └── node.py
├── query_analysis/
│   ├── __init__.py
│   ├── node.py
│   └── prompts.py
├── sql_generation/
│   ├── __init__.py
│   ├── node.py
│   └── prompts.py
├── sql_validation/
│   ├── __init__.py
│   ├── node.py
│   └── validators.py
├── sql_execution/
│   ├── __init__.py
│   └── node.py
├── visualization/
│   ├── __init__.py
│   ├── node.py
│   └── prompts.py
└── formatting/
    ├── __init__.py
    └── node.py
```

```
components/visualize/
├── __init__.py
├── state.py
├── models.py
├── schema.py
├── generate_chart/
│   ├── __init__.py
│   ├── node.py
│   └── charts.py
├── generate_details/
│   ├── __init__.py
│   ├── node.py
│   ├── models.py
│   └── prompts.py
├── validate_details/
│   ├── __init__.py
│   └── node.py
└── correct_details/
    ├── __init__.py
    ├── node.py
    └── prompts.py
```

---

## 包根

| 文件 | 概述 |
|------|------|
| `__init__.py` | 包元数据与 `__version__`。 |
| `main.py` | FastAPI 应用：CORS、`knowledge_router` / `lightrag_router` / `v1` 路由、静态目录 `/uploads`、启动时建表与 LightRAG 服务、根路径重定向文档。 |

---

## `config/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出配置相关符号（若有）。 |
| `settings.py` | Pydantic Settings：LLM、Embedding、Milvus、Neo4j、Redis、LightRAG、`DATABASE_URL` 等全局参数。 |

---

## `domain/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 领域包导出。 |
| `models/__init__.py` | 领域模型包导出。 |

---

## `crawler/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 爬虫包导出。 |
| `cli.py` | 命令行入口（抓取任务参数与调度）。 |
| `wikipedia.py` | Wikipedia 等来源的抓取逻辑。 |

---

## `interfaces/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 接口层包导出。 |

### `interfaces/http/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | HTTP 子包导出。 |
| `knowledge_router.py` | 菜谱/知识图谱相关路由（如 Neo4j QA 服务封装接口）。 |
| `lightrag_router.py` | LightRAG 相关 HTTP 接口。 |

#### `interfaces/http/models/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 聚合导出各 API 模型。 |
| `chat.py` | 聊天请求/响应等与对话相关的 Pydantic 模型。 |
| `chat_session.py` | 会话实体在 API 层的模型。 |
| `chat_message.py` | 单条消息在 API 层的模型。 |
| `chat_history.py` | 历史记录在 API 层的模型。 |
| `user.py` | 用户相关 API 模型。 |

#### `interfaces/http/v1/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 注册并导出 `api_router`（聚合 v1 子路由）。 |
| `chat.py` | 主对话、流式输出等 v1 端点。 |
| `sessions.py` | 会话创建、列表、删除等 v1 端点。 |
| `upload.py` | 文件上传相关 v1 端点。 |

---

## `application/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 应用层包导出。 |

### `application/prompts/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 提示词子包导出。 |
| `search_prompts.py` | 搜索/联网检索类提示模板。 |

### `application/services/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出 `lightrag_service` 等。 |
| `lightrag_service.py` | LightRAG 实例生命周期与全局 getter（供 `main` 等使用）。 |
| `llm_client.py` | LLM 调用封装（统一 base_url、重试等）。 |
| `redis_cache.py` | Redis 缓存读写封装。 |
| `search_service.py` | 搜索业务编排（调用基础设施层搜索工具等）。 |

### `application/agents/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | Agents 包导出。 |
| `lg_builder.py` | **主 LangGraph 装配**：路由节点、子图、工具、边；对接聊天主流程。 |
| `lg_prompts.py` | 主流程用大段系统提示与说明文本。 |
| `lg_states.py` | 主图 `State`、`InputState` 等类型定义。 |
| `utils.py` | Agent 侧通用工具（如 ID 生成、消息处理辅助）。 |
| `main.py` | **本地 CLI 调试入口**：加载 `lg_builder.graph`，`MemorySaver` + 流式打印，支持历史裁剪与 interrupt resume。 |

#### `application/agents/kb_tools/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出知识库工具节点。 |
| `node.py` | Milvus 等向量检索的 LangGraph 节点实现。 |
| `prompts.py` | 知识库检索节点用提示词。 |

#### `application/agents/text2sql/`

独立 Text2SQL LangGraph；`components` 下实现 **转发** 到 `agentic_rag_agents.components.text2sql`（避免重复实现）。

| 文件 | 概述 |
|------|------|
| `__init__.py` | 子包导出。 |
| `workflow.py` | 组装 `retrieve_schema` → `analyze` → `generate_sql` → `validate` → `execute` → `visualization` → `format_answer` 状态图。 |
| `state.py` | `Text2SQLState` / 输入输出状态类型。 |
| `models.py` | Text2SQL 相关 Pydantic/数据结构。 |
| `utils.py` | Text2SQL 辅助函数。 |
| `components/__init__.py` | **Re-export**：从 `agentic_rag_agents...text2sql` 导入各 `create_*_node`。 |
| `components/schema_retrieval/__init__.py` | 导出 `create_schema_retrieval_node`。 |
| `components/schema_retrieval/node.py` | **仅从** `agentic_rag_agents...text2sql.schema_retrieval.node` **再导出**实现。 |
| `components/sql_generation/__init__.py` | 导出 SQL 生成节点工厂。 |
| `components/sql_generation/node.py` | **再导出** agentic 包内 SQL 生成节点实现。 |
| `components/sql_generation/prompts.py` | **再导出** `create_sql_generation_prompt`、`format_schema_as_text`。 |
| `components/sql_validation/__init__.py` | 导出校验节点工厂。 |
| `components/sql_validation/node.py` | **再导出** agentic 包内校验节点。 |
| `components/sql_validation/validators.py` | **再导出** `validate_sql_syntax`、`validate_sql_security`。 |
| `components/sql_execution/__init__.py` | 导出执行节点工厂。 |
| `components/sql_execution/node.py` | **再导出** agentic 包内 SQL 执行节点。 |

#### `application/agents/kg_sub_graph/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 知识子图包导出。 |
| `kg_neo4j_conn.py` | 获取 `Neo4jGraph` / 驱动，供子图与工具使用。 |
| `kg_tools_list.py` | 子图工具 **Pydantic schema 类**（`cypher_query`、`predefined_cypher`、`microsoft_graphrag_query`、`text2sql_query`）供 `bind_tools`。 |
| `kg_states.py` | 子图专用状态类型（与主 `lg_states` 区分）。 |
| `kg_builder.py` | 子图构建占位；**当前为空**时以 `lg_builder` + `agentic_rag_agents` 为准。 |
| `multi_tools.py` | 对 `create_multi_tool_workflow` 等的再导出/薄封装，便于上层 import。 |

##### `kg_sub_graph/prompts/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 提示词子包导出。 |
| `kg_prompts.py` | 子图用系统提示（工具说明、路由、`TOOL_SELECTION` 等常量）。 |
| `schema_utils.py` | Schema 文本格式化等辅助。 |

##### `kg_sub_graph/planner/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 规划节点包导出。 |
| `planner_node.py` | 与菜谱子图配合的规划节点实现（若与 `agentic_rag` 内 planner 并存，以调用链为准）。 |

##### `kg_sub_graph/ps_genai_agents/`

历史/外部 **ps_genai** 风格代码残留。

| 文件 | 概述 |
|------|------|
| `__init__.py` | 子包导出。 |
| `components/__init__.py` | 组件子包导出。 |
| `components/guardrails/__init__.py` | 护栏组件导出。 |
| `components/guardrails/prompts.py` | 与 ps_genai 护栏相关的提示模板。 |

---

## `kg_sub_graph/agentic_rag_agents/`（图谱多工具子图核心）

| 文件 | 概述 |
|------|------|
| `__init__.py` | 子包导出。 |
| `agent.py` | LangGraph Studio / 调试：组装外部 `ps_genai_agents` 工作流与 Neo4j、向量检索器。 |
| `agent_cooking_assistant.py` | 菜谱场景下多工具工作流装配示例。 |
| `constants.py` | 子图常量。 |
| `exceptions.py` | 子图自定义异常。 |

### `agentic_rag_agents/utils/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 工具子包导出。 |
| `config.py` | 子图内可读的配置项/开关。 |

### `agentic_rag_agents/embeddings/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 嵌入子包导出。 |
| `embedder_protocol.py` | 嵌入函数协议/适配，供检索器使用。 |

### `agentic_rag_agents/retrievers/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 检索器根包导出。 |

#### `retrievers/cypher_examples/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | Cypher 示例检索子包导出。 |
| `base.py` | Cypher 示例检索抽象基类/协议。 |
| `recipe_retriever.py` | 菜谱场景 **RecipeCypherRetriever**：按图 Schema 等拉 Few-shot 示例。 |
| `dynamic_schema_retriever.py` | 动态 Schema 感知的示例检索（若启用）。 |
| `vector_store/__init__.py` | 向量存储检索子包导出。 |
| `vector_store/neo4j_vector_example_retriever.py` | 基于 Neo4j 向量索引的 Cypher 示例检索。 |

### `agentic_rag_agents/ingest/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 数据导入子包导出。 |

#### `ingest/cypher_examples/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | Cypher 示例导入子包导出。 |
| `models.py` | 导入任务数据结构。 |
| `ingest_neo4j.py` | 将 Cypher 示例写入 Neo4j（含向量索引相关逻辑）。 |
| `utils.py` | 导入过程辅助函数。 |

### `agentic_rag_agents/workflows/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 工作流包导出。 |

#### `workflows/multi_agent/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出 `create_multi_tool_workflow` 等。 |
| `multi_tool.py` | **主多工具图**：guardrails、planner、tool_selection、cypher/predefined/customer_tools/text2sql、summarize、final_answer 及边。 |
| `edges.py` | 条件边、`Send`/`Command`、Map-Reduce 等路由函数。 |
| `text2cypher_with_viz_and_follow_ups.py` | 带可视化与追问的多段流水线变体。 |
| `text2cypher.py` | 占位或历史文件（**若为空则以 multi_tool 为准**）。 |
| `text2cypher_with_visualization.py` | 占位或历史文件（**若为空则以其它工作流为准**）。 |

#### `workflows/single_agent/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 单智能体工作流导出。 |
| `text2cypher.py` | 单智能体 Text2Cypher 流程装配。 |
| `visualization.py` | 单智能体可视化相关流程。 |

### `agentic_rag_agents/ui/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | UI 子包导出。 |
| `components/__init__.py` | UI 组件导出。 |
| `components/chat.py` | 演示/聊天 UI 组件。 |
| `components/sidebar.py` | 侧边栏 UI 组件。 |

### `agentic_rag_agents/components/`（LangGraph 节点）

| 文件 | 概述 |
|------|------|
| `__init__.py` | 组件根导出。 |
| `state.py` | 多工具子图共享 **OverallState** 等状态类型。 |
| `models.py` | 跨节点 Pydantic 模型。 |

#### `components/guardrails/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出护栏节点工厂。 |
| `node.py` | 业务范围与敏感内容判断，不通过则直接收尾。 |
| `models.py` | 护栏结构化输出模型。 |
| `prompts.py` | 护栏提示模板。 |

#### `components/planner/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出规划节点。 |
| `node.py` | 将用户问题拆成子任务（供 Map-Reduce 与 tool_selection）。 |
| `models.py` | 规划结果结构。 |
| `prompts.py` | 规划提示模板。 |

#### `components/tool_selection/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出 `create_tool_selection_node`。 |
| `node.py` | 规则捷径 + `bind_tools` + `PydanticToolsParser(first_tool_only)` 选工具并 `Send` 路由。 |
| `models.py` | 工具选择相关类型。 |
| `prompts.py` | 包装 `TOOL_SELECTION` 等为 `ChatPromptTemplate`。 |

#### `components/cypher_tools/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出 Text2Cypher 主链节点。 |
| `node.py` | 串联生成、校验、纠错、执行 Neo4j，输出与其它工具对齐的状态。 |
| `prompts.py` | Cypher 生成/校验等提示。 |
| `utils.py` | Cypher 字符串处理、结果封装等。 |

#### `components/predefined_cypher/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出预定义查询节点。 |
| `node.py` | 按模板名执行 `cypher_dict` 中语句。 |
| `cypher_dict.py` | 模板名 → Cypher 语句映射。 |
| `descriptions.py` | 模板人类可读描述（供模型或文档使用）。 |
| `utils.py` | 参数替换、校验等。 |

#### `components/text2cypher/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出各子步骤节点工厂（生成/校验/执行等）。 |
| `models.py` | Text2Cypher 内部模型。 |
| `schema.py` | 图 Schema 片段类型。 |
| `state.py` | Text2Cypher 子状态机状态。 |
| `text2sql_tool.py` | **子图内 Text2SQL 工具包装**：建 `create_text2sql_workflow`、写回 `cyphers` 统一结构。 |
| `generation/__init__.py` | 导出生成节点。 |
| `generation/node.py` | LLM 生成 Cypher。 |
| `generation/prompts.py` | 生成阶段提示。 |
| `validation/__init__.py` | 导出校验节点。 |
| `validation/node.py` | 调用校验器决定是否重试或执行。 |
| `validation/models.py` | 校验结构化输出。 |
| `validation/prompts.py` | LLM 校验提示。 |
| `validation/validators.py` | 规则/语法校验实现。 |
| `validation/utils/__init__.py` | 校验工具子包。 |
| `validation/utils/cypher_extractors.py` | 从模型输出抽取 Cypher 文本。 |
| `validation/utils/regex_patterns.py` | Cypher 相关正则。 |
| `validation/utils/utils.py` | 其它校验辅助。 |
| `correction/__init__.py` | 导出纠错节点。 |
| `correction/node.py` | 失败时 LLM 修正 Cypher。 |
| `correction/prompts.py` | 纠错提示。 |
| `execution/__init__.py` | 导出执行节点。 |
| `execution/node.py` | 在 Neo4j 上执行 Cypher 并收集结果。 |

#### `components/text2sql/`（子图内 Text2SQL 节点实现）

与 `application/agents/text2sql/workflow.py` 使用的工厂一致。

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出 `create_schema_retrieval_node` 等全部节点工厂。 |
| `domain_knowledge.py` | 业务域补充说明，辅助 SQL 生成。 |
| `schema_retrieval/__init__.py` | 导出 Schema 检索节点。 |
| `schema_retrieval/node.py` | 从 MySQL `INFORMATION_SCHEMA` 取相关表结构。 |
| `query_analysis/__init__.py` | 导出问句分析节点。 |
| `query_analysis/node.py` | LLM 解析用户意图、实体。 |
| `query_analysis/prompts.py` | 分析阶段提示。 |
| `sql_generation/__init__.py` | 导出 SQL 生成节点。 |
| `sql_generation/node.py` | LLM 生成 SQL。 |
| `sql_generation/prompts.py` | SQL 生成提示。 |
| `sql_validation/__init__.py` | 导出校验节点。 |
| `sql_validation/node.py` | 校验与重试计数逻辑。 |
| `sql_validation/validators.py` | 方言相关语法检查等。 |
| `sql_execution/__init__.py` | 导出执行节点。 |
| `sql_execution/node.py` | 使用 `DATABASE_URL` 执行查询并限制行数。 |
| `visualization/__init__.py` | 导出可视化建议节点。 |
| `visualization/node.py` | 根据结果建议图表类型/配置。 |
| `visualization/prompts.py` | 可视化建议提示。 |
| `formatting/__init__.py` | 导出答案格式化节点。 |
| `formatting/node.py` | 将 SQL 结果整理为自然语言答案。 |

#### `components/customer_tools/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出 LightRAG 查询节点。 |
| `node.py` | **LightRAG**：初始化/查询 `LightRAGAPI`，对应工具 schema 常为 `microsoft_graphrag_query`。 |

#### `components/gather_cypher/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出聚合节点。 |
| `node.py` | 合并多子任务返回的 Cypher 结果列表。 |

#### `components/gather_visualizations/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出聚合节点。 |
| `node.py` | 合并可视化建议/配置。 |

#### `components/summarize/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出汇总节点。 |
| `node.py` | 将多工具子任务结果汇总为中间叙述。 |
| `prompts.py` | 汇总提示模板。 |

#### `components/final_answer/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出终答节点。 |
| `node.py` | 生成面向用户的最终回复文本。 |

#### `components/validate_final_answer/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出终答校验节点。 |
| `node.py` | 对最终答案做一致性/质量检查（若配置启用）。 |
| `models.py` | 校验结构化输出。 |
| `prompts.py` | 终答校验提示。 |

#### `components/visualize/`（Cypher 结果可视化链路）

| 文件 | 概述 |
|------|------|
| `__init__.py` | 可视化子包导出。 |
| `state.py` | 可视化流水线状态。 |
| `models.py` | 图表/细节相关模型。 |
| `schema.py` | 可视化 schema 定义。 |
| `generate_chart/__init__.py` | 图表生成子包导出。 |
| `generate_chart/node.py` | 生成图表规格或数据。 |
| `generate_chart/charts.py` | 具体图表类型构造逻辑。 |
| `generate_details/__init__.py` | 细节生成子包导出。 |
| `generate_details/node.py` | 生成数据细节描述。 |
| `generate_details/models.py` | 细节结构模型。 |
| `generate_details/prompts.py` | 细节生成提示。 |
| `validate_details/__init__.py` | 细节校验子包导出。 |
| `validate_details/node.py` | 校验生成细节是否合法。 |
| `correct_details/__init__.py` | 细节纠错子包导出。 |
| `correct_details/node.py` | LLM 修正细节。 |
| `correct_details/prompts.py` | 纠错提示。 |

#### `components/errors/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 错误处理子包导出。 |
| `tool_selection/__init__.py` | 工具选择错误节点导出。 |
| `tool_selection/node.py` | 无法选型时的错误分支与错误信息写入。 |

#### `components/utils/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 工具子包导出。 |
| `utils.py` | 组件层通用小函数。 |
| `regex_patterns.py` | 共享正则常量。 |

---

## `infrastructure/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 基础设施包导出。 |

### `infrastructure/core/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 导出 `configure_logging` 等。 |
| `logger.py` | Loguru/日志格式、级别与 `get_logger`。 |
| `database.py` | SQLAlchemy `engine`、`Base`、`get_db` 会话工厂；`main` 建表用。 |
| `hashing.py` | 密码或内容哈希工具。 |
| `middleware.py` | FastAPI 中间件（请求日志、计时等，以代码为准）。 |
| `security.py` | 安全相关工具（令牌、校验等）。 |

### `infrastructure/knowledge/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 知识层导出。 |
| `embeddings.py` | OpenAI 兼容 Embedding 客户端封装。 |
| `vector_store.py` | Milvus 等向量存储访问封装。 |
| `reranker.py` | 重排序 API 调用（如 DashScope rerank）。 |
| `knowledge_service.py` | 检索、融合等业务级知识服务编排。 |
| `recipe_import.py` | 菜谱数据导入管线入口。 |

#### `infrastructure/knowledge/recipe_kg/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 菜谱 KG 子包导出。 |
| `neo4j_qa_service.py` | 面向 HTTP 的菜谱 Neo4j 问答服务封装。 |
| `qa_pipeline_orchestrator.py` | 问句 → 意图 → 查询 → 答案 编排。 |
| `query_parser_service.py` | 问句解析为结构化查询条件。 |
| `question_intent_classifier.py` | 问题意图分类。 |
| `answer_search_engine.py` | 在图/索引上检索候选答案。 |
| `graph_database_client.py` | Neo4j 读写客户端封装。 |
| `graph_importer_service.py` | 图谱数据导入服务。 |
| `graph_cache_loader.py` | 图或字典缓存加载。 |
| `recipe_json_parser.py` | 菜谱 JSON 解析为图数据。 |
| `fuzzy_matcher.py` | 菜名/实体模糊匹配。 |

#### `recipe_kg/dicts/`（非 Python，资源文件）

| 文件 | 概述 |
|------|------|
| `caixi.txt` | 菜系等类别词表，供解析或匹配。 |
| `deny.txt` | 否定/排除类词表。 |
| `gongyi.txt` | 烹饪工艺相关词表。 |
| `haoshi.txt` | 耗时/时长相关词表。 |
| `kouwei.txt` | 口味相关词表。 |
| `leixing.txt` | 菜品类型词表。 |
| `material.txt` | 食材/原料词表。 |
| `recipe.txt` | 菜名词表或别名。 |
| `yongliang.txt` | 用量单位或用量相关词表。 |

> 具体字段含义以实现代码（如 `query_parser_service`、`fuzzy_matcher`）为准。

### `infrastructure/persistence/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 持久化包导出。 |

#### `infrastructure/persistence/db/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | DB 子包导出。 |

##### `infrastructure/persistence/db/models/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | ORM 模型聚合导出。 |
| `user.py` | 用户表 ORM。 |
| `chat_session.py` | 会话表 ORM。 |
| `chat_message.py` | 消息表 ORM。 |
| `chat_history.py` | 历史表 ORM（若与 message 并存则各管一维）。 |
| `conversation.py` | 会话/对话 ORM（与命名一致的具体字段见模型）。 |
| `message.py` | 通用消息 ORM。 |

#### `infrastructure/persistence/crud/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | CRUD 导出。 |
| `base.py` | 通用 CRUD 基类。 |
| `chat_history.py` | 聊天历史 CRUD。 |
| `conversation.py` | 对话 CRUD。 |
| `crud_chat_message.py` | 消息 CRUD。 |
| `crud_chat_session.py` | 会话 CRUD。 |

### `infrastructure/tools/`

| 文件 | 概述 |
|------|------|
| `__init__.py` | 工具包导出。 |
| `definitions.py` | 工具常量/Schema 定义（供 Agent 绑定）。 |
| `search.py` | 联网搜索等外部工具实现。 |

---

## 请求链路（串联）

`main.py` → `interfaces/http/v1/chat.py` → `application/agents/lg_builder.py` 主图 →（图谱分支）`agentic_rag_agents/workflows/multi_agent/multi_tool.py` 与各 `components/*` → 落库 `persistence/*`；向量/菜谱问答 `infrastructure/knowledge/*`；问数 `text2sql/workflow.py`（实现体在 `agentic_rag_agents/components/text2sql`）。

---

## 延伸阅读

- 《agent开发项目总览.md》  
- 《环境变量与配置说明.md》  

*表中所述与空文件状态随提交变化，请以当前仓库为准。*
