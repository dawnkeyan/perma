"""工具目录单元测试。

工具是产品层的一等概念，它一旦错了，错的不是页面文案，而是**整条链路的语义**：
- 工具 id 对不上 → 客户端拿不到工具
- 检索侧重丢了 → 工具退化成普通对话，「匹配干预策略」落空
- 上下文要求丢了 → 「识别状态」没有数据支撑

所以这里对目录本身做契约级断言。
"""

from __future__ import annotations

import pytest
from app.agents.nodes import _merge_queries
from app.services.chat_service import ChatService
from app.tools.catalog import (
    TOOLS,
    ToolSpec,
    get_tool,
    list_tools,
    tools_for_emotion,
)


class TestCatalog:
    """目录本身的完整性。"""

    def test_catalog_not_empty(self) -> None:
        assert list_tools(), "工具目录不能为空"

    def test_tool_ids_unique(self) -> None:
        """id 重复会导致客户端拿到重复卡片，且无法按 id 定位。"""
        ids = [tool.id for tool in TOOLS]
        assert len(ids) == len(set(ids))

    def test_tool_has_required_fields(self) -> None:
        """每个工具都必须能被渲染成一张卡片。"""
        for tool in TOOLS:
            assert tool.id
            assert tool.name
            assert tool.description
            assert tool.icon
            assert tool.entry.startswith("/pages/")
            assert tool.starter_prompt.strip()
            assert tool.steps, f"{tool.id} 缺少流程步骤"

    def test_steps_have_hint(self) -> None:
        """步骤要带一句话说明——「AI 不做黑箱」是产品原则。"""
        for tool in TOOLS:
            for step in tool.steps:
                assert step.title.strip()
                assert step.hint.strip(), f"{tool.id} 的步骤「{step.title}」缺少说明"

    def test_get_tool_by_id(self) -> None:
        tool = get_tool("mood_navigation")
        assert tool is not None
        assert tool.name == "心绪导航"

    def test_get_unknown_tool_returns_none(self) -> None:
        assert get_tool("not-exist") is None


class TestMoodNavigation:
    """「心绪导航」这条具体工具的契约。"""

    @pytest.fixture
    def tool(self) -> ToolSpec:
        spec = get_tool("mood_navigation")
        assert spec is not None
        return spec

    def test_name_and_description(self, tool: ToolSpec) -> None:
        """产品文案不能被实现悄悄改掉。"""
        assert tool.name == "心绪导航"
        assert tool.description == (
            "情绪低落时，先识别状态，再匹配干预策略。每条建议都可追溯来源。"
        )

    def test_steps_cover_the_promised_flow(self, tool: ToolSpec) -> None:
        """简介里承诺的三步，必须真的出现在流程里。"""
        titles = [step.title for step in tool.steps]
        assert len(titles) == 3
        assert "识别" in titles[0]
        assert "策略" in titles[1]
        assert "来源" in titles[2]

    def test_requires_emotion_context(self, tool: ToolSpec) -> None:
        """「先识别状态」必须有情绪记录撑着，否则第一步是空转。"""
        assert "emotion_window" in tool.requires_context
        assert "profile" in tool.requires_context

    def test_focus_is_not_empty(self, tool: ToolSpec) -> None:
        """检索侧重不能为空，否则工具和普通对话没有区别。"""
        assert len(tool.focus) >= 2


class TestEmotionRecommendation:
    """按情绪推荐。"""

    def test_low_mood_recommends_navigation(self) -> None:
        tools = tools_for_emotion("sadness")
        assert any(tool.id == "mood_navigation" for tool in tools)

    def test_unknown_emotion_falls_back_to_all(self) -> None:
        """情绪识别失败时工具列表不能变空。"""
        assert tools_for_emotion("unknown-emotion") == list_tools()


class TestMergeQueries:
    """子查询与工具检索侧重的合并。"""

    def test_extra_queries_appended(self) -> None:
        merged = _merge_queries(["用户原话"], ["策略方向"])
        assert merged == ["用户原话", "策略方向"]

    def test_duplicates_removed(self) -> None:
        merged = _merge_queries(["情绪低落"], ["情绪低落", "认知重构"])
        assert merged == ["情绪低落", "认知重构"]

    def test_limit_enforced(self) -> None:
        """总查询数必须封顶，否则检索轮次失控会打崩延迟。"""
        merged = _merge_queries([f"q{i}" for i in range(5)], [f"e{i}" for i in range(5)], limit=6)
        assert len(merged) == 6

    def test_empty_extra_returns_primary(self) -> None:
        assert _merge_queries(["a"], []) == ["a"]


class TestResolveTool:
    """对话请求里的 tool_id 解析。"""

    def test_none_returns_none(self) -> None:
        assert ChatService._resolve_tool(None) is None
        assert ChatService._resolve_tool("") is None

    def test_known_tool_resolved(self) -> None:
        tool = ChatService._resolve_tool("mood_navigation")
        assert tool is not None
        assert tool.id == "mood_navigation"

    def test_unknown_tool_raises(self) -> None:
        """未知工具必须报错——静默忽略会让「工具已生效」变成假象。"""
        from app.core.errors import ValidationError

        with pytest.raises(ValidationError):
            ChatService._resolve_tool("not-exist")
