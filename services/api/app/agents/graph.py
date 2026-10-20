"""LangGraph 编排图。

图结构（见 docs/ARCHITECTURE.md §6.2）：

    entry ──crisis?──> crisis ──> END
      │
      └──> retrieve ──> generate ──> guard ──> action ──> reflect ──> END

用「图」而不是「链」的原因：
1. 危机场景需要**中断**整条链路（链式编排无法提前返回）
2. guard 校验失败需要**回环**到 retrieve 重试
3. 意图路由需要**分支**
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.nodes import (
    action_node,
    crisis_node,
    entry_node,
    generate_node,
    guard_node,
    reflect_node,
    retrieve_node,
)
from app.agents.state import CoachState, Intent
from app.core.logging import get_logger

logger = get_logger(__name__)

# guard 最多重试次数（补检索）
MAX_GUARD_RETRY = 1


def _route_from_entry(state: CoachState) -> str:
    """入口路由：危机走短路，其余走正常链路。"""
    if state.get("intent") == Intent.CRISIS:
        return "crisis"
    return "retrieve"


def _route_from_guard(state: CoachState) -> str:
    """守卫路由：引用缺失且未超重试次数时，回环到检索节点。"""
    retry = state.get("retry_count", 0)
    if retry >= MAX_GUARD_RETRY:
        return "action"
    flags = state.get("usage", {}).get("guard_flags", 0)
    if flags and state.get("citations"):
        state["retry_count"] = retry + 1
        logger.info("guard_retry_retrieve", retry=retry + 1)
        return "retrieve"
    return "action"


def build_graph(checkpointer: Any = None) -> Any:
    """构建并编译图。"""
    graph = StateGraph(CoachState)

    graph.add_node("entry", entry_node)
    graph.add_node("crisis", crisis_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)
    graph.add_node("guard", guard_node)
    graph.add_node("action", action_node)
    graph.add_node("reflect", reflect_node)

    graph.set_entry_point("entry")

    graph.add_conditional_edges(
        "entry",
        _route_from_entry,
        {"crisis": "crisis", "retrieve": "retrieve"},
    )
    graph.add_edge("crisis", END)
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "guard")
    graph.add_conditional_edges(
        "guard",
        _route_from_guard,
        {"retrieve": "retrieve", "action": "action"},
    )
    graph.add_edge("action", "reflect")
    graph.add_edge("reflect", END)

    return graph.compile(checkpointer=checkpointer)


_graph: Any = None


def get_graph(checkpointer: Any = None) -> Any:
    """获取编译后的图（单例）。"""
    global _graph
    if _graph is None:
        _graph = build_graph(checkpointer)
    return _graph


def reset_graph() -> None:
    global _graph
    _graph = None
