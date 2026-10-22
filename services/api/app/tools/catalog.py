"""工具目录（Tool Catalog）。

「工具」是比「页面」更产品化的一层抽象：
一个工具 = 一段被产品定义好的引导流程 + 一组检索侧重 + 一句固定的话术。

为什么要单独抽这一层
------------------
1. 小程序首页要展示工具卡片（名称 + 简介）。这些文案如果散落在前端代码里，
   产品改一句话就要改前端 → 提审 → 发版；放在服务端目录里，前端只负责渲染，
   文案变更不需要发版。
2. 工具的「进入引导语」必须和检索策略（focus）放在一起，
   否则就会出现「话术说匹配干预策略，检索却跑偏」的情况。
3. 目录是纯数据，可以被测试与评测用例直接引用。

关键设计：工具**不新增任何执行链路**
------------------------------------
每个工具最终都落到同一条 Agent 管线上：

    识别状态 → 匹配干预策略 → 给出可追溯来源
    (emotion_context 注入) → (RAG 检索) → (citations 引用)

工具本身只是对现有链路的一次「参数化预设」：
它决定进入时说什么话、检索时往哪个方向偏、需要哪些上下文。
这样保证「工具」不会变成一个个功能孤岛。

安全约束
--------
工具入口**不绕过**安全链路。用户在工具里说出危机表达时，
仍然走 `detect_crisis` 的硬编码干预话术（见 app/agents/safety.py）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolStepSpec:
    """工具流程中的一步。

    `title` 是给用户看的一行标题，`hint` 是一句话说明这一步会发生什么。
    两段都直接展示在客户端——用户点进工具前就该知道要经历什么，
    这是「AI 工具不做黑箱」这条产品原则的体现。
    """

    title: str
    hint: str


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """一个工具的完整定义。

    字段说明：
    - `id`：稳定标识，客户端只认它，不认中文名（中文名会改）
    - `name`：展示名
    - `description`：一句话简介，客户端直接展示
    - `icon`：客户端图标 key（具体图形由客户端资源决定，服务端不下发图片）
    - `entry`：客户端入口路由
    - `starter_prompt`：进入工具时预填给用户的引导语
    - `steps`：工具的三步流程，直接展示给用户，让用户知道「接下来会发生什么」
    - `focus`：检索侧重，喂给 RAG 的附加查询方向（干预策略匹配用）
    - `recommended_for`：建议在哪些情绪下置顶展示
    - `requires_context`：需要注入的上下文（emotion_window / profile 等）
    """

    id: str
    name: str
    description: str
    icon: str
    entry: str
    starter_prompt: str
    steps: tuple[ToolStepSpec, ...]
    focus: tuple[str, ...]
    recommended_for: tuple[str, ...]
    requires_context: tuple[str, ...]


# --------------------------------------------------------------------------- #
# 工具目录
# --------------------------------------------------------------------------- #
TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="mood_navigation",
        name="心绪导航",
        description="情绪低落时，先识别状态，再匹配干预策略。每条建议都可追溯来源。",
        icon="compass",
        entry="/pages/chat/index",
        starter_prompt=(
            "我最近情绪有点低落，想先弄清楚自己现在的状态，再看看有哪些心理学方法可以帮我调整。"
        ),
        steps=(
            ToolStepSpec(
                title="识别当前状态",
                hint="结合你最近 7 天的情绪记录，判断现在的状态与触发因素",
            ),
            ToolStepSpec(
                title="匹配干预策略",
                hint="从积极心理学方法库中匹配适合当前状态的干预方式",
            ),
            ToolStepSpec(
                title="查看建议来源",
                hint="每条建议都标注依据的知识条目，可展开查看原文与证据等级",
            ),
        ),
        # 干预策略的检索侧重：优先命中「行为激活 / 认知重构 / 社会联结」等
        # 面向低落情绪的条目，而不是泛泛的积极心理学科普。
        focus=(
            "情绪低落 识别 情绪状态",
            "行为激活 启动困难 行动",
            "认知重构 消极认知 自动化想法",
            "社会联结 支持系统 求助",
        ),
        recommended_for=("sadness", "fatigue", "anxiety"),
        requires_context=("emotion_window", "profile"),
    ),
)


def list_tools() -> list[ToolSpec]:
    """返回全部工具（按目录定义顺序）。"""
    return list(TOOLS)


def get_tool(tool_id: str) -> ToolSpec | None:
    """按 id 取工具；不存在返回 None。"""
    for tool in TOOLS:
        if tool.id == tool_id:
            return tool
    return None


def tools_for_emotion(emotion: str) -> list[ToolSpec]:
    """返回针对某个情绪推荐的工具（用于首页置顶排序）。

    没有匹配时返回全部工具——工具列表不该因为情绪识别失败而变空。
    """
    matched = [tool for tool in TOOLS if emotion in tool.recommended_for]
    return matched or list(TOOLS)


__all__ = [
    "TOOLS",
    "ToolSpec",
    "ToolStepSpec",
    "get_tool",
    "list_tools",
    "tools_for_emotion",
]
