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
    """结构化输出：判断用户问题是否与图谱内容相关。"""

    decision: Literal["end", "proceed"] = Field(
        description="问题是否与图谱内容相关：end 表示拒绝，proceed 表示继续。"
    )


# 构建日志记录器
logger = get_logger(service="lg_builder")

# 单下划线开头表示 “internal use”，提醒维护者：别在别的模块里随便依赖它
def _ensure_router(router_obj: Any, *, fallback_question: str = "") -> Router:
    """将任意 router 结构转换为 Router 模型，保持字段访问兼容。"""
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
    """分析用户查询并确定路由分支。

    使用大模型对用户问题进行分类，决定在对话流中的下一步走向。

    参数:
        state: 当前 Agent 状态，含对话历史。
        config: 运行配置（含用于路由分析的模型等）。

    返回:
        包含 ``router`` 键的字典，值为分类结果（类型与逻辑说明等）。
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

# 返回值是节点名字符串
def route_query(
        state: AgentState,
) -> Literal["respond_to_general_query", "get_additional_info", "create_research_plan", "create_image_query", "create_file_query", "create_kb_query"]:
    """根据查询分类确定下一步操作。

    参数:
        state: 当前 Agent 状态，含路由器分类结果。

    返回:
        下一个节点名称：respond_to_general_query / get_additional_info / create_research_plan /
        create_image_query / create_file_query / create_kb_query。
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
    """生成对一般查询的响应，完全基于大模型，不会触发任何外部服务的调用，包括自定义工具、知识库查询等。
    当路由器将查询分类为一般问题时，将调用此节点。
    参数:
        state: 当前 Agent 状态，含对话历史与路由逻辑。
        config: 用于配置生成回复所用模型。
    返回:
        包含 ``messages`` 键的字典，值为生成的消息列表。
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
async def get_additional_info(
        state: AgentState, *, config: RunnableConfig
) -> Dict[str, List[BaseMessage]]:
    """生成一个响应，要求用户提供更多信息。

    当路由确定需要从用户那里获取更多信息时，将调用此函数。

    参数:
        state: 当前 Agent 状态，含对话历史与路由逻辑。
        config: 用于配置生成回复所用模型。

    返回:
        包含 ``messages`` 键的字典，值为生成的消息列表。
    """
    logger.info("------continue to get additional info------")

    # 使用大模型生成回复
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

    # 空值保护：大模型若返回 None，则视为 proceed
    if guardrails_output is None:
        logger.warning("Guardrails returned None, defaulting to proceed")
        guardrails_output = AdditionalGuardrailsOutput(decision="proceed")

    # 根据格式化输出的结果，返回不同的响应
    if guardrails_output.decision == "end":
        logger.info("-----Fail to pass guardrails check-----")
        return {"messages": [AIMessage(content="厨友您好～抱歉哦，这个问题不太属于我们的菜谱范围呢，我主要帮您解答菜谱和烹饪方面的问题～😊")]}
    else:
        logger.info("-----Pass guardrails check-----")
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
    """使用 CogView-4 API 根据用户描述生成图片。

    参数:
        user_query: 用户的图片生成请求文本。
        state: 当前 Agent 状态。

    返回:
        包含 ``messages`` 键的字典，内容为带图片链接等信息的助手回复。
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
    """处理图片相关查询：支持文生图、上传图识别，并生成助手回复。

    参数:
        state: 当前 Agent 状态，含对话历史。
        config: 运行配置（如 configurable 中的 image_path、线程 ID 等）。

    返回:
        包含 ``messages`` 键的字典，值为生成的消息列表。
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
    """处理用户上传文件：Excel 走外部接入服务，文本类写入知识库并可追问。"""

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
        success = await service.add_document(
            doc_id=doc_id,
            title=title,
            content=raw_text,
            metadata={"category": "uploaded"},
        )
        if not success:
            return {"messages": [AIMessage(content="文件已读取，但保存到知识库失败，请稍后重试。")]}

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
    """通过多工具工作流查询向量知识库（可选外部检索 API）；失败时回退为直连检索节点。"""
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
    """基于本地图与结构化数据回答用户：组装多工具工作流并执行（含任务分解与工具编排）。

    参数:
        state: 当前 Agent 状态，含对话历史。
        config: 运行配置（如所用模型等）。

    返回:
        包含助手 ``messages`` 的字典（最终答案在 AIMessage 正文中）。
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
    """根据检索到的文档事实，判断当前生成内容是否与之相符，输出二分类评分。

    使用大模型对「用户问题 + 已生成回复 + 文档依据」做一致性打分。

    参数:
        state: 当前 Agent 状态，需包含 ``documents`` 与最新 ``messages``。
        config: 运行配置（所用判定模型等）。

    返回:
        包含 ``hallucination`` 键的字典，值为 ``GradeHallucinations`` 结构化结果。
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
def _heuristic_router(question: str) -> Optional[Router]:
    """基于关键词的简单启发式路由，作为大模型路由失败时的兜底。"""
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
    """向后兼容接口：返回已编译的主 Agent 状态图（旧称 Supervisor Graph）。"""
    return graph


async def safety_guardrails(
    state: AgentState, *, config: RunnableConfig
) -> Dict[str, List[BaseMessage]]:
    """兼容旧接口，复用 get_additional_info 的护栏逻辑。"""
    return await get_additional_info(state, config=config)