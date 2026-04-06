"""GustoBot Agent 命令行入口：加载 ``lg_builder.graph`` 并交互式处理用户查询。

使用 LangGraph ``MemorySaver`` 与随机 ``thread_id``；调用 ``graph.astream`` 时采用
``stream_mode='messages'`` 按块打印。入图前用 ``RemoveMessage`` 按轮数裁剪历史，避免
上下文无限增长。环境变量 ``GUSTOBOT_MEMORY_TURNS``（或 ``GUSTOBOT_MAX_MEMORY_TURNS``）
控制保留的用户轮数；≤0 表示不裁剪。若运行结束仍存在 ``interrupts``，可输入 ``y`` 以
``Command(resume)`` 继续生成。
"""

import sys
import os
from pathlib import Path
from typing import List, Optional, Sequence

# 将项目根目录添加到 Python 路径
root_dir = Path(__file__).parent.parent.parent
sys.path.append(str(root_dir))

from gustobot.application.agents.lg_states import InputState
from gustobot.application.agents.utils import new_uuid
from gustobot.application.agents.lg_builder import graph
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import RemoveMessage
from langgraph.types import Command
import asyncio
import time
import builtins

thread = {"configurable": {"thread_id": new_uuid()}}


def _resolve_memory_turn_limit() -> Optional[int]:
    """读取环境变量中的「记忆轮数」上限。

    优先 ``GUSTOBOT_MEMORY_TURNS``，否则 ``GUSTOBOT_MAX_MEMORY_TURNS``；默认字符串 ``"2"``。
    非整数时回退为 ``5``。返回值 ≤0 时视为不限制（返回 ``None``），此时不会删除历史消息。
    """
    raw_value = os.getenv("GUSTOBOT_MEMORY_TURNS", os.getenv("GUSTOBOT_MAX_MEMORY_TURNS", "2"))
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = 5
    return value if value > 0 else None


MEMORY_TURN_LIMIT: Optional[int] = _resolve_memory_turn_limit()


def _select_messages_to_remove(existing: Sequence[BaseMessage]) -> List[BaseMessage]:
    """在不超过 ``MEMORY_TURN_LIMIT`` 的前提下，选出应通过 ``RemoveMessage`` 删除的旧消息。

    从后往前数用户（human）消息，保留最近 ``MEMORY_TURN_LIMIT - 1`` 轮用户发言之前的所有
    带 ``id`` 的消息作为待删列表；若保留轮数为 0 则删除所有有 id 的历史。无限制（``None``）
    或空列表时返回空列表。
    """
    if not existing or MEMORY_TURN_LIMIT is None:
        return []

    humans_to_keep = max(MEMORY_TURN_LIMIT - 1, 0)
    if humans_to_keep == 0:
        return [msg for msg in existing if getattr(msg, "id", None)]

    humans_seen = 0
    keep_from = 0
    for index in range(len(existing) - 1, -1, -1):
        message = existing[index]
        if getattr(message, "type", None) == "human":
            humans_seen += 1
            if humans_seen == humans_to_keep:
                keep_from = index
                break
    else:
        keep_from = 0

    return [msg for msg in existing[:keep_from] if getattr(msg, "id", None)]


def _stringify_content(content: object) -> str:
    """将 LangChain 消息的 ``content`` 转成可打印字符串。

    支持 ``str``、``list``（多模态块：字符串或含 ``text`` 键的 dict）及其它类型的 ``str()``。
    ``None`` 返回空串。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


async def process_query(query: str) -> None:
    """执行一轮对话：裁剪历史、注入用户消息、流式打印助手回复并处理可选中断。

    先 ``graph.get_state`` 取当前消息列表，按轮数生成 ``RemoveMessage``，再追加
    ``HumanMessage``，以 ``InputState`` 调用 ``graph.astream``。打印时跳过元数据中
    ``tags`` 含 ``research_plan`` 的块（避免计划文本混入终端输出）。若结束后仍有
    ``interrupts``，提示用户输入 ``y`` 以 ``Command(resume)`` 继续流式输出。
    """
    state_snapshot = graph.get_state(thread)
    existing_messages = list(state_snapshot.values.get("messages", []))
    messages_to_remove = _select_messages_to_remove(existing_messages)

    removals = [
        RemoveMessage(id=msg.id)
        for msg in messages_to_remove
        if getattr(msg, "id", None)
    ]
    human_message = HumanMessage(content=query)
    input_messages: List[BaseMessage] = [*removals, human_message]
    input_state = InputState(messages=input_messages)

    async for chunk, metadata in graph.astream(
        input=input_state,
        stream_mode="messages",
        config=thread,
    ):
        text = _stringify_content(chunk.content)
        if text and "research_plan" not in metadata.get("tags", []):
            print(text, end="", flush=True)

    latest_snapshot = graph.get_state(thread)
    pending_tasks = latest_snapshot.tasks
    if pending_tasks and len(pending_tasks[0].interrupts) > 0:
        response = input('\n响应可能包含不确定信息。重试生成？如果是，按"y"：')
        if response.lower() == 'y':
            async for chunk, metadata in graph.astream(
                Command(resume=response),
                stream_mode="messages",
                config=thread,
            ):
                if chunk.additional_kwargs.get("tool_calls"):
                    print(chunk.additional_kwargs.get("tool_calls")[0]["function"].get("arguments"), end="")
                if chunk.content:
                    time.sleep(0.05)
                    print(chunk.content, end="", flush=True)


async def main() -> None:
    """REPL 主循环：读取 ``> `` 前缀输入，``q`` 退出，其余交给 :func:`process_query`。"""
    input_func = builtins.input
    while True:
        query = input_func("> ")
        if query.strip().lower() == "q":
            print("Exiting...")
            break
        await process_query(query)


if __name__ == "__main__":
    asyncio.run(main())
