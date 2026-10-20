"""Agent 状态定义。

状态是整张图的唯一数据流载体。每个节点只做「读 state → 写 state」，
不共享可变对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, NotRequired, TypedDict

from app.agents.safety import SafetyVerdict
from app.rag.context import Citation
from app.rag.retriever import SearchDiagnostics


class Intent(StrEnum):
    """用户意图。"""

    CHITCHAT = "chitchat"              # 寒暄、无意义
    KNOWLEDGE = "knowledge"            # 心理学知识问答
    EMOTION_SUPPORT = "emotion_support"  # 情绪支持
    REFLECTION = "reflection"          # 认知重构
    CRISIS = "crisis"                  # 危机
    MEDICAL = "medical"                # 医疗越界


@dataclass(slots=True)
class EmotionContext:
    """用户近期情绪上下文。"""

    days: int = 7
    entry_count: int = 0
    avg_intensity: float = 0.0
    dominant_emotion: str = ""
    top_tags: list[str] = field(default_factory=list)
    text: str = ""

    @classmethod
    def empty(cls) -> EmotionContext:
        return cls(text="（暂无情绪记录）")


@dataclass(slots=True)
class UserProfile:
    """用户画像。"""

    strengths: list[str] = field(default_factory=list)
    blind_spots: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    long_term_theme: str = ""

    @property
    def is_empty(self) -> bool:
        return not any(
            [self.strengths, self.blind_spots, self.preferences, self.long_term_theme]
        )


@dataclass(slots=True)
class ChatTurn:
    """一轮对话。"""

    role: str
    content: str


@dataclass(slots=True)
class NodeTrace:
    """节点执行轨迹。用于 trace UI 与 BadCase 归因。"""

    node: str
    duration_ms: int = 0
    summary: dict[str, Any] = field(default_factory=dict)
    output_version: str | None = None


class CoachState(TypedDict):
    """Agent 图的状态。"""

    # ---- 输入 ----
    user_id: str
    session_id: str
    query: str
    history: list[dict[str, str]]
    emotion_context: dict[str, Any]
    user_profile: dict[str, Any]

    # ---- 控制流 ----
    intent: str
    turn: int
    retry_count: int
    needs_retrieval: bool

    # ---- 检索 ----
    sub_queries: list[str]
    retrieved_chunks: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    search_diagnostics: dict[str, Any]

    # ---- 输出 ----
    answer: str
    sections: dict[str, Any]
    micro_action: str
    safety: dict[str, Any]

    # ---- 追踪 ----
    trace: list[dict[str, Any]]
    prompt_version: str
    usage: dict[str, int]
    cost_cny: float


def make_initial_state(
    *,
    user_id: str,
    session_id: str,
    query: str,
    history: list[dict[str, str]] | None = None,
    emotion_context: dict[str, Any] | None = None,
    user_profile: dict[str, Any] | None = None,
) -> CoachState:
    """构造初始状态。"""
    return CoachState(
        user_id=user_id,
        session_id=session_id,
        query=query,
        history=history or [],
        emotion_context=emotion_context or {},
        user_profile=user_profile or {},
        intent=Intent.CHITCHAT,
        turn=0,
        retry_count=0,
        needs_retrieval=False,
        sub_queries=[],
        retrieved_chunks=[],
        citations=[],
        search_diagnostics={},
        answer="",
        sections={},
        micro_action="",
        safety={},
        trace=[],
        prompt_version="",
        usage={"prompt_tokens": 0, "completion_tokens": 0},
        cost_cny=0.0,
    )


__all__ = [
    "ChatTurn",
    "CoachState",
    "EmotionContext",
    "Intent",
    "NodeTrace",
    "NotRequired",
    "SafetyVerdict",
    "UserProfile",
    "make_initial_state",
]
