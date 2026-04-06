"""LangGraph Agent 的状态与路由相关数据模型。

``InputState`` / ``AgentState`` 供 ``lg_builder`` 中 ``StateGraph`` 使用；``Router`` 为
意图分类结构化输出；``GradeHallucinations`` 供幻觉检测节点写入状态。
"""

from pydantic import BaseModel, Field
from dataclasses import dataclass, field
from typing import Annotated, Any, Dict, List, Literal, Optional
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages


class Router(BaseModel):
    """对用户查询做意图分类的结构化结果（Pydantic，允许 ``extra`` 以兼容旧字段）。

    ``type`` 取值与主图条件边一致，如 ``general-query``、``graphrag-query``、``kb-query`` 等。
    ``get(key)`` 方法可按类字典方式读取动态字段。
    """

    logic: str = "" # 路由给出的分类理由
    type: Literal[
        "general-query",
        "additional-query",
        "kb-query",
        "graphrag-query",
        "image-query",
        "file-query",
        "text2sql-query",
    ] = "kb-query" # 默认路由到知识库查询
    question: str = "" # 用户问题文本
    decision: Optional[str] = None # 护栏判定：继续处理或结束
    confidence: Optional[float] = None # 置信度：0-1，表示模型对分类的信心程度
    reasoning: Optional[str] = None # 推理过程：模型对分类的推理过程

    class Config:
        extra = "allow"

    def get(self, key: str, default: Any = None) -> Any:
        """以字典风格访问，兼容旧逻辑。"""
        return getattr(self, key, self.__dict__.get(key, default))


@dataclass(kw_only=True)
class RouteResult:
    """路由选择结果（名称、置信度、下一节点与附加元数据）。

    可与 ``Router`` 配合使用，用于需要显式 ``next_node`` 字符串的编排逻辑。
    """
    route: str
    confidence: float = 0.0
    next_node: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class GradeHallucinations(BaseModel):
    """对「模型回复是否严格依据检索事实」的二元评分（字符串 ``'1'`` / ``'0'``）。"""

    binary_score: str = Field(
        description="答案是否基于事实：'1' 表示是，'0' 表示否"
    )


@dataclass(kw_only=True)
class InputState:
    """Agent 的输入状态结构。

    包含用户与 Agent 之间的消息列表。`messages` 使用 `add_messages` 归约：
    合并两条消息列表，按 ID 更新已有消息；默认可视为追加，若新消息与某条已有消息
    ID 相同则替换。

    典型模式为 Human / AI 交替；若结合带工具调用的 ReAct，大致为：
    1. HumanMessage：用户输入
    2. 含 .tool_calls 的 AIMessage：选择要调用的工具
    3. ToolMessage：工具执行结果或错误
    （按需重复 2、3）
    4. 不含 .tool_calls 的 AIMessage：以自然语言回复用户
    5. HumanMessage：下一轮用户输入
    （按需重复 2–5）
    """

    # messages 的类型是消息列表，且在状态更新时用 add_messages 规则做合并
    messages: Annotated[list[AnyMessage], add_messages]

# kw_only：强制要求数据类中的所有字段必须以关键字参数的形式提供。即不能以位置参数的方式传递。
@dataclass(kw_only=True)
class AgentState(InputState):
    """在 ``InputState`` 之上扩展的完整 Agent 状态，贯穿主图各节点。

    字段说明：

    - ``router``：意图分类结果，决定条件边走向。
    - ``steps``：可选的中间步骤说明（如检索轨迹），供调试或展示。
    - ``documents``：检索到的文本依据，供幻觉检测等节点使用。
    - ``question`` / ``answer``：可缓存规范化问题或最终答案（按节点写入）。
    - ``hallucination``：幻觉评分结构化结果。
    - ``sources``：引用来源列表，常与知识库返回一并更新。
    """
    # 路由器对用户问题的分类结果
    router: Router = field(default_factory=lambda: Router(type="general-query", logic=""))
    # 由检索等环节填充，Agent 可参考的步骤/轨迹说明列表
    steps: list[str] = field(default_factory=list)
    # 从知识库检索到的文档片段等
    documents: list[str] = field(default_factory=list)
    question: str = field(default_factory=str)
    answer: str = field(default_factory=str)
    hallucination: GradeHallucinations = field(default_factory=lambda: GradeHallucinations(binary_score="0"))
    # 知识库查询等返回的引用来源
    sources: list = field(default_factory=list)
