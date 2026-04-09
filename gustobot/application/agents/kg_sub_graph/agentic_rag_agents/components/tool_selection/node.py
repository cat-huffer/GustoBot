"""
工具选择节点：为当前子任务决定用哪一个下游工具。

流程概览：
1. 规则捷径——含「口味/营养/功效」等描述向关键词时优先走 GraphRAG（customer_tools）；
   问句像 SQL 且路由为 text2sql-query 时直接走 Text2SQL。
2. 否则用「提示词 + LLM.bind_tools + PydanticToolsParser(first_tool_only)」
   在 tool_schemas（如 cypher_query、predefined_cypher、microsoft_graphrag_query、text2sql_query）中选一个。
3. LLM 未选出工具时，默认回退到 cypher_query（Text2Cypher）。

输出为 LangGraph 的 Command，通过 Send 跳转到对应工具节点。
"""

from typing import Any, Callable, Coroutine, Dict, List, Literal, Set
import re

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import PydanticToolsParser
from langchain_core.runnables.base import Runnable
from langgraph.types import Command, Send
from pydantic import BaseModel

from gustobot.application.agents.kg_sub_graph.agentic_rag_agents.components.state import (
    ToolSelectionInputState,
)
from gustobot.application.agents.kg_sub_graph.agentic_rag_agents.components.tool_selection.prompts import (
    create_tool_selection_prompt_template,
)
from gustobot.infrastructure.core.logger import get_logger

# Pre-build the prompt template so we can reuse it across calls.
tool_selection_prompt = create_tool_selection_prompt_template()
logger = get_logger(service="tool-selection")

SQL_KEYWORDS = [
    "sql",
    "统计",
    "报表",
    "同比",
    "环比",
    "平均",
    "总数",
    "数量",
    "占比",
    "select",
    "count",
    "sum",
    "avg",
    "max",
    "min",
    "group by",
    "order by",
    "having",
    "where",
    "insert",
    "update",
    "delete",
]
SQL_PATTERN = re.compile(r"\b(select|count|sum|avg|max|min|group\s+by|order\s+by)\b", re.IGNORECASE)

DESCRIPTIVE_KEYWORDS = [
    "口味",
    "特色",
    "风味",
    "营养",
    "功效",
    "健康",
    "介绍",
    "概况",
    "食材",
    "材料",
]


def _looks_like_sql_question(question: str) -> bool:
    """根据英文 SQL 片段或中英文统计类关键词，粗略判断是否在问结构化统计。"""
    normalized = question.lower()
    if SQL_PATTERN.search(normalized):
        return True
    return any(keyword in normalized for keyword in SQL_KEYWORDS)


def _make_command(target: str, payload: Dict[str, Any]) -> Command[Any]:
    """封装跳转到命名节点 target，并附带子任务 payload。"""
    return Command(goto=Send(target, payload))


