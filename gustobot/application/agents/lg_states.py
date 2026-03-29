from pydantic import BaseModel, Field
from dataclasses import dataclass, field
from typing import Annotated, Any, Dict, List, Literal, Optional
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages


class Router(BaseModel):
    """对用户查询做分类（同时兼容旧字段/属性访问）。"""

    logic: str = ""
    type: Literal[
        "general-query",
        "additional-query",
        "kb-query",
        "graphrag-query",
        "image-query",
        "file-query",
        "text2sql-query",
    ] = "kb-query"
    question: str = ""
    decision: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None

    class Config:
        extra = "allow"

    def get(self, key: str, default: Any = None) -> Any:
        """以字典风格访问，兼容旧逻辑。"""
        return getattr(self, key, self.__dict__.get(key, default))


@dataclass(kw_only=True)
class RouteResult:
    """路由选择结果，供下游节点使用。"""
    route: str
    confidence: float = 0.0
    next_node: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class GradeHallucinations(BaseModel):
    """对生成答案是否出现幻觉的二元评分。"""

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
    """检索图 / Agent 的运行状态。"""
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
