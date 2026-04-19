# 项目目录架构说明

除 **`kb_ingest/`** 专节外，树与表中的路径均相对 **`gustobot/`** 包根（省略该前缀书写）。**`kb_ingest/`** 在仓库根，为独立入库/检索服务 → **[§](#仓库根kb_ingest知识库入库服务)**。

**数据流**：HTTP → `application`（LangGraph）→ `infrastructure`（库/向量）；图谱多工具在 `kg_sub_graph/agentic_rag_agents`。

---

## 分层一览

| 层     | 目录               | 职责                            |
| ------ | ------------------ | ------------------------------- |
| 入口   | `main.py`          | FastAPI、路由、DB、静态资源     |
| 配置   | `config/`          | `settings`                      |
| 接口   | `interfaces/http/` | REST、Pydantic                  |
| 应用   | `application/`     | 智能体、服务、提示词            |
| 基建   | `infrastructure/`  | 日志、ORM、CRUD、向量/图谱      |
| 领域   | `domain/`          | 领域模型                        |
| 其它   | `crawler/`         | 离线爬虫                        |
| 仓库根 | `kb_ingest/`       | 表格→pgvector、检索 API（专节） |

---

## 阅读约定

- **`__init__.py`**：未展开时视为包导出。
- **目录树**：不含 `__pycache__`；行尾 **`# …`** 为职责摘要。后文 **文件概述** 表格与实现同步，以当前仓库为准。

---

## 完整目录树（层级结构，便于建立整体印象）

下文自 **`gustobot/`** 包根起逐段展开。**`kb_ingest/`**（仓库根独立子项目）的**完整文件树与逻辑分层**见文末 **[仓库根：kb_ingest](#仓库根kb_ingest知识库入库服务)**。

### 包根 `gustobot/`

```
gustobot/                              # Python 包根：后端业务与智能体主代码
├── __init__.py                        # 包元数据、版本号导出
├── main.py                            # FastAPI 入口：挂载路由、启动与建表
├── config/                            # 全局配置（环境变量 → settings）
├── domain/                            # 领域层：与框架无关的核心概念
│   └── models/                        # 领域模型定义（占位/扩展）
├── interfaces/                        # 接口适配层：对外 HTTP 契约
│   └── http/                          # REST 路由、v1 API、请求响应模型
│       ├── knowledge_router.py        # 菜谱批量导入、图谱问答等知识相关路由
│       ├── lightrag_router.py         # LightRAG 查询等独立路由
│       ├── models/                    # API 层 Pydantic 模型（chat/session 等）
│       └── v1/                        # 版本化 REST：对话、会话、上传
├── application/                       # 应用层：编排、服务、智能体图
│   ├── prompts/                       # 应用级提示词片段（如搜索）
│   ├── services/                      # LightRAG 单例、LLM、Redis、搜索编排
│   └── agents/                        # LangGraph 主图、子图、工具节点（见下）
├── infrastructure/                    # 基础设施：日志、存储、向量、工具实现
│   ├── core/                          # 日志、DB 引擎、中间件、安全、哈希
│   ├── knowledge/                     # Milvus、Embedding、知识服务、菜谱导入
│   │   └── recipe_kg/                 # 菜谱 Neo4j 问答管线；dicts/ 词典资源
│   ├── persistence/                   # 关系库持久化
│   │   ├── db/models/                 # SQLAlchemy ORM 表模型
│   │   └── crud/                      # 会话、消息、历史等 CRUD
│   └── tools/                         # 供 Agent 绑定的工具定义与搜索实现
└── crawler/                           # 离线爬虫：维基等 → JSON，非在线主链路
```

### `interfaces/http/`（API 层，逐文件展开）

```
interfaces/http/                       # 对外 HTTP：知识路由 + v1 业务 API
├── __init__.py                        # 子包导出
├── knowledge_router.py                # 知识库/图谱相关端点
├── lightrag_router.py                 # LightRAG 相关端点
├── models/                            # 请求体/响应体 Pydantic 模型
│   ├── __init__.py                    # 模型聚合导出
│   ├── chat.py                        # 对话请求与流式等模型
│   ├── chat_session.py                # 会话 API 模型
│   ├── chat_message.py                # 单条消息 API 模型
│   ├── chat_history.py                # 历史记录 API 模型
│   └── user.py                        # 用户 API 模型
└── v1/                                # /api/v1 下子路由聚合
    ├── __init__.py                    # 注册 api_router
    ├── chat.py                        # 主对话、流式输出
    ├── sessions.py                    # 会话 CRUD 类端点
    └── upload.py                      # 文件上传
```

### `application/`（除 agents 外）

```
application/                         # 应用层（不含 agents 时的公共部分）
├── __init__.py                        # 应用包导出
├── prompts/                           # 非 Agent 专有的提示词模板
│   ├── __init__.py
│   └── search_prompts.py              # 搜索/联网检索类提示
└── services/                          # 可注入业务服务（供 main、路由、Agent 用）
    ├── __init__.py
    ├── lightrag_service.py            # LightRAG 生命周期与全局 getter
    ├── llm_client.py                  # 统一 LLM 调用封装
    ├── redis_cache.py                 # Redis 读写封装
    └── search_service.py              # 搜索业务编排
```

### `application/agents/`（智能体根）

```
application/agents/                    # 智能体：主图、KB 工具、Text2SQL、图谱子图
├── __init__.py                        # agents 包导出
├── lg_builder.py                      # 主 LangGraph 装配（聊天总入口）
├── lg_prompts.py                      # 主流程系统提示与大段说明
├── lg_states.py                       # 主图 State / InputState
├── utils.py                           # Agent 侧通用辅助（消息、ID 等）
├── main.py                            # 本地 CLI：加载 graph、MemorySaver 调试
├── kb_tools/                          # Milvus 向量检索节点与提示
│   ├── __init__.py
│   ├── node.py                        # KB 检索 LangGraph 节点
│   └── prompts.py                     # KB 节点提示词
├── text2sql/                          # 独立 Text2SQL 状态图（节点多来自 agentic 再导出）
│   ├── __init__.py
│   ├── workflow.py                    # Schema→分析→生成→校验→执行→可视化→格式化
│   ├── state.py                       # Text2SQL 状态类型
│   ├── models.py                      # Text2SQL 数据结构
│   ├── utils.py                       # Text2SQL 辅助函数
│   └── components/                    # 各步节点工厂（转发 agentic 实现）
│       ├── __init__.py                # Re-export 各 create_*_node
│       ├── schema_retrieval/          # 拉表结构
│       │   ├── __init__.py
│       │   └── node.py
│       ├── sql_generation/            # LLM 生成 SQL
│       │   ├── __init__.py
│       │   ├── node.py
│       │   └── prompts.py
│       ├── sql_validation/            # 语法与安全校验
│       │   ├── __init__.py
│       │   ├── node.py
│       │   └── validators.py
│       └── sql_execution/             # 执行 SQL 并收结果
│           ├── __init__.py
│           └── node.py
└── kg_sub_graph/                      # 知识图谱子图：Neo4j、工具 schema、多工具编排入口
    ├── __init__.py
    ├── kg_neo4j_conn.py               # Neo4j 驱动 / LangChain Graph
    ├── kg_tools_list.py               # bind_tools 用 Pydantic 工具定义
    ├── kg_states.py                   # 子图专用状态
    ├── kg_builder.py                  # 子图构建占位（可能为空）
    ├── multi_tools.py                 # create_multi_tool_workflow 等薄封装
    ├── prompts/                       # 子图系统提示与 Schema 文本工具
    │   ├── __init__.py
    │   ├── kg_prompts.py
    │   └── schema_utils.py
    ├── planner/                       # 与菜谱子图配合的规划节点
    │   ├── __init__.py
    │   └── planner_node.py
    ├── ps_genai_agents/               # 历史 ps_genai 风格护栏提示残留
    │   ├── __init__.py
    │   └── components/
    │       ├── __init__.py
    │       └── guardrails/
    │           ├── __init__.py
    │           └── prompts.py
    └── agentic_rag_agents/            # 图谱多工具 LangGraph 核心（见下）
```

### `agentic_rag_agents/`（图谱多工具子图，嵌套最深）

```
agentic_rag_agents/                    # 图谱多工具子图：工作流 + 节点 + 检索 + 示例入库
├── __init__.py
├── agent.py                           # LangGraph Studio / 外部工作流调试入口
├── agent_cooking_assistant.py         # 菜谱场景多工具装配示例
├── constants.py                       # 子图常量
├── exceptions.py                      # 子图自定义异常
├── utils/                             # 子图配置与杂项
│   ├── __init__.py
│   └── config.py                      # 可读开关与参数
├── embeddings/                        # 嵌入协议（供检索器）
│   ├── __init__.py
│   └── embedder_protocol.py
├── retrievers/                        # Few-shot / 向量检索 Cypher 示例
│   ├── __init__.py
│   └── cypher_examples/
│       ├── __init__.py
│       ├── base.py                    # 检索器抽象
│       ├── recipe_retriever.py        # 菜谱场景示例检索
│       ├── dynamic_schema_retriever.py # 动态 Schema 感知检索
│       └── vector_store/
│           ├── __init__.py
│           └── neo4j_vector_example_retriever.py  # Neo4j 向量索引检索示例
├── ingest/                            # 将 Cypher 示例写入 Neo4j
│   ├── __init__.py
│   └── cypher_examples/
│       ├── __init__.py
│       ├── models.py                  # 导入数据结构
│       ├── utils.py                   # 导入辅助
│       └── ingest_neo4j.py            # 实际写入与索引
├── workflows/                         # 可编译 LangGraph 流水线定义
│   ├── __init__.py
│   ├── multi_agent/                   # 多工具主路径（Map-Reduce、Send 等）
│   │   ├── __init__.py
│   │   ├── multi_tool.py              # 主多工具图 ★（护栏→规划→选工具→各工具→汇总→终答）
│   │   ├── edges.py                   # 条件边、Send、Command 路由
│   │   ├── text2cypher_with_viz_and_follow_ups.py  # 可视化 + 追问变体
│   │   ├── text2cypher.py             # 占位或历史（以 multi_tool 为准）
│   │   └── text2cypher_with_visualization.py
│   └── single_agent/                  # 单智能体 Text2Cypher / 可视化
│       ├── __init__.py
│       ├── text2cypher.py
│       └── visualization.py
├── ui/                                # 演示用 Streamlit/UI 组件（非生产 API）
│   ├── __init__.py
│   └── components/
│       ├── __init__.py
│       ├── chat.py                    # 聊天 UI
│       └── sidebar.py                 # 侧边栏
└── components/                        # 多工具图各节点实现（多为 node + prompts）
    ├── __init__.py
    ├── state.py                       # OverallState 等共享状态
    ├── models.py                      # 跨节点 Pydantic 模型
    ├── guardrails/                    # 入口护栏
    ├── planner/                       # 子任务规划
    ├── tool_selection/                # 模型选工具 + Send 分发
    ├── cypher_tools/                  # Text2Cypher 整条链封装为「一工具」
    ├── predefined_cypher/             # 模板名 → 固定 Cypher（cypher_dict 等）
    ├── text2cypher/                   # 生成 / 校验 / 纠错 / 执行 子步骤
    │   └── validation/utils/          # 抽取 Cypher、正则等
    ├── text2sql/                      # 子图内 Text2SQL 全链路（与 application/text2sql 共用）
    │   ├── domain_knowledge.py
    │   ├── schema_retrieval/
    │   ├── query_analysis/
    │   ├── sql_generation/
    │   ├── sql_validation/
    │   ├── sql_execution/
    │   ├── visualization/
    │   └── formatting/
    ├── customer_tools/                # LightRAG 客户工具节点
    ├── gather_cypher/                 # 多路 Cypher 结果聚合
    ├── gather_visualizations/         # 多路可视化配置聚合
    ├── summarize/                     # 子任务结果中间汇总
    ├── final_answer/                  # 面向用户的终答生成
    ├── validate_final_answer/         # 终答质量检查（若启用）
    ├── visualize/                     # 图表与细节生成、校验、纠错
    ├── errors/tool_selection/         # 选工具失败分支
    └── utils/                         # 组件层正则与通用小函数
```

### `infrastructure/knowledge/recipe_kg/`（含词典资源）

```
infrastructure/knowledge/recipe_kg/    # 菜谱 Neo4j 问答：解析→意图→查图→答（供 knowledge_router 等）
├── __init__.py
├── neo4j_qa_service.py                # 对外服务封装（HTTP 层调用）
├── qa_pipeline_orchestrator.py        # 整条 QA 管线编排
├── query_parser_service.py            # 自然语言 → 结构化查询条件
├── question_intent_classifier.py      # 问题意图分类
├── answer_search_engine.py            # 图/索引上检索候选答案
├── graph_database_client.py           # Neo4j 访问封装
├── graph_importer_service.py          # 图谱数据导入
├── graph_cache_loader.py              # 图或缓存加载
├── recipe_json_parser.py              # 菜谱 JSON → 图侧结构
├── fuzzy_matcher.py                   # 菜名/实体模糊匹配
└── dicts/                             # 纯文本词表（菜系、口味、食材等）
    ├── caixi.txt                      # 菜系
    ├── deny.txt                       # 否定/排除词
    ├── gongyi.txt                     # 工艺
    ├── haoshi.txt                     # 耗时
    ├── kouwei.txt                     # 口味
    ├── leixing.txt                    # 类型
    ├── material.txt                   # 食材
    ├── recipe.txt                     # 菜名/别名
    └── yongliang.txt                  # 用量
```

### `infrastructure/` 其余（与树对应）

```
infrastructure/                        # 横切能力：日志、库、向量、CRUD、工具
├── __init__.py
├── core/                              # 应用基础设施（与业务弱耦合）
│   ├── __init__.py
│   ├── logger.py                      # Loguru 配置与 get_logger
│   ├── database.py                    # SQLAlchemy 引擎、Session、建表
│   ├── hashing.py                     # 哈希工具
│   ├── middleware.py                  # FastAPI 中间件
│   └── security.py                    # 安全相关工具
├── knowledge/                         # 向量库、Embedding、重排、菜谱 KG
│   ├── __init__.py
│   ├── embeddings.py                  # OpenAI 兼容 Embedding 客户端
│   ├── vector_store.py                # Milvus 封装
│   ├── reranker.py                    # 重排序 API
│   ├── knowledge_service.py           # 切块、入库、检索编排
│   ├── recipe_import.py               # 批量导入 JSON → 菜谱结构
│   └── recipe_kg/                     # 见上一节（Neo4j QA + dicts）
├── persistence/                       # 关系型数据：模型 + CRUD
│   ├── __init__.py
│   ├── db/                            # ORM 层
│   │   ├── __init__.py
│   │   └── models/                    # 表映射
│   │       ├── __init__.py
│   │       ├── user.py
│   │       ├── chat_session.py
│   │       ├── chat_message.py
│   │       ├── chat_history.py
│   │       ├── conversation.py
│   │       └── message.py
│   └── crud/                          # 数据访问封装
│       ├── __init__.py
│       ├── base.py                    # 通用 CRUD 基类
│       ├── chat_history.py
│       ├── conversation.py
│       ├── crud_chat_message.py
│       └── crud_chat_session.py
└── tools/                             # Agent 可用工具实现与定义
    ├── __init__.py
    ├── definitions.py                 # 工具 Schema / 常量
    └── search.py                      # 联网搜索等
```

### `config/`、`domain/`、`crawler/`（浅层，与表格一致）

```
config/                                # Pydantic Settings：集中读环境变量
├── __init__.py
└── settings.py                        # LLM、Milvus、Neo4j、DB URL 等

domain/                                # 领域层（当前较薄，可扩展实体）
├── __init__.py
└── models/
    └── __init__.py                    # 领域模型包占位

crawler/                               # 离线：维基摘要 → 菜谱 JSON → 可选 POST 后端
├── __init__.py
├── cli.py                             # argparse 子命令入口
└── wikipedia.py                       # MediaWiki API 与映射逻辑
```

**阅读方式**：上面各 **目录树** 负责「嵌套到哪一层、文件夹之间什么关系」；后面各章 **表格** 负责「每个文件具体干什么」。`agentic_rag_agents/components/` 下子目录多、文件模式重复（多为 `__init__.py` + `node.py` ± `prompts.py`），树中已折叠，**逐文件说明请看文档后半对应小节**。

### `components/text2cypher/` 与 `components/text2sql/`（深层展开示例）

其余 `components/*` 子目录结构类似，可按此对照；**每一文件的职责仍以表格为准**。

```
components/text2cypher/                # 自然语言 → Cypher：生成→校验→纠错→执行
├── __init__.py                        # 导出各步节点工厂
├── models.py                          # 内部数据结构
├── schema.py                          # 图 Schema 片段类型
├── state.py                           # 本子图状态机状态
├── text2sql_tool.py                   # 包装独立 Text2SQL 子图为工具
├── generation/                        # LLM 写 Cypher
│   ├── __init__.py                    # 导出生成节点
│   ├── node.py                        # 生成节点实现
│   └── prompts.py                     # 生成阶段提示
├── validation/                        # 语法/规则校验与抽取
│   ├── __init__.py
│   ├── node.py                        # 校验决策与重试
│   ├── models.py                      # 校验结构化输出
│   ├── prompts.py                     # LLM 校验提示
│   ├── validators.py                  # 规则/语法实现
│   └── utils/
│       ├── __init__.py
│       ├── cypher_extractors.py       # 从模型输出抠出 Cypher
│       ├── regex_patterns.py          # Cypher 相关正则
│       └── utils.py                   # 其它校验辅助
├── correction/                        # 失败时 LLM 修正 Cypher
│   ├── __init__.py
│   ├── node.py                        # 纠错节点
│   └── prompts.py                     # 纠错提示
└── execution/                         # Neo4j 执行并收集结果
    ├── __init__.py
    └── node.py                        # 执行节点

components/text2sql/                   # 自然语言 → SQL：与 application/text2sql 共用
├── __init__.py                        # 导出全部节点工厂
├── domain_knowledge.py                # 业务域补充，辅助 SQL
├── schema_retrieval/                  # INFORMATION_SCHEMA 拉表结构
│   ├── __init__.py
│   └── node.py                        # Schema 检索节点
├── query_analysis/                    # 意图与实体解析
│   ├── __init__.py
│   ├── node.py                        # 分析节点
│   └── prompts.py
├── sql_generation/                    # LLM 生成 SQL
│   ├── __init__.py
│   ├── node.py                        # SQL 生成节点
│   └── prompts.py
├── sql_validation/                    # 校验与重试计数
│   ├── __init__.py
│   ├── node.py                        # 校验节点
│   └── validators.py                  # 方言语法检查等
├── sql_execution/                     # 执行查询、限行
│   ├── __init__.py
│   └── node.py                        # 执行节点
├── visualization/                     # 结果可视化建议
│   ├── __init__.py
│   ├── node.py                        # 可视化建议节点
│   └── prompts.py
└── formatting/                        # 自然语言答案格式化
    ├── __init__.py
    └── node.py                        # 格式化节点
```

```
components/visualize/                  # Cypher 结果 → 图表与文字细节（生成/校验/纠错）
├── __init__.py                        # 子包导出
├── state.py                           # 可视化流水线状态
├── models.py                          # 图表与细节模型
├── schema.py                          # 可视化 schema
├── generate_chart/                    # 生成图表规格或数据
│   ├── __init__.py
│   ├── node.py                        # 图表生成节点
│   └── charts.py                      # 各图表类型构造
├── generate_details/                  # 生成数据细节描述
│   ├── __init__.py
│   ├── node.py                        # 细节生成节点
│   ├── models.py
│   └── prompts.py
├── validate_details/                  # 校验细节合法性
│   ├── __init__.py
│   └── node.py                        # 细节校验节点
└── correct_details/                   # LLM 修正细节
    ├── __init__.py
    ├── node.py                        # 细节纠错节点
    └── prompts.py
```

---

## 包根

| 文件          | 概述                                                                                                                                        |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py` | 包元数据与 `__version__`。                                                                                                                  |
| `main.py`     | FastAPI 应用：CORS、`knowledge_router` / `lightrag_router` / `v1` 路由、静态目录 `/uploads`、启动时建表与 LightRAG 服务、根路径重定向文档。 |

---

## `config/`

| 文件          | 概述                                                                                           |
| ------------- | ---------------------------------------------------------------------------------------------- |
| `__init__.py` | 导出配置相关符号（若有）。                                                                     |
| `settings.py` | Pydantic Settings：LLM、Embedding、Milvus、Neo4j、Redis、LightRAG、`DATABASE_URL` 等全局参数。 |

---

## `domain/`

| 文件                 | 概述             |
| -------------------- | ---------------- |
| `__init__.py`        | 领域包导出。     |
| `models/__init__.py` | 领域模型包导出。 |

---

## `crawler/`

| 文件           | 概述                               |
| -------------- | ---------------------------------- |
| `__init__.py`  | 爬虫包导出。                       |
| `cli.py`       | 命令行入口（抓取任务参数与调度）。 |
| `wikipedia.py` | Wikipedia 等来源的抓取逻辑。       |

---

## `interfaces/`

| 文件          | 概述           |
| ------------- | -------------- |
| `__init__.py` | 接口层包导出。 |

### `interfaces/http/`

| 文件                  | 概述                                                |
| --------------------- | --------------------------------------------------- |
| `__init__.py`         | HTTP 子包导出。                                     |
| `knowledge_router.py` | 菜谱/知识图谱相关路由（如 Neo4j QA 服务封装接口）。 |
| `lightrag_router.py`  | LightRAG 相关 HTTP 接口。                           |

#### `interfaces/http/models/`

| 文件              | 概述                                        |
| ----------------- | ------------------------------------------- |
| `__init__.py`     | 聚合导出各 API 模型。                       |
| `chat.py`         | 聊天请求/响应等与对话相关的 Pydantic 模型。 |
| `chat_session.py` | 会话实体在 API 层的模型。                   |
| `chat_message.py` | 单条消息在 API 层的模型。                   |
| `chat_history.py` | 历史记录在 API 层的模型。                   |
| `user.py`         | 用户相关 API 模型。                         |

#### `interfaces/http/v1/`

| 文件          | 概述                                        |
| ------------- | ------------------------------------------- |
| `__init__.py` | 注册并导出 `api_router`（聚合 v1 子路由）。 |
| `chat.py`     | 主对话、流式输出等 v1 端点。                |
| `sessions.py` | 会话创建、列表、删除等 v1 端点。            |
| `upload.py`   | 文件上传相关 v1 端点。                      |

---

## `application/`

| 文件          | 概述           |
| ------------- | -------------- |
| `__init__.py` | 应用层包导出。 |

### `application/prompts/`

| 文件                | 概述                      |
| ------------------- | ------------------------- |
| `__init__.py`       | 提示词子包导出。          |
| `search_prompts.py` | 搜索/联网检索类提示模板。 |

### `application/services/`

| 文件                  | 概述                                                     |
| --------------------- | -------------------------------------------------------- |
| `__init__.py`         | 导出 `lightrag_service` 等。                             |
| `lightrag_service.py` | LightRAG 实例生命周期与全局 getter（供 `main` 等使用）。 |
| `llm_client.py`       | LLM 调用封装（统一 base_url、重试等）。                  |
| `redis_cache.py`      | Redis 缓存读写封装。                                     |
| `search_service.py`   | 搜索业务编排（调用基础设施层搜索工具等）。               |

### `application/agents/`

| 文件            | 概述                                                                                                        |
| --------------- | ----------------------------------------------------------------------------------------------------------- |
| `__init__.py`   | Agents 包导出。                                                                                             |
| `lg_builder.py` | **主 LangGraph 装配**：路由节点、子图、工具、边；对接聊天主流程。                                           |
| `lg_prompts.py` | 主流程用大段系统提示与说明文本。                                                                            |
| `lg_states.py`  | 主图 `State`、`InputState` 等类型定义。                                                                     |
| `utils.py`      | Agent 侧通用工具（如 ID 生成、消息处理辅助）。                                                              |
| `main.py`       | **本地 CLI 调试入口**：加载 `lg_builder.graph`，`MemorySaver` + 流式打印，支持历史裁剪与 interrupt resume。 |

#### `application/agents/kb_tools/`

| 文件          | 概述                                     |
| ------------- | ---------------------------------------- |
| `__init__.py` | 导出知识库工具节点。                     |
| `node.py`     | Milvus 等向量检索的 LangGraph 节点实现。 |
| `prompts.py`  | 知识库检索节点用提示词。                 |

#### `application/agents/text2sql/`

独立 Text2SQL LangGraph；`components` 下实现 **转发** 到 `agentic_rag_agents.components.text2sql`（避免重复实现）。

| 文件                                      | 概述                                                                                                                      |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py`                             | 子包导出。                                                                                                                |
| `workflow.py`                             | 组装 `retrieve_schema` → `analyze` → `generate_sql` → `validate` → `execute` → `visualization` → `format_answer` 状态图。 |
| `state.py`                                | `Text2SQLState` / 输入输出状态类型。                                                                                      |
| `models.py`                               | Text2SQL 相关 Pydantic/数据结构。                                                                                         |
| `utils.py`                                | Text2SQL 辅助函数。                                                                                                       |
| `components/__init__.py`                  | **Re-export**：从 `agentic_rag_agents...text2sql` 导入各 `create_*_node`。                                                |
| `components/schema_retrieval/__init__.py` | 导出 `create_schema_retrieval_node`。                                                                                     |
| `components/schema_retrieval/node.py`     | **仅从** `agentic_rag_agents...text2sql.schema_retrieval.node` **再导出**实现。                                           |
| `components/sql_generation/__init__.py`   | 导出 SQL 生成节点工厂。                                                                                                   |
| `components/sql_generation/node.py`       | **再导出** agentic 包内 SQL 生成节点实现。                                                                                |
| `components/sql_generation/prompts.py`    | **再导出** `create_sql_generation_prompt`、`format_schema_as_text`。                                                      |
| `components/sql_validation/__init__.py`   | 导出校验节点工厂。                                                                                                        |
| `components/sql_validation/node.py`       | **再导出** agentic 包内校验节点。                                                                                         |
| `components/sql_validation/validators.py` | **再导出** `validate_sql_syntax`、`validate_sql_security`。                                                               |
| `components/sql_execution/__init__.py`    | 导出执行节点工厂。                                                                                                        |
| `components/sql_execution/node.py`        | **再导出** agentic 包内 SQL 执行节点。                                                                                    |

#### `application/agents/kg_sub_graph/`

| 文件               | 概述                                                                                                                                  |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py`      | 知识子图包导出。                                                                                                                      |
| `kg_neo4j_conn.py` | 获取 `Neo4jGraph` / 驱动，供子图与工具使用。                                                                                          |
| `kg_tools_list.py` | 子图工具 **Pydantic schema 类**（`cypher_query`、`predefined_cypher`、`microsoft_graphrag_query`、`text2sql_query`）供 `bind_tools`。 |
| `kg_states.py`     | 子图专用状态类型（与主 `lg_states` 区分）。                                                                                           |
| `kg_builder.py`    | 子图构建占位；**当前为空**时以 `lg_builder` + `agentic_rag_agents` 为准。                                                             |
| `multi_tools.py`   | 对 `create_multi_tool_workflow` 等的再导出/薄封装，便于上层 import。                                                                  |

##### `kg_sub_graph/prompts/`

| 文件              | 概述                                                        |
| ----------------- | ----------------------------------------------------------- |
| `__init__.py`     | 提示词子包导出。                                            |
| `kg_prompts.py`   | 子图用系统提示（工具说明、路由、`TOOL_SELECTION` 等常量）。 |
| `schema_utils.py` | Schema 文本格式化等辅助。                                   |

##### `kg_sub_graph/planner/`

| 文件              | 概述                                                                               |
| ----------------- | ---------------------------------------------------------------------------------- |
| `__init__.py`     | 规划节点包导出。                                                                   |
| `planner_node.py` | 与菜谱子图配合的规划节点实现（若与 `agentic_rag` 内 planner 并存，以调用链为准）。 |

##### `kg_sub_graph/ps_genai_agents/`

历史/外部 **ps_genai** 风格代码残留。

| 文件                                | 概述                             |
| ----------------------------------- | -------------------------------- |
| `__init__.py`                       | 子包导出。                       |
| `components/__init__.py`            | 组件子包导出。                   |
| `components/guardrails/__init__.py` | 护栏组件导出。                   |
| `components/guardrails/prompts.py`  | 与 ps_genai 护栏相关的提示模板。 |

---

## `kg_sub_graph/agentic_rag_agents/`（图谱多工具子图核心）

| 文件                         | 概述                                                                             |
| ---------------------------- | -------------------------------------------------------------------------------- |
| `__init__.py`                | 子包导出。                                                                       |
| `agent.py`                   | LangGraph Studio / 调试：组装外部 `ps_genai_agents` 工作流与 Neo4j、向量检索器。 |
| `agent_cooking_assistant.py` | 菜谱场景下多工具工作流装配示例。                                                 |
| `constants.py`               | 子图常量。                                                                       |
| `exceptions.py`              | 子图自定义异常。                                                                 |

### `agentic_rag_agents/utils/`

| 文件          | 概述                      |
| ------------- | ------------------------- |
| `__init__.py` | 工具子包导出。            |
| `config.py`   | 子图内可读的配置项/开关。 |

### `agentic_rag_agents/embeddings/`

| 文件                   | 概述                              |
| ---------------------- | --------------------------------- |
| `__init__.py`          | 嵌入子包导出。                    |
| `embedder_protocol.py` | 嵌入函数协议/适配，供检索器使用。 |

### `agentic_rag_agents/retrievers/`

| 文件          | 概述             |
| ------------- | ---------------- |
| `__init__.py` | 检索器根包导出。 |

#### `retrievers/cypher_examples/`

| 文件                                             | 概述                                                                 |
| ------------------------------------------------ | -------------------------------------------------------------------- |
| `__init__.py`                                    | Cypher 示例检索子包导出。                                            |
| `base.py`                                        | Cypher 示例检索抽象基类/协议。                                       |
| `recipe_retriever.py`                            | 菜谱场景 **RecipeCypherRetriever**：按图 Schema 等拉 Few-shot 示例。 |
| `dynamic_schema_retriever.py`                    | 动态 Schema 感知的示例检索（若启用）。                               |
| `vector_store/__init__.py`                       | 向量存储检索子包导出。                                               |
| `vector_store/neo4j_vector_example_retriever.py` | 基于 Neo4j 向量索引的 Cypher 示例检索。                              |

### `agentic_rag_agents/ingest/`

| 文件          | 概述               |
| ------------- | ------------------ |
| `__init__.py` | 数据导入子包导出。 |

#### `ingest/cypher_examples/`

| 文件              | 概述                                             |
| ----------------- | ------------------------------------------------ |
| `__init__.py`     | Cypher 示例导入子包导出。                        |
| `models.py`       | 导入任务数据结构。                               |
| `ingest_neo4j.py` | 将 Cypher 示例写入 Neo4j（含向量索引相关逻辑）。 |
| `utils.py`        | 导入过程辅助函数。                               |

### `agentic_rag_agents/workflows/`

| 文件          | 概述           |
| ------------- | -------------- |
| `__init__.py` | 工作流包导出。 |

#### `workflows/multi_agent/`

| 文件                                     | 概述                                                                                                                           |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `__init__.py`                            | 导出 `create_multi_tool_workflow` 等。                                                                                         |
| `multi_tool.py`                          | **主多工具图**：guardrails、planner、tool_selection、cypher/predefined/customer_tools/text2sql、summarize、final_answer 及边。 |
| `edges.py`                               | 条件边、`Send`/`Command`、Map-Reduce 等路由函数。                                                                              |
| `text2cypher_with_viz_and_follow_ups.py` | 带可视化与追问的多段流水线变体。                                                                                               |
| `text2cypher.py`                         | 占位或历史文件（**若为空则以 multi_tool 为准**）。                                                                             |
| `text2cypher_with_visualization.py`      | 占位或历史文件（**若为空则以其它工作流为准**）。                                                                               |

#### `workflows/single_agent/`

| 文件               | 概述                            |
| ------------------ | ------------------------------- |
| `__init__.py`      | 单智能体工作流导出。            |
| `text2cypher.py`   | 单智能体 Text2Cypher 流程装配。 |
| `visualization.py` | 单智能体可视化相关流程。        |

### `agentic_rag_agents/ui/`

| 文件                     | 概述                |
| ------------------------ | ------------------- |
| `__init__.py`            | UI 子包导出。       |
| `components/__init__.py` | UI 组件导出。       |
| `components/chat.py`     | 演示/聊天 UI 组件。 |
| `components/sidebar.py`  | 侧边栏 UI 组件。    |

### `agentic_rag_agents/components/`（LangGraph 节点）

| 文件          | 概述                                         |
| ------------- | -------------------------------------------- |
| `__init__.py` | 组件根导出。                                 |
| `state.py`    | 多工具子图共享 **OverallState** 等状态类型。 |
| `models.py`   | 跨节点 Pydantic 模型。                       |

#### `components/guardrails/`

| 文件          | 概述                                       |
| ------------- | ------------------------------------------ |
| `__init__.py` | 导出护栏节点工厂。                         |
| `node.py`     | 业务范围与敏感内容判断，不通过则直接收尾。 |
| `models.py`   | 护栏结构化输出模型。                       |
| `prompts.py`  | 护栏提示模板。                             |

#### `components/planner/`

| 文件          | 概述                                                      |
| ------------- | --------------------------------------------------------- |
| `__init__.py` | 导出规划节点。                                            |
| `node.py`     | 将用户问题拆成子任务（供 Map-Reduce 与 tool_selection）。 |
| `models.py`   | 规划结果结构。                                            |
| `prompts.py`  | 规划提示模板。                                            |

#### `components/tool_selection/`

| 文件          | 概述                                                                                    |
| ------------- | --------------------------------------------------------------------------------------- |
| `__init__.py` | 导出 `create_tool_selection_node`。                                                     |
| `node.py`     | 规则捷径 + `bind_tools` + `PydanticToolsParser(first_tool_only)` 选工具并 `Send` 路由。 |
| `models.py`   | 工具选择相关类型。                                                                      |
| `prompts.py`  | 包装 `TOOL_SELECTION` 等为 `ChatPromptTemplate`。                                       |

#### `components/cypher_tools/`

| 文件          | 概述                                                         |
| ------------- | ------------------------------------------------------------ |
| `__init__.py` | 导出 Text2Cypher 主链节点。                                  |
| `node.py`     | 串联生成、校验、纠错、执行 Neo4j，输出与其它工具对齐的状态。 |
| `prompts.py`  | Cypher 生成/校验等提示。                                     |
| `utils.py`    | Cypher 字符串处理、结果封装等。                              |

#### `components/predefined_cypher/`

| 文件              | 概述                                   |
| ----------------- | -------------------------------------- |
| `__init__.py`     | 导出预定义查询节点。                   |
| `node.py`         | 按模板名执行 `cypher_dict` 中语句。    |
| `cypher_dict.py`  | 模板名 → Cypher 语句映射。             |
| `descriptions.py` | 模板人类可读描述（供模型或文档使用）。 |
| `utils.py`        | 参数替换、校验等。                     |

#### `components/text2cypher/`

| 文件                                    | 概述                                                                                   |
| --------------------------------------- | -------------------------------------------------------------------------------------- |
| `__init__.py`                           | 导出各子步骤节点工厂（生成/校验/执行等）。                                             |
| `models.py`                             | Text2Cypher 内部模型。                                                                 |
| `schema.py`                             | 图 Schema 片段类型。                                                                   |
| `state.py`                              | Text2Cypher 子状态机状态。                                                             |
| `text2sql_tool.py`                      | **子图内 Text2SQL 工具包装**：建 `create_text2sql_workflow`、写回 `cyphers` 统一结构。 |
| `generation/__init__.py`                | 导出生成节点。                                                                         |
| `generation/node.py`                    | LLM 生成 Cypher。                                                                      |
| `generation/prompts.py`                 | 生成阶段提示。                                                                         |
| `validation/__init__.py`                | 导出校验节点。                                                                         |
| `validation/node.py`                    | 调用校验器决定是否重试或执行。                                                         |
| `validation/models.py`                  | 校验结构化输出。                                                                       |
| `validation/prompts.py`                 | LLM 校验提示。                                                                         |
| `validation/validators.py`              | 规则/语法校验实现。                                                                    |
| `validation/utils/__init__.py`          | 校验工具子包。                                                                         |
| `validation/utils/cypher_extractors.py` | 从模型输出抽取 Cypher 文本。                                                           |
| `validation/utils/regex_patterns.py`    | Cypher 相关正则。                                                                      |
| `validation/utils/utils.py`             | 其它校验辅助。                                                                         |
| `correction/__init__.py`                | 导出纠错节点。                                                                         |
| `correction/node.py`                    | 失败时 LLM 修正 Cypher。                                                               |
| `correction/prompts.py`                 | 纠错提示。                                                                             |
| `execution/__init__.py`                 | 导出执行节点。                                                                         |
| `execution/node.py`                     | 在 Neo4j 上执行 Cypher 并收集结果。                                                    |

#### `components/text2sql/`（子图内 Text2SQL 节点实现）

与 `application/agents/text2sql/workflow.py` 使用的工厂一致。

| 文件                           | 概述                                                 |
| ------------------------------ | ---------------------------------------------------- |
| `__init__.py`                  | 导出 `create_schema_retrieval_node` 等全部节点工厂。 |
| `domain_knowledge.py`          | 业务域补充说明，辅助 SQL 生成。                      |
| `schema_retrieval/__init__.py` | 导出 Schema 检索节点。                               |
| `schema_retrieval/node.py`     | 从 MySQL `INFORMATION_SCHEMA` 取相关表结构。         |
| `query_analysis/__init__.py`   | 导出问句分析节点。                                   |
| `query_analysis/node.py`       | LLM 解析用户意图、实体。                             |
| `query_analysis/prompts.py`    | 分析阶段提示。                                       |
| `sql_generation/__init__.py`   | 导出 SQL 生成节点。                                  |
| `sql_generation/node.py`       | LLM 生成 SQL。                                       |
| `sql_generation/prompts.py`    | SQL 生成提示。                                       |
| `sql_validation/__init__.py`   | 导出校验节点。                                       |
| `sql_validation/node.py`       | 校验与重试计数逻辑。                                 |
| `sql_validation/validators.py` | 方言相关语法检查等。                                 |
| `sql_execution/__init__.py`    | 导出执行节点。                                       |
| `sql_execution/node.py`        | 使用 `DATABASE_URL` 执行查询并限制行数。             |
| `visualization/__init__.py`    | 导出可视化建议节点。                                 |
| `visualization/node.py`        | 根据结果建议图表类型/配置。                          |
| `visualization/prompts.py`     | 可视化建议提示。                                     |
| `formatting/__init__.py`       | 导出答案格式化节点。                                 |
| `formatting/node.py`           | 将 SQL 结果整理为自然语言答案。                      |

#### `components/customer_tools/`

| 文件          | 概述                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------ |
| `__init__.py` | 导出 LightRAG 查询节点。                                                                   |
| `node.py`     | **LightRAG**：初始化/查询 `LightRAGAPI`，对应工具 schema 常为 `microsoft_graphrag_query`。 |

#### `components/gather_cypher/`

| 文件          | 概述                                 |
| ------------- | ------------------------------------ |
| `__init__.py` | 导出聚合节点。                       |
| `node.py`     | 合并多子任务返回的 Cypher 结果列表。 |

#### `components/gather_visualizations/`

| 文件          | 概述                  |
| ------------- | --------------------- |
| `__init__.py` | 导出聚合节点。        |
| `node.py`     | 合并可视化建议/配置。 |

#### `components/summarize/`

| 文件          | 概述                               |
| ------------- | ---------------------------------- |
| `__init__.py` | 导出汇总节点。                     |
| `node.py`     | 将多工具子任务结果汇总为中间叙述。 |
| `prompts.py`  | 汇总提示模板。                     |

#### `components/final_answer/`

| 文件          | 概述                         |
| ------------- | ---------------------------- |
| `__init__.py` | 导出终答节点。               |
| `node.py`     | 生成面向用户的最终回复文本。 |

#### `components/validate_final_answer/`

| 文件          | 概述                                        |
| ------------- | ------------------------------------------- |
| `__init__.py` | 导出终答校验节点。                          |
| `node.py`     | 对最终答案做一致性/质量检查（若配置启用）。 |
| `models.py`   | 校验结构化输出。                            |
| `prompts.py`  | 终答校验提示。                              |

#### `components/visualize/`（Cypher 结果可视化链路）

| 文件                           | 概述                   |
| ------------------------------ | ---------------------- |
| `__init__.py`                  | 可视化子包导出。       |
| `state.py`                     | 可视化流水线状态。     |
| `models.py`                    | 图表/细节相关模型。    |
| `schema.py`                    | 可视化 schema 定义。   |
| `generate_chart/__init__.py`   | 图表生成子包导出。     |
| `generate_chart/node.py`       | 生成图表规格或数据。   |
| `generate_chart/charts.py`     | 具体图表类型构造逻辑。 |
| `generate_details/__init__.py` | 细节生成子包导出。     |
| `generate_details/node.py`     | 生成数据细节描述。     |
| `generate_details/models.py`   | 细节结构模型。         |
| `generate_details/prompts.py`  | 细节生成提示。         |
| `validate_details/__init__.py` | 细节校验子包导出。     |
| `validate_details/node.py`     | 校验生成细节是否合法。 |
| `correct_details/__init__.py`  | 细节纠错子包导出。     |
| `correct_details/node.py`      | LLM 修正细节。         |
| `correct_details/prompts.py`   | 纠错提示。             |

#### `components/errors/`

| 文件                         | 概述                                 |
| ---------------------------- | ------------------------------------ |
| `__init__.py`                | 错误处理子包导出。                   |
| `tool_selection/__init__.py` | 工具选择错误节点导出。               |
| `tool_selection/node.py`     | 无法选型时的错误分支与错误信息写入。 |

#### `components/utils/`

| 文件                | 概述               |
| ------------------- | ------------------ |
| `__init__.py`       | 工具子包导出。     |
| `utils.py`          | 组件层通用小函数。 |
| `regex_patterns.py` | 共享正则常量。     |

---

## `infrastructure/`

| 文件          | 概述             |
| ------------- | ---------------- |
| `__init__.py` | 基础设施包导出。 |

### `infrastructure/core/`

| 文件            | 概述                                                            |
| --------------- | --------------------------------------------------------------- |
| `__init__.py`   | 导出 `configure_logging` 等。                                   |
| `logger.py`     | Loguru/日志格式、级别与 `get_logger`。                          |
| `database.py`   | SQLAlchemy `engine`、`Base`、`get_db` 会话工厂；`main` 建表用。 |
| `hashing.py`    | 密码或内容哈希工具。                                            |
| `middleware.py` | FastAPI 中间件（请求日志、计时等，以代码为准）。                |
| `security.py`   | 安全相关工具（令牌、校验等）。                                  |

### `infrastructure/knowledge/`

| 文件                   | 概述                                     |
| ---------------------- | ---------------------------------------- |
| `__init__.py`          | 知识层导出。                             |
| `embeddings.py`        | OpenAI 兼容 Embedding 客户端封装。       |
| `vector_store.py`      | Milvus 等向量存储访问封装。              |
| `reranker.py`          | 重排序 API 调用（如 DashScope rerank）。 |
| `knowledge_service.py` | 检索、融合等业务级知识服务编排。         |
| `recipe_import.py`     | 菜谱数据导入管线入口。                   |

#### `infrastructure/knowledge/recipe_kg/`

| 文件                            | 概述                                  |
| ------------------------------- | ------------------------------------- |
| `__init__.py`                   | 菜谱 KG 子包导出。                    |
| `neo4j_qa_service.py`           | 面向 HTTP 的菜谱 Neo4j 问答服务封装。 |
| `qa_pipeline_orchestrator.py`   | 问句 → 意图 → 查询 → 答案 编排。      |
| `query_parser_service.py`       | 问句解析为结构化查询条件。            |
| `question_intent_classifier.py` | 问题意图分类。                        |
| `answer_search_engine.py`       | 在图/索引上检索候选答案。             |
| `graph_database_client.py`      | Neo4j 读写客户端封装。                |
| `graph_importer_service.py`     | 图谱数据导入服务。                    |
| `graph_cache_loader.py`         | 图或字典缓存加载。                    |
| `recipe_json_parser.py`         | 菜谱 JSON 解析为图数据。              |
| `fuzzy_matcher.py`              | 菜名/实体模糊匹配。                   |

#### `recipe_kg/dicts/`（非 Python，资源文件）

| 文件            | 概述                           |
| --------------- | ------------------------------ |
| `caixi.txt`     | 菜系等类别词表，供解析或匹配。 |
| `deny.txt`      | 否定/排除类词表。              |
| `gongyi.txt`    | 烹饪工艺相关词表。             |
| `haoshi.txt`    | 耗时/时长相关词表。            |
| `kouwei.txt`    | 口味相关词表。                 |
| `leixing.txt`   | 菜品类型词表。                 |
| `material.txt`  | 食材/原料词表。                |
| `recipe.txt`    | 菜名词表或别名。               |
| `yongliang.txt` | 用量单位或用量相关词表。       |

> 具体字段含义以实现代码（如 `query_parser_service`、`fuzzy_matcher`）为准。

### `infrastructure/persistence/`

| 文件          | 概述           |
| ------------- | -------------- |
| `__init__.py` | 持久化包导出。 |

#### `infrastructure/persistence/db/`

| 文件          | 概述          |
| ------------- | ------------- |
| `__init__.py` | DB 子包导出。 |

##### `infrastructure/persistence/db/models/`

| 文件              | 概述                                          |
| ----------------- | --------------------------------------------- |
| `__init__.py`     | ORM 模型聚合导出。                            |
| `user.py`         | 用户表 ORM。                                  |
| `chat_session.py` | 会话表 ORM。                                  |
| `chat_message.py` | 消息表 ORM。                                  |
| `chat_history.py` | 历史表 ORM（若与 message 并存则各管一维）。   |
| `conversation.py` | 会话/对话 ORM（与命名一致的具体字段见模型）。 |
| `message.py`      | 通用消息 ORM。                                |

#### `infrastructure/persistence/crud/`

| 文件                   | 概述             |
| ---------------------- | ---------------- |
| `__init__.py`          | CRUD 导出。      |
| `base.py`              | 通用 CRUD 基类。 |
| `chat_history.py`      | 聊天历史 CRUD。  |
| `conversation.py`      | 对话 CRUD。      |
| `crud_chat_message.py` | 消息 CRUD。      |
| `crud_chat_session.py` | 会话 CRUD。      |

### `infrastructure/tools/`

| 文件             | 概述                                    |
| ---------------- | --------------------------------------- |
| `__init__.py`    | 工具包导出。                            |
| `definitions.py` | 工具常量/Schema 定义（供 Agent 绑定）。 |
| `search.py`      | 联网搜索等外部工具实现。                |

---

## 仓库根：kb_ingest（知识库入库服务）

与 `gustobot/` **并列**于仓库根；自有 `requirements.txt`、`Dockerfile`，**不**随主包 import。

**管线**：Excel / MySQL → 可选 LLM **rewrite** 或 **flatten** → Embedding → **pgvector**。**HTTP**（`kb_service/main.py`）：路由 **`/api`** 与 **`/api/v1/knowledge`**（入库 + `/search` + `/search/hybrid`）。**容器**：`entrypoint.sh` 等 PG 后可后台跑一次 Excel 预热，不挡 `/health`。

**对接主应用**：`gustobot/config/settings.py` 的 **`KB_*`**、**`KB_EXTERNAL_SEARCH_URL`**（常指向本服务基址 + **`/api/v1/knowledge/search`**）；URL 拼接逻辑见 `kg_sub_graph/agentic_rag_agents/workflows/multi_agent/multi_tool.py`。环境变量细节见《环境变量与配置说明.md》。

### `kb_ingest/` 完整目录树（仓库根）

```
kb_ingest/                              # 独立子项目：表格入库 pgvector + 检索 HTTP
├── main.py                             # CLI：dotenv → kb_service.cli.main()
├── entrypoint.sh                       # 容器：等 PostgreSQL；可选后台 Excel 导入；exec uvicorn
├── Dockerfile                          # 镜像构建；CMD 默认 kb_service.main:app
├── requirements.txt                    # 子项目依赖
├── .env.example                        # KB_* / PG / Embedding 等示例
└── kb_service/                         # 业务 Python 包
    ├── __init__.py                     # 包导出
    ├── main.py                         # FastAPI：挂载路由 /api 与 /api/v1/knowledge；/health
    ├── cli.py                          # argparse：process-excel、search、ingest-mysql
    ├── core/                           # 配置
    │   ├── __init__.py
    │   └── config.py                   # 环境变量 → Config（PG、LLM、Embedding、rerank 等）
    ├── api/                            # HTTP
    │   ├── __init__.py
    │   ├── deps.py                     # get_config，Depends 注入
    │   └── routes.py                   # ingest/excel、ingest/excel/upload、ingest/mysql；search、search/hybrid
    ├── schemas/                        # Pydantic 契约
    │   ├── __init__.py
    │   ├── ingest.py                   # Excel / MySQL 入库请求体
    │   └── search.py                   # 向量检索、混合检索请求体
    ├── clients/                        # 外部模型 API
    │   ├── __init__.py
    │   ├── embedding.py                # Embedding 调用
    │   └── llm.py                      # LLM 调用（表格行改写）
    ├── prompts/                        # 提示模板
    │   ├── __init__.py
    │   └── manager.py                  # PromptManager、列 Schema、模板键
    ├── db/                             # 数据库辅助
    │   └── __init__.py                 # 占位说明
    └── services/                       # 管线与存储
        ├── processor.py                # DataProcessor：Excel → 可选改写 → 向量写入
        ├── mysql_ingest.py             # MySQLIngestor：表批取入库
        ├── vector_store.py             # VectorStoreWriter 等与 pgvector 交互
        ├── vector_store_generic.py     # 通用向量访问扩展
        ├── search.py                   # VectorSearcher：相似度、hybrid（+ rerank）
        ├── reranker.py                 # 重排序 HTTP 客户端
        └── utils.py                    # flatten_row 等表格工具
```

### `kb_ingest` 逻辑架构（分层）

| 分层     | 主要路径                          | 职责                                       |
| -------- | --------------------------------- | ------------------------------------------ |
| 交付形态 | `Dockerfile`、`entrypoint.sh`     | 镜像、启动前等库、可选预热导入             |
| 入口     | 根 `main.py`、`kb_service/main.py`、`cli.py` | CLI；FastAPI 应用与 `/health`              |
| 接口层   | `api/routes.py`、`api/deps.py`   | REST 入库与检索；配置注入                  |
| 契约层   | `schemas/`                        | 请求/校验模型                              |
| 领域管线 | `services/processor.py` 等      | Excel/MySQL 处理、写向量、检索、重排       |
| 适配层   | `clients/`、`prompts/manager.py` | LLM / Embedding；改写类提示组装          |
| 配置     | `core/config.py`                  | 环境 → 单例 Config                         |

---

## 请求链路（串联）

`gustobot/main.py` → `interfaces/http/v1/chat.py` → `application/agents/lg_builder.py` →（图谱）`agentic_rag_agents/workflows/multi_agent/multi_tool.py` + `components/*` → `persistence/*`；向量/菜谱 `infrastructure/knowledge/*`；问数 `text2sql/workflow.py`（实现在 `agentic_rag_agents/components/text2sql`）。外部 pgvector 检索可经 **`KB_EXTERNAL_SEARCH_URL`** 指向 **`kb_ingest`**（见上节）。

---

## 延伸阅读

- 《agent开发项目总览.md》
- 《环境变量与配置说明.md》
