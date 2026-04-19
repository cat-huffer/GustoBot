"""菜谱助手主 LangGraph（``StateGraph(AgentState, input=InputState)``）。

``START`` → ``analyze_and_route_query`` → ``route_query`` 条件边分发至一般问答、补充信息、图谱、图片、文件、向量检索等节点；编译为模块级 ``graph``（``MemorySaver``），供 ``astream`` / ``ainvoke``，``thread_id`` 在同进程内持久会话。

主图状态由 ``messages`` 与检查点承载；知识库子图将 ``messages[:-1]`` 编为 history，供子图路由 LLM 读近期对话（与主图同源，非独立向量记忆库）。

依赖 ``lg_prompts``、``lg_states`` 与 Neo4j / Milvus。节点签名为 ``(state, *, config)``，返回 ``AgentState`` 的部分更新。
"""

from gustobot.application.agents.lg_prompts import (
    ROUTER_SYSTEM_PROMPT,
    GET_ADDITIONAL_SYSTEM_PROMPT,
    GENERAL_QUERY_SYSTEM_PROMPT,
    GET_IMAGE_SYSTEM_PROMPT,
    GUARDRAILS_SYSTEM_PROMPT,
    RAGSEARCH_SYSTEM_PROMPT,
    CHECK_HALLUCINATIONS,
    GENERATE_QUERIES_SYSTEM_PROMPT,
    IMAGE_GENERATION_ENHANCE_PROMPT,
    IMAGE_GENERATION_SUCCESS_PROMPT
)
from langchain_core.runnables import RunnableConfig
from gustobot.config import settings
from gustobot.infrastructure.core.logger import get_logger
from typing import cast, Literal, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from gustobot.application.agents.lg_states import AgentState, InputState, Router, GradeHallucinations
from gustobot.application.agents.kg_sub_graph.agentic_rag_agents.retrievers.cypher_examples.recipe_retriever import \
    RecipeCypherRetriever
from gustobot.application.agents.kg_sub_graph.agentic_rag_agents.components.planner.node import create_planner_node
from gustobot.application.agents.kg_sub_graph.agentic_rag_agents.workflows.multi_agent.multi_tool import (
    create_multi_tool_workflow,
    create_kb_multi_tool_workflow,
)
from gustobot.application.agents.kg_sub_graph.kg_neo4j_conn import get_neo4j_graph
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage
from langchain_core.runnables.base import Runnable
from gustobot.application.agents.kg_sub_graph.agentic_rag_agents.components.utils.utils import \
    retrieve_and_parse_schema_from_graph_for_prompts
from langchain_core.prompts import ChatPromptTemplate
import base64
import os
import aiohttp
import asyncio
import json
import time
from pathlib import Path
from PIL import Image
import io

from langchain_openai import ChatOpenAI
from gustobot.application.agents.kb_tools import create_knowledge_query_node, KnowledgeQueryInputState
from gustobot.infrastructure.knowledge import KnowledgeService

class AdditionalGuardrailsOutput(BaseModel):
    """``get_additional_info`` 中护栏链的结构化输出（与 ``GUARDRAILS_SYSTEM_PROMPT`` 配合）。

    ``end``：超出服务范围，返回固定拒绝话术；``proceed``：继续走补充信息提示模板。
    """

    decision: Literal["end", "proceed"] = Field(
        description="问题是否与图谱内容相关：end 表示拒绝，proceed 表示继续。"
    )


# 构建日志记录器
logger = get_logger(service="lg_builder")

# 单下划线开头表示 “internal use”，提醒维护者：别在别的模块里随便依赖它
def _ensure_router(router_obj: Any, *, fallback_question: str = "") -> Router:
    """将路由结果统一为 ``Router`` 实例。

    已为 ``Router`` 则原样返回；``dict`` 则 ``Router.model_validate``；失败时返回默认
    ``kb-query`` 并带上 ``fallback_question``。
    """
    if isinstance(router_obj, Router):
        return router_obj
    if isinstance(router_obj, dict):
        try:
            return Router.model_validate(router_obj)
        except Exception:
            pass
    return Router(type="kb-query", logic="missing router", question=fallback_question)

# TODO 没懂在做什么
def _extract_configurable(config: Any) -> Dict[str, Any]:
    """从 LangGraph 的 RunnableConfig 里取出 ``configurable`` 子字典。

    LangGraph 会把调用方传入的 thread_id、image_path、file_path 等放在
    ``config["configurable"]``（或对象的 ``.configurable``）里；本函数统一
    成普通 ``dict``，避免上层既可能是 dict 又可能是带属性的 config 对象。

    若缺失或类型不对则返回空字典，保证调用方总能 ``.get()``。
    """
    if not config:
        return {}
    if isinstance(config, dict):
        value = config.get("configurable", {})
        return value if isinstance(value, dict) else {}
    # RunnableConfig：优先属性 .configurable
    configurable = getattr(config, "configurable", None)
    if isinstance(configurable, dict):
        return configurable
    # 少数实现像 dict 一样提供 .get
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            value = getter("configurable", {})
            if isinstance(value, dict):
                return value
        except Exception:  # pragma: no cover - 容错
            pass
    return {}