def create_tool_selection_node(
    llm: BaseChatModel,
    tool_schemas: List[type[BaseModel]],
    default_to_text2cypher: bool = True,
) -> Callable[[ToolSelectionInputState], Coroutine[Any, Any, Command[Any]]]:
    """
    构造工具选择 LangGraph 节点。

    使用传入的 ``tool_schemas`` 绑定为 LLM 可调工具列表；节点每次调用返回应执行的
    下一跳（cypher_query / predefined_cypher / customer_tools / text2sql_query 等）。
    """

    tool_selection_chain: Runnable[Dict[str, Any], Any] = (
        tool_selection_prompt
        | llm.bind_tools(tools=tool_schemas)
        | PydanticToolsParser(tools=tool_schemas, first_tool_only=True)
    )

    # Record tool titles for quick membership checks.
    available_tools: Set[str] = {
        schema.model_json_schema().get("title", "") for schema in tool_schemas
    }

    async def tool_selection(
        state: ToolSelectionInputState,
    ) -> Command[Literal["cypher_query", "predefined_cypher", "customer_tools", "text2sql_query"]]:
        """
        根据 ``state["question"]`` 与 ``state["context"]``（如 route_type）选择工具并路由。

        命中规则捷径则不经 LLM；否则解析 LLM 的 tool call，映射到具体图节点名。
        """

        question_text = state.get("question", "")
        context = state.get("context") or {}
        route_type = (context.get("route_type") or "").lower()

        logger.info(
            "Tool selection invoked",
            extra={
                "question": question_text,
                "route_type": route_type or "unknown",
            },
        )

        # Heuristic fast path for结构化查询
        if any(keyword in question_text for keyword in DESCRIPTIVE_KEYWORDS):
            logger.info("检测到菜谱描述类需求，优先使用 GraphRAG。")
            return _make_command(
                "customer_tools",
                {
                    "task": question_text,
                    "query_name": "microsoft_graphrag_query",
                    "query_parameters": {"query": question_text},
                    "steps": ["tool_selection"],
                },
            )

        if _looks_like_sql_question(question_text) and route_type == "text2sql-query":
            logger.info("Detect text2sql intent via route_type/keywords，直接调用 Text2SQL 工具。")
            return _make_command(
                "text2sql_query",
                {
                    "task": question_text,
                    "query_name": "text2sql_query",
                    "query_parameters": {},
                    "steps": ["tool_selection"],
                },
            )

        # Heuristic for GraphRAG/LightRAG - 改为让 LLM 动态选择工具，而不是硬编码
        # if route_type == "graphrag-query":
        #     logger.info("Router 标记为 graphrag-query，优先使用 GraphRAG 工具。")
        #     return _make_command(
        #         "customer_tools",
        #         {
        #             "task": question_text,
        #             "query_name": "microsoft_graphrag_query",
        #             "query_parameters": {"query": question_text},
        #             "steps": ["tool_selection"],
        #         },
        #     )

        go_to_text2cypher: Command[
            Literal["cypher_query", "predefined_cypher", "customer_tools", "text2sql_query"]
        ] = Command(
            goto=Send(
                "cypher_query",
                {
                    "task": state.get("question", ""),
                    "query_name": "cypher_query",
                    "query_parameters": {"question": state.get("question", "")},
                    "steps": ["tool_selection"],
                },
            )
        )

        tool_selection_output: BaseModel | None = await tool_selection_chain.ainvoke(
            {"question": state.get("question", "")}
        )

        if tool_selection_output is not None:
            tool_name: str = tool_selection_output.model_json_schema().get("title", "")
            tool_args: Dict[str, Any] = tool_selection_output.model_dump()
            logger.info(
                "LLM 选择工具",
                extra={
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "route_type": route_type or "unknown",
                },
            )

            if tool_name == "predefined_cypher":
                return _make_command(
                    "predefined_cypher",
                    {
                        "task": question_text,
                        "query_name": tool_name,
                        "query_parameters": tool_args,
                        "steps": ["tool_selection"],
                    },
                )
            if tool_name == "cypher_query":
                return go_to_text2cypher
            if tool_name == "text2sql_query":
                return _make_command(
                    "text2sql_query",
                    {
                        "task": question_text,
                        "query_name": tool_name,
                        "query_parameters": tool_args,
                        "steps": ["tool_selection"],
                    },
                )
            if tool_name and tool_name in available_tools:
                return _make_command(
                    "customer_tools",
                    {
                        "task": question_text,
                        "query_name": tool_name,
                        "query_parameters": tool_args,
                        "steps": ["tool_selection"],
                    },
                )

        if default_to_text2cypher:
            logger.info("LLM 未给出明确工具，回退到 cypher_query。")
            return go_to_text2cypher

        logger.warning("无法确定工具，进入 error_tool_selection。")
        return _make_command(
            "error_tool_selection",
            {
                "task": question_text,
                "errors": [
                    f"Unable to assign tool to question: `{question_text}`"
                ],
                "steps": ["tool_selection"],
            },
        )

    return tool_selection