def _coerce_to_bool(value: Any, *, default: bool = False) -> bool:
    """把环境变量、HTTP 查询串等「弱类型」配置转成 bool。

    - ``None``：返回 ``default``（未配置时的默认行为）。
    - 字符串：仅当去掉空白并小写后为 1/true/yes/y/on 时为真（兼容常见 env 写法）。
    - 其他类型：走 Python ``bool(value)``（如数字 0 为假、非 0 为真）。
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)

# 意图识别与路由决策
async def analyze_and_route_query(
        state: AgentState, *, config: RunnableConfig
) -> dict[str, Router]:
    """调用带 ``Router`` 结构化输出的聊天模型，对用户最后一轮消息做意图分类。

    先用 :func:`_heuristic_router` 得到兜底路由；若 LLM 调用失败或返回非法 ``type``，
    则回退到启发式或默认 ``kb-query``。合法类型集合与 ``Router.type`` 的 Literal 一致。
    模型带 ``tags=["router"]``，便于流式输出时过滤。

    Args:
        state: 含 ``messages``；最后一条通常为用户当前问句。
        config: LangGraph 传入的 runnable 配置（本函数体未强依赖，保留签名统一）。

    Returns:
        字典 ``router`` 键对应 ``Router`` 实例，供 ``route_query`` 读取。

    Raises:
        RuntimeError: 未配置 ``OPENAI_API_KEY`` 时。
    """

    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured for router analysis.")

    model = ChatOpenAI(
        openai_api_key=settings.OPENAI_API_KEY, # API 密钥
        model_name=settings.OPENAI_MODEL, # 模型名称
        openai_api_base=settings.OPENAI_API_BASE, # API 根地址
        temperature=0.7, 
        tags=["router"], # 标签
    )

    # 拼接提示模版 + 用户的实时问题（包含历史上下文对话）
    messages = [
                   {"role": "system", "content": ROUTER_SYSTEM_PROMPT}
               ] + state.messages
    logger.info("-----Analyze user query type-----")
    logger.info(f"History messages: {state.messages}")

    # state.messages[-1] 是 LangChain 里的一条消息（例如 HumanMessage、AIMessage 等，类型上属于 AnyMessage）。这些消息对象都有一个 content 字段，表示这条消息的正文
    question_text = state.messages[-1].content if state.messages else ""
    heuristic_router = _heuristic_router(question_text)
    fallback_router: Router = heuristic_router or Router(
        type="kb-query",
        logic="fallback: default to knowledge base routing",
        question=question_text,
    )

    allowed_types: set[str] = {
        "general-query",
        "additional-query",
        "kb-query",
        "graphrag-query",
        "image-query",
        "file-query",
        "text2sql-query",
    }

    try:
        raw_response = await model.with_structured_output(Router).ainvoke(messages)
    except Exception as exc:
        logger.warning("Router LLM failed: %s. Falling back to KB query.", exc)
        return {"router": fallback_router}

    response = raw_response if isinstance(raw_response, Router) else Router.model_validate(raw_response)
    router_type = response.type
    logic = response.logic or ""

    if not router_type or router_type not in allowed_types:
        logger.warning(
            "Router returned invalid type `%s`; applying heuristic fallback.", router_type
        )
        if heuristic_router:
            sanitized = heuristic_router
            if not sanitized.logic:
                sanitized.logic = logic or ""
            return {"router": sanitized}
        return {
            "router": Router(
                type="kb-query",
                logic=logic or "fallback: invalid router output",
                question=question_text,
            )
        }

    sanitized_router = Router(
        type=router_type,
        logic=logic,
        question=response.question or question_text,
        decision=response.decision,
        confidence=response.confidence,
        reasoning=response.reasoning,
    )

    # 大模型输出已通过校验时不再叠加启发式；启发式仅在上文失败分支中使用。
    logger.info(f"Analyze user query type completed, result: {sanitized_router}")
    return {"router": sanitized_router}

def route_query(
        state: AgentState,
) -> Literal["respond_to_general_query", "get_additional_info", "create_research_plan", "create_image_query", "create_file_query", "create_kb_query"]:
    """读取 ``state.router.type``，返回 LangGraph 条件边的下一节点名。

    若 ``state`` 上存在 ``config.configurable`` 且含 ``image_path`` / ``file_path``，
    优先走向图片或文件节点（覆盖纯文本路由结果）。未知类型抛 ``ValueError``。

    Args:
        state: 已写入 ``router`` 的 Agent 状态。

    Returns:
        六个业务节点之一的名字符串，与 ``builder.add_node`` 注册名一致。
    """
    router = _ensure_router(getattr(state, "router", None), fallback_question=state.messages[-1].content if state.messages else "")
    state.router = router
    _type = router.type or "kb-query"

    # 检查配置中是否有图片或文件路径，如果有，优先对应处理
    if hasattr(state, "config") and state.config:
        cfg = state.config.get("configurable", {})
        if cfg.get("image_path"):
            logger.info("检测到图片路径，转为图片查询处理")
            return "create_image_query"
        if cfg.get("file_path"):
            logger.info("检测到文件路径，转为文件上传处理")
            return "create_file_query"

    if _type == "general-query":
        return "respond_to_general_query"
    elif _type == "additional-query":
        return "get_additional_info"
    elif _type in ("graphrag-query", "text2sql-query"):  # 图查询或结构化问数
        return "create_research_plan"
    elif _type == "image-query":
        return "create_image_query"
    elif _type == "file-query":
        return "create_file_query"
    elif _type=="kb-query":
        return "create_kb_query"
    else:
        raise ValueError(f"未知的路由类型：{_type}")

# 一般问答（仅大模型，不调外部工具）
async def respond_to_general_query(
        state: AgentState, *, config: RunnableConfig
) -> Dict[str, List[BaseMessage]]:
    """一般闲聊分支：仅用 ``GENERAL_QUERY_SYSTEM_PROMPT`` + 历史消息调用 LLM，不调工具。

    系统提示中注入 ``router.logic``（路由给出的分类理由）。模型带 ``tags=["general_query"]``。

    Args:
        state: 含 ``messages`` 与 ``router``。
        config: 保留统一签名。

    Returns:
        形如 ``{"messages": [AIMessage(...)]}``，单条助手回复。
    """
    logger.info("-----generate general-query response-----")

    # 使用大模型生成回复
    model = ChatOpenAI(openai_api_key=settings.OPENAI_API_KEY, model_name=settings.OPENAI_MODEL,
                       openai_api_base=settings.OPENAI_API_BASE, temperature=0.7,
                       tags=["general_query"])

    router = _ensure_router(getattr(state, "router", None), fallback_question=state.messages[-1].content if state.messages else "")
    state.router = router
    system_prompt = GENERAL_QUERY_SYSTEM_PROMPT.format(
        logic=router.logic
    )

    messages = [{"role": "system", "content": system_prompt}] + state.messages
    response = await model.ainvoke(messages)
    return {"messages": [response]}

# 大模型在「补充信息」分支下可能产生额外消息，由下游按需处理。
# 先判是否在菜谱业务内，再决定是拒答还是生成追问话术。 它不在这里查知识库、也不跑图谱/知识库工作流，只负责对话策略与话术。
async def get_additional_info(
        state: AgentState, *, config: RunnableConfig
) -> Dict[str, List[BaseMessage]]:
    """「补充信息」分支：先做业务范围护栏，再通过模型生成追问话术。

    第一段链：``GUARDRAILS_SYSTEM_PROMPT`` + 服务范围/Neo4j Schema 上下文，结构化输出
    ``AdditionalGuardrailsOutput``；``decision == "end"`` 时直接返回礼貌拒答 ``AIMessage``。
    否则使用 ``GET_ADDITIONAL_SYSTEM_PROMPT``（注入 ``router.logic``）生成追问。

    Args:
        state: 含 ``messages`` 与 ``router``。
        config: 保留与图节点统一签名；内部主要使用默认 ``ChatOpenAI``。

    Returns:
        形如 ``{"messages": [AIMessage(...)]}``。
    """
    logger.info("------continue to get additional info------")

    model = ChatOpenAI(openai_api_key=settings.OPENAI_API_KEY, model_name=settings.OPENAI_MODEL,
                       openai_api_base=settings.OPENAI_API_BASE, temperature=0.7,
                       tags=["additional_info"])
    # 如果用户的问题是菜谱相关，但与自己的业务无关，则需要返回"无关问题"

    # 首先连接 Neo4j 图数据库
    # 核心目的是让“是否继续回答/是否越界”的判断更贴近当前知识图谱实际结构，而不是只靠固定规则。
    try:
        neo4j_graph = get_neo4j_graph()
        logger.info("success to get Neo4j graph database connection")
    except Exception as e:
        logger.error(f"failed to get Neo4j graph database connection: {e}")
        neo4j_graph = None

    # 定义菜谱助手服务范围（用户友好的业务描述）
    scope_description = """
        菜谱智能助手服务范围：为您提供全方位的烹饪指导和美食知识，包括但不限于：

        🍳 菜谱查询与制作指导
        - 各类中华料理的详细做法和烹饪技巧
        - 食材用量、烹饪时长、火候掌握
        - 分步骤的烹饪指导和小贴士

        🥬 食材知识与营养价值
        - 食材的营养成分和健康功效
        - 食材的选购、储存和处理方法
        - 食材之间的搭配和替代建议

        🌶️ 口味与烹饪技法
        - 各种口味特点（麻辣、酱香、清淡等）
        - 不同烹饪方法（炒、蒸、煮、炖、烤等）
        - 菜品分类（热菜、凉菜、汤品、主食等）

        💊 食疗养生建议
        - 食材的中医食疗功效
        - 季节性饮食调理建议
        - 特定人群的饮食注意事项

        暂不支持：政治、娱乐八卦、新闻时事、天气预报、网购推荐、医疗诊断等非烹饪美食相关内容。
        如遇此类问题，我会礼貌地引导您回到烹饪美食话题～
    """

    scope_context = (
        f"参考此范围描述来决策:\n{scope_description}"
        if scope_description is not None
        else ""
    )

    # 动态从 Neo4j 图表中获取图表结构
    graph_context = (
        f"\n参考图表结构来回答:\n{retrieve_and_parse_schema_from_graph_for_prompts(neo4j_graph)}"
        if neo4j_graph is not None
        else ""
    )

    message = scope_context + graph_context + "\nQuestion: {question}"

    # 拼接提示模版
    full_system_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                GUARDRAILS_SYSTEM_PROMPT,
            ),
            (
                "human",
                (message),
            ),
        ]
    )

    # 构建带结构化输出的链：护栏通过则 proceed，否则 end。
    guardrails_chain = full_system_prompt | model.with_structured_output(AdditionalGuardrailsOutput)
    guardrails_output: AdditionalGuardrailsOutput = await guardrails_chain.ainvoke(
        {"question": state.messages[-1].content if state.messages else ""}
    )

    # 根据格式化输出的结果，返回不同的响应
    if guardrails_output.decision == "end":
        logger.info("-----Fail to pass guardrails check-----")
        return {"messages": [AIMessage(content="厨友您好～抱歉哦，这个问题不太属于我们的菜谱范围呢，我主要帮您解答菜谱和烹饪方面的问题～😊")]}
    else:
        logger.info("-----Pass guardrails check-----")
        # 取出其中的 logic，告诉补问模型「系统认为缺什么信息」，好生成更贴题的追问
        router = _ensure_router(getattr(state, "router", None), fallback_question=state.messages[-1].content if state.messages else "")
        state.router = router
        system_prompt = GET_ADDITIONAL_SYSTEM_PROMPT.format(
            logic=router.logic
        )
        messages = [{"role": "system", "content": system_prompt}] + state.messages
        response = await model.ainvoke(messages)
        return {"messages": [response]}

# 图片生成
async def _generate_image(user_query: str, state: AgentState) -> Dict[str, List[BaseMessage]]:
    """根据用户描述生成菜谱相关图片（文生图）。

    流程：
        1. 使用 ``settings.LLM_*`` 构造 ``ChatOpenAI``，按 ``IMAGE_GENERATION_ENHANCE_PROMPT``
           将 ``user_query`` 扩写为更适合作画的提示词。
        2. 向 ``IMAGE_GENERATION_BASE_URL`` 下的 ``/images/generations`` 发送 JSON POST（OpenAI/CogView
           兼容：``model``、``prompt``、``size``），需配置 ``IMAGE_GENERATION_API_KEY``。
        3. 从响应 ``data[0].url`` 取图片地址；成功文案由 ``IMAGE_GENERATION_SUCCESS_PROMPT`` 格式化，
           ``dish_name`` 由 ``user_query`` 与内置菜名关键词列表简单匹配，未命中则默认为「菜品」。

    失败与异常：
        未配置 API Key、HTTP 非 200、响应无 ``data`` 或无 ``url``、请求超时（``asyncio.TimeoutError``）
        或其它异常时，均返回单条 ``AIMessage`` 说明原因，不向上抛出。

    Args:
        user_query: 用户输入的自然语言（期望与菜品/画面相关）。
        state: 当前 Agent 状态；本函数体内未读取，保留参数供调用方与后续扩展一致。

    Returns:
        ``{"messages": [AIMessage(content=...)]}``；成功时 ``content`` 含成功话术与「图片链接: …」行。
    """
    try:
        # 步骤 1：使用大模型优化用户提示词
        model = ChatOpenAI(
            model=settings.LLM_MODEL,
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            temperature=0.7
        )

        enhance_prompt = IMAGE_GENERATION_ENHANCE_PROMPT.format(user_query=user_query)
        enhance_messages = [{"role": "user", "content": enhance_prompt}]

        logger.info(f"Enhancing user prompt: {user_query}")
        enhanced_response = await model.ainvoke(enhance_messages)
        enhanced_prompt = enhanced_response.content.strip()
        logger.info(f"Enhanced prompt: {enhanced_prompt}")

        # 步骤 2：调用 CogView-4 API 生成图片
        api_key = settings.IMAGE_GENERATION_API_KEY
        base_url = settings.IMAGE_GENERATION_BASE_URL
        model_name = settings.IMAGE_GENERATION_MODEL
        size = settings.IMAGE_GENERATION_SIZE

        if not api_key:
            logger.error("IMAGE_GENERATION_API_KEY not configured")
            return {"messages": [AIMessage(content="抱歉，图片生成服务配置不完整，无法生成图片。")]}

        # 构建 API 请求
        api_url = f"{base_url}/images/generations"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model_name,
            "prompt": enhanced_prompt,
            "size": size
        }

        logger.info(f"Calling CogView-4 API: {api_url}")
        logger.info(f"Payload: model={model_name}, size={size}")

        # 异步 HTTP 请求
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers, timeout=60) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"CogView-4 API error: {resp.status} - {error_text}")
                    return {"messages": [AIMessage(content="抱歉，图片生成失败，请稍后再试。")]}

                result = await resp.json()
                logger.info(f"CogView-4 API response: {json.dumps(result, ensure_ascii=False)}")

        # 步骤 3：解析响应，获取图片 URL
        if "data" not in result or len(result["data"]) == 0:
            logger.error(f"CogView-4 API returned no image data: {result}")
            return {"messages": [AIMessage(content="抱歉，图片生成失败，请稍后再试。")]}

        image_url = result["data"][0].get("url", "")
        if not image_url:
            logger.error(f"CogView-4 API returned no image URL: {result}")
            return {"messages": [AIMessage(content="抱歉，图片生成失败，请稍后再试。")]}

        logger.info(f"Image generated successfully: {image_url}")

        # 步骤 4：从查询中提取菜名（简单关键词匹配）
        dish_name = "菜品"
        for keyword in ["宫保鸡丁", "红烧肉", "麻婆豆腐", "糖醋排骨", "鱼香肉丝"]:
            if keyword in user_query:
                dish_name = keyword
                break

        # 步骤 5：格式化成功回复文案
        success_message = IMAGE_GENERATION_SUCCESS_PROMPT.format(dish_name=dish_name)
        response_content = f"{success_message}\n\n图片链接: {image_url}"

        return {"messages": [AIMessage(content=response_content)]}

    except asyncio.TimeoutError:
        logger.error("CogView-4 API timeout")
        return {"messages": [AIMessage(content="抱歉，图片生成超时，请稍后再试。")]}
    except Exception as e:
        logger.error(f"Error generating image: {e}", exc_info=True)
        return {"messages": [AIMessage(content=f"抱歉，图片生成过程中出现错误：{str(e)}")]}

# 处理图片相关查询：支持文生图、上传图识别，并生成助手回复。
async def create_image_query(
        state: AgentState, *, config: RunnableConfig
) -> Dict[str, List[BaseMessage]]:
    """处理图片相关请求：文生图或上传图的视觉理解。

    分支判定（按代码顺序）：
        1. **文生图**：用户最后一条消息含生成类关键词（如「生成」「画」「创建图片」「做一张」
           等），且 **未** 提供 ``image_path`` 时，调用 :func:`_generate_image`（LLM 扩写提示词
           + CogView 兼容 HTTP）。不依赖本地上传文件。
        2. **上传图理解**：需要 ``config["configurable"]["image_path"]`` 指向**已存在**的本地
           文件。缺失或路径无效时返回提示重新上传。依赖 ``VISION_API_KEY``、``VISION_BASE_URL``、
           ``VISION_MODEL``：用 Pillow 将图缩放到最长边不超过 1024、转 JPEG Base64，POST
           ``{VISION_BASE_URL}/chat/completions`` 多模态消息获取图片描述，再将描述注入
           ``GET_IMAGE_SYSTEM_PROMPT``，用 ``OPENAI_*`` 配置的 ``ChatOpenAI`` 结合
           ``state.messages`` 生成最终回复。

    若用户既未触发文生图关键词、又未提供有效 ``image_path``，会提示无法查看图片。

    Args:
        state: Agent 状态；取 ``messages[-1].content`` 作为用户指令，视觉成功分支会将完整
            ``state.messages`` 交给文本模型。
        config: RunnableConfig；从 ``configurable.image_path`` 读取上传图片的本地路径（若存在）。

    Returns:
        ``{"messages": [AIMessage(...)]}``。视觉或文生图失败、异常、配置不全时多为简短错误话术。
    """
    logger.info("-----Handle Image Query-----")
    image_path = config.get("configurable", {}).get("image_path", None)
    user_query = state.messages[-1].content if state.messages else ""

    # 判断是图像识别还是图像生成
    generation_keywords = ["生成", "画", "创建", "制作图片", "做一张", "给我一张", "来一张"]
    is_generation = any(keyword in user_query for keyword in generation_keywords)

    # 情况 1：用户要求生成图片（未上传图片或明确要生成）
    if is_generation and not image_path:
        logger.info(f"Image Generation Request: {user_query}")
        return await _generate_image(user_query, state)

    # 情况 2：用户已上传图片，走视觉识别
    if not image_path:
        logger.warning(f"User Upload Image Path is None for recognition")
        return {"messages": [AIMessage(content="抱歉，我无法查看这张图片，请重新上传。")]}

    if not Path(image_path).exists():
        logger.warning(f"User Upload Image Not Found: {image_path}")
        return {"messages": [AIMessage(content="抱歉，我无法查看这张图片，请重新上传。")]}

    # 获取视觉模型配置
    api_key = settings.VISION_API_KEY
    base_url = settings.VISION_BASE_URL
    vision_model = settings.VISION_MODEL

    if not api_key or not base_url or not vision_model:
        logger.error("Vision Model Configuration Not Complete")
        return {"messages": [AIMessage(content="抱歉，我无法查看这张图片，请重新上传。")]}

    logger.info(f"Using Vision Model: {vision_model} to process image: {image_path}")

    try:
        # 读取并压缩图片
        with Image.open(image_path) as img:
            # 设置最大尺寸
            max_size = 1024
            # 计算缩放比例
            width, height = img.size
            ratio = min(max_size / width, max_size / height)

            # 如果图片尺寸已经小于最大尺寸，不需要缩放
            if width <= max_size and height <= max_size:
                resized_img = img
            else:
                new_width = int(width * ratio)
                new_height = int(height * ratio)
                resized_img = img.resize((new_width, new_height), Image.LANCZOS)

            # 转为 JPEG 并控制压缩质量
            img_byte_arr = io.BytesIO()
            if resized_img.mode != 'RGB':
                resized_img = resized_img.convert('RGB')
            resized_img.save(img_byte_arr, format='JPEG', quality=85)
            img_byte_arr.seek(0)

            # 转为 Base64 供视觉 API 使用
            image_data = base64.b64encode(img_byte_arr.read()).decode('utf-8')

            logger.info(
                f"Image Compressed, Original Size: {width}x{height}, New Size: {resized_img.width}x{resized_img.height}")

        # 构建视觉 API 请求
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        payload = {
            "model": vision_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个专业的菜谱图像分析助手。请详细分析图片中的内容，特别关注菜品名称、食材、烹饪方法、摆盘等细节。"
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 4000,
            "temperature": 0.7
        }

        # 调用视觉接口
        async with aiohttp.ClientSession() as session:
            async with session.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=60  # 视觉推理较慢，适当延长超时
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    image_description = result["choices"][0]["message"]["content"]
                    logger.info(f"Successfully processed image and generated description")
                    # 结合图片描述与用户问题，用 lg_prompts 中的模板生成最终回复

                    # 第二次调用：文本大模型整理回答
                    model = ChatOpenAI(openai_api_key=settings.OPENAI_API_KEY, model_name=settings.OPENAI_MODEL,
                                       openai_api_base=settings.OPENAI_API_BASE, temperature=0.7,
                                       tags=["image_query"])
                    # 使用专门的图片查询提示模板
                    system_prompt = GET_IMAGE_SYSTEM_PROMPT.format(
                        image_description=image_description
                    )
                    messages = [{"role": "system", "content": system_prompt}] + state.messages
                    response = await model.ainvoke(messages)
                    return {"messages": [response]}

                else:
                    error_text = await response.text()
                    logger.error(f"Vision API Request Failed: {response.status} - {error_text}")
                    return {"messages": [AIMessage(content=f"抱歉，我无法查看这张图片，请重新上传。")]}

    except Exception as e:
        logger.error(f"Error processing image: {str(e)}")
        return {"messages": [AIMessage(content=f"抱歉，我无法查看这张图片，请重新上传。")]}

# 处理文件相关查询：支持Excel导入、文本类文件写入知识库，并可追问。
async def create_file_query(
        state: AgentState, *, config: RunnableConfig
) -> Dict[str, List[BaseMessage]]:
    """文件分支：Excel/xls 调用 ``INGEST_SERVICE_URL`` 外部导入；文本类读入后 ``KnowledgeService.add_document``。

    支持后缀与大小受 ``settings`` 约束；纯文本入库后可再调 ``create_knowledge_query_node``
    用用户原问句做一次检索回答。``configurable`` 中 ``incremental`` / ``regenerate`` 仅对
    Excel 分支生效（经 :func:`_coerce_to_bool`）。

    Args:
        state: 用户消息与对话历史。
        config: 使用 :func:`_extract_configurable` 取 ``file_path`` 等。

    Returns:
        形如 ``{"messages": [AIMessage(...)]}``。
    """

    logger.info("-----Found User Upload File-----")
    config_opts = _extract_configurable(config)
    file_path = config_opts.get("file_path")
    ingest_service_url = settings.INGEST_SERVICE_URL

    service = KnowledgeService()

    if not file_path:
        logger.warning("User Upload File Path is None")
        return {"messages": [AIMessage(content="请提供要处理的文件路径。")]}

    p = Path(file_path)
    if not p.exists() or not p.is_file():
        logger.warning("User Upload File Not Found: %s", file_path)
        return {"messages": [AIMessage(content="抱歉，未找到该文件，请确认路径是否正确。")]}

    try:
        suffix = p.suffix.lower()
        size_bytes = p.stat().st_size
        if size_bytes > settings.FILE_UPLOAD_MAX_MB * 1024 * 1024:
            return {"messages": [AIMessage(content=f"文件过大（>{settings.FILE_UPLOAD_MAX_MB}MB），请分割后重新上传。")]}

        if suffix in {".xlsx", ".xls"}:
            # Excel 必须由外部接入服务处理
            if not ingest_service_url:
                return {"messages": [AIMessage(content="未配置外部接入服务 INGEST_SERVICE_URL，无法处理 Excel 导入。")]}
            incremental_flag = _coerce_to_bool(
                config_opts.get("incremental"),
                default=settings.INGEST_INCREMENTAL_DEFAULT,
            )
            regenerate_flag = _coerce_to_bool(config_opts.get("regenerate"), default=False)
            payload = {
                "excel_path": str(p),
                "incremental": incremental_flag,
                "regenerate": regenerate_flag,
            }
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{ingest_service_url.rstrip('/')}/api/ingest/excel",
                    json=payload,
                    timeout=60,
                ) as resp:
                    if resp.status not in (200, 202):
                        txt = await resp.text()
                        return {"messages": [AIMessage(content=f"外部 Excel 导入请求失败（{resp.status}）：{txt}")]}
            return {"messages": [AIMessage(content="已启动 Excel 导入（外部服务），完成后可直接检索或提问。")]}

        raw_text = ""
        if suffix in {".txt", ".md", ".csv", ".log"}:
            raw_text = p.read_text(encoding="utf-8", errors="ignore")
        elif suffix == ".json":
            import json
            data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
            raw_text = json.dumps(data, ensure_ascii=False, indent=2)
        else:
            return {"messages": [AIMessage(content=f"暂不支持该文件类型：{suffix}。当前仅支持 .txt/.md/.json/.csv/.log/.xlsx/.xls。")]}

        import uuid
        doc_id = f"upload_{p.stem}_{uuid.uuid4().hex[:6]}"
        title = p.stem
        # 写进知识库
        success = await service.add_document(
            doc_id=doc_id,
            title=title,
            content=raw_text,
            metadata={"category": "uploaded"},
        )
        if not success:
            return {"messages": [AIMessage(content="文件已读取，但保存到知识库失败，请稍后重试。")]}
            
        # 查询知识库
        knowledge_node = create_knowledge_query_node(knowledge_service=service)
        user_question = state.messages[-1].content if state.messages else title
        input_state: KnowledgeQueryInputState = {
            "task": user_question,
            "context": {"top_k": 5},
            "steps": ["file_upload"],
        }
        kb_result = await knowledge_node(input_state)
        answer_text = kb_result.get("answer") or f"文件《{title}》已上传并加入知识库，可直接对我提问相关内容。"
        return {"messages": [AIMessage(content=answer_text)]}
    except Exception as exc:
        logger.exception("Failed to ingest uploaded file: %s", exc)
        return {"messages": [AIMessage(content="文件导入出现异常，请稍后再试或联系管理员。")]}

# 处理知识库查询：通过多工具工作流查询向量知识库（可选外部检索 API）；失败时回退为直连检索节点。
async def create_kb_query(
        state: AgentState, *, config: RunnableConfig
) -> Dict[str, List[BaseMessage]]:
    """向量知识库：优先 ``create_kb_multi_tool_workflow``（LLM 选工具 + Milvus + 可选外部搜索）。

    ``configurable`` 可覆盖 ``kb_top_k``、``kb_similarity_threshold``、``kb_filter_expr``。
    工作流异常时记录警告并回退到 ``create_knowledge_query_node`` 单次检索。成功时
    ``AIMessage.additional_kwargs["sources"]`` 附带引用列表。

    Args:
        state: 用最后一条用户消息作为检索问句。
        config: RunnableConfig，从中抽取 ``configurable``。

    Returns:
        含 ``messages``，成功时可能另含 ``sources`` 状态字段。
    """
    logger.info("------execute KB multi-tool query------")

    last_message = state.messages[-1].content if state.messages else ""
    if not last_message.strip():
        return {"messages": [AIMessage(content="请告诉我具体的问题，我才能帮您查询知识库。")]}

    config_opts = _extract_configurable(config)
    kb_top_k = config_opts.get("kb_top_k") or settings.KB_TOP_K
    kb_similarity_threshold = (
        config_opts.get("kb_similarity_threshold")
        if config_opts.get("kb_similarity_threshold") is not None
        else settings.KB_SIMILARITY_THRESHOLD
    )
    kb_filter_expr = config_opts.get("kb_filter_expr")

    knowledge_service: Optional[KnowledgeService] = None
    try:
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not configured for KB multi-tool workflow.")

        llm = ChatOpenAI(
            openai_api_key=settings.OPENAI_API_KEY,
            model_name=settings.OPENAI_MODEL,
            openai_api_base=settings.OPENAI_API_BASE,
            temperature=0.3,
            tags=["kb_multi_tool"],
        )

        knowledge_service = KnowledgeService()

        external_url = settings.KB_EXTERNAL_SEARCH_URL
        if not external_url and settings.INGEST_SERVICE_URL:
            external_url = f"{settings.INGEST_SERVICE_URL.rstrip('/')}/api/search"

        # 护栏 + 路由 + ingest 上的 PostgreSQL + Milvus + 可选外部 HTTP + 最终 LLM 合成回答
        workflow = create_kb_multi_tool_workflow(
            llm=llm,
            knowledge_service=knowledge_service,
            top_k=kb_top_k,
            similarity_threshold=kb_similarity_threshold,
            filter_expr=kb_filter_expr,
            allow_external=settings.KB_ENABLE_EXTERNAL_SEARCH,
            external_search_url=external_url,
        )

        history_payload = [
            {
                "role": getattr(msg, "type", "user"),
                "content": getattr(msg, "content", ""),
            }
            for msg in state.messages[:-1]
            if getattr(msg, "content", "").strip()
        ]

        response = await workflow.ainvoke(
            {
                "question": last_message,
                "history": history_payload,
            }
        )
        answer_text = response.get("answer") or "检索完成，但暂时没有可以分享的结果。"
        sources = response.get("sources", [])

        # 构造 AIMessage，并在 additional_kwargs 中附带 sources 供接口层读取
        ai_message = AIMessage(content=answer_text)
        ai_message.additional_kwargs["sources"] = sources

        return {"messages": [ai_message], "sources": sources}
    except Exception as exc:
        logger.warning("KB multi-tool workflow unavailable (%s); falling back to direct search.", exc)

    # 回退路径：不经过子工作流，直接调用知识查询节点
    if knowledge_service is None:
        knowledge_service = KnowledgeService()
    knowledge_node = create_knowledge_query_node(knowledge_service=knowledge_service)
    input_state: KnowledgeQueryInputState = {
        "task": last_message,
        "context": {
            "top_k": kb_top_k,
            "similarity_threshold": kb_similarity_threshold,
            "filter_expr": kb_filter_expr,
        },
        "steps": ["kb_query"],
    }
    result = await knowledge_node(input_state)
    answer_text = result.get("answer", "") or "抱歉，我暂时无法从知识库中找到答案。"
    return {"messages": [AIMessage(content=answer_text)]}

# 图谱多工具子图入口（GraphRAG / Cypher / Text2SQL 等）
async def create_research_plan(
        state: AgentState, *, config: RunnableConfig
) -> Dict[str, List[str] | str]:
    """主图「图谱 / 结构化问数」：构建并执行 ``create_multi_tool_workflow``（图查询、GraphRAG、Text2SQL 等）。

    末条用户消息 → ``question``；``route_type`` 由 ``_ensure_router(getattr(state, "router", None))`` 得到；
    ``data`` / ``history`` 为空。Neo4j 连不上时 ``graph`` 为 ``None`` 并记日志。
    """
    logger.info("------execute local knowledge base query------")

    # 使用大模型生成查询/多跳、并行查询计划
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured for research plan generation.")

    model = ChatOpenAI(
        openai_api_key=settings.OPENAI_API_KEY,
        openai_api_base=settings.OPENAI_API_BASE,
        model_name=settings.OPENAI_MODEL,
        temperature=0.7,
        tags=["research_plan"],
    )

    # 初始化 Neo4j 连接（连接信息来自配置）
    neo4j_graph=None
    try:
        neo4j_graph = get_neo4j_graph()
        logger.info("success to get Neo4j graph database connection")
    except Exception as e:
        logger.error(f"failed to get Neo4j graph database connection: {e}")

    # 菜谱场景 Cypher Few-shot 检索器：据图 Schema 拉取示例，引导模型生成合法 Cypher
    cypher_retriever = RecipeCypherRetriever()

    # 子图中可选工具的结构化模式（供工具选择节点使用）
    from gustobot.application.agents.kg_sub_graph.kg_tools_list import (
        cypher_query,
        predefined_cypher,
        microsoft_graphrag_query,
        text2sql_query,
    )
    tool_schemas: List[type[BaseModel]] = [
        cypher_query,
        predefined_cypher,
        microsoft_graphrag_query,
        text2sql_query,
    ]

    # 预定义 Cypher 模板（高频问法快速命中）
    from gustobot.application.agents.kg_sub_graph.agentic_rag_agents.components.predefined_cypher.cypher_dict import \
        predefined_cypher_dict

    # 定义菜谱助手服务范围
    scope_description = """
        菜谱智能助手服务范围：为您提供全方位的烹饪指导和美食知识，包括但不限于：

        🍳 菜谱查询与制作指导
        - 各类中华料理的详细做法和烹饪技巧
        - 食材用量、烹饪时长、火候掌握
        - 分步骤的烹饪指导和小贴士

        🥬 食材知识与营养价值
        - 食材的营养成分和健康功效
        - 食材的选购、储存和处理方法
        - 食材之间的搭配和替代建议

        🌶️ 口味与烹饪技法
        - 各种口味特点（麻辣、酱香、清淡等）
        - 不同烹饪方法（炒、蒸、煮、炖、烤等）
        - 菜品分类（热菜、凉菜、汤品、主食等）

        💊 食疗养生建议
        - 食材的中医食疗功效
        - 季节性饮食调理建议
        - 特定人群的饮食注意事项

        暂不支持：政治、娱乐八卦、新闻时事、天气预报、网购推荐、医疗诊断等非烹饪美食相关内容。
    """

    # 创建多工具工作流
    multi_tool_workflow = create_multi_tool_workflow(
        llm=model,
        graph=neo4j_graph,
        tool_schemas=tool_schemas,
        predefined_cypher_dict=predefined_cypher_dict,
        cypher_example_retriever=cypher_retriever,
        scope_description=scope_description,
        llm_cypher_validation=True,
    )

    # 调试需要时可改为：return multi_tool_workflow
    # 组装子图输入
    last_message = state.messages[-1].content if state.messages else ""
    input_state = {
        "question": last_message,
        "data": [],
        "history": [],
        "route_type": _ensure_router(getattr(state, "router", None)).type,
    }

    # 执行工作流
    response = await multi_tool_workflow.ainvoke(input_state)
    return {"messages": [AIMessage(content=response["answer"])]}

# 检查是否存在幻觉：使用大模型对「用户问题 + 已生成回复 + 文档依据」做一致性打分。
async def check_hallucinations(
        state: AgentState, *, config: RunnableConfig
) -> dict[str, Any]:
    """用 ``CHECK_HALLUCINATIONS`` 模板将 ``state.documents`` 与最后一条生成对比，结构化输出评分。

    返回 ``GradeHallucinations``，其中 ``binary_score`` 为 ``'1'`` 表示未见明显幻觉、
    ``'0'`` 表示可能编造。注意：模板中 ``generation=state.messages[-1]`` 会把消息对象
    代入，依赖模型的字符串化行为。

    Args:
        state: 需已有检索结果 ``documents`` 与待审的助手消息。
        config: 保留统一签名。

    Returns:
        形如 ``{"hallucination": GradeHallucinations(...)}``。

    Raises:
        RuntimeError: 未配置 ``OPENAI_API_KEY`` 时。
    """
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured for hallucination checks.")

    model = ChatOpenAI(
        openai_api_key=settings.OPENAI_API_KEY,
        openai_api_base=settings.OPENAI_API_BASE,
        model_name=settings.OPENAI_MODEL,
        temperature=0.7,
        tags=["hallucinations"],
    )

    system_prompt = CHECK_HALLUCINATIONS.format(
        documents=state.documents,
        generation=state.messages[-1]
    )

    messages = [
                   {"role": "system", "content": system_prompt}
               ] + state.messages

    logger.info("---CHECK HALLUCINATIONS---")

    response = cast(GradeHallucinations, await model.with_structured_output(GradeHallucinations).ainvoke(messages))

    return {"hallucination": response}


# MemorySaver：进程内内存检查点，按 thread_id 保存状态（重启即丢失）
checkpointer = MemorySaver()

# 主状态图：入口为意图识别，经条件边分发到各业务节点
builder = StateGraph(AgentState, input=InputState)
# 注册节点
# 只传函数一个参数时，LangGraph 会用函数的 __name__ 作为节点名
builder.add_node(analyze_and_route_query)  # 意图识别与路由决策
builder.add_node(respond_to_general_query)  # 一般问答（仅大模型，不调外部工具）
builder.add_node(get_additional_info)  # 补充信息 / 护栏与引导话术
builder.add_node("create_research_plan", create_research_plan)  # 图谱多工具（GraphRAG、Cypher、Text2SQL 等）
builder.add_node(create_image_query)  # 文生图或视觉识别
builder.add_node(create_file_query)  # 文件接入与知识库写入
builder.add_node(create_kb_query)  # 向量知识库多工具工作流


# 边：START → 路由节点 → 条件边按 route_query 结果跳转
builder.add_edge(START, "analyze_and_route_query")
builder.add_conditional_edges("analyze_and_route_query", route_query)

# 编译后的可执行图；与 checkpointer 组合后支持按 thread_id 恢复状态
graph = builder.compile(checkpointer=checkpointer)

# 以下为可选调试：将 LangGraph 导出为 PNG 或在 Notebook 中展示（需 graphviz / IPython 等依赖）
# png_bytes = graph.get_graph().draw_mermaid_png()
# output_path = Path(__file__).resolve().parent / "lg_builder_workflow.png"
# output_path.write_bytes(png_bytes)
# logger.info("工作流图已保存到 %s", output_path)
#
# try:
#     from IPython.display import Image as IPythonImage, display as ipython_display
# except ImportError:  # pragma: no cover - 可选依赖
#     logger.info("IPython 未安装，跳过图像内联展示。")
# else:
#     ipython_display(IPythonImage(png_bytes))

# 基于关键词的兜底路由（小写匹配）
def _heuristic_router(question: str) -> Optional[Router]:
    """基于关键词的兜底路由（小写匹配）。

    含「统计/多少/总数」等优先 ``text2sql-query``；含「怎么做/步骤/食材」等优先
    ``graphrag-query``；否则返回 ``None``，由调用方再用默认 ``kb-query``。
    """
    if not question:
        return None

    lowered = question.lower()

    graphrag_keywords = [
        "怎么做",
        "如何做",
        "做法",
        "步骤",
        "火候",
        "食材",
        "原料",
        "需要什么",
        "配料",
        "用什么",
    ]

    text2sql_keywords = ["统计", "多少", "总数", "数量", "排名"]

    if any(keyword in lowered for keyword in text2sql_keywords):
        return Router(
            type="text2sql-query",
            logic="keyword fallback: text2sql",
            question=question,
        )

    if any(keyword in lowered for keyword in graphrag_keywords):
        return Router(
            type="graphrag-query",
            logic="keyword fallback: graphrag",
            question=question,
        )

    return None


def build_supervisor_graph():
    """向后兼容：返回模块级已 ``compile`` 的 ``graph``（旧称 Supervisor Graph）。"""
    return graph

# 历史兼容入口：直接 ``await get_additional_info(...)``，行为与补充信息分支一致。
async def safety_guardrails(
    state: AgentState, *, config: RunnableConfig
) -> Dict[str, List[BaseMessage]]:
    """历史兼容入口：直接 ``await get_additional_info(...)``，行为与补充信息分支一致。"""
    return await get_additional_info(state, config=config)