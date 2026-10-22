"""Prompt 注册中心单元测试。

Prompt 是**模型行为的一部分**，它的变更等同于代码变更：
需要版本号、需要评测、需要回滚、需要能追溯线上到底跑的是哪一版。

这套测试守护的就是这条链路上的三件事：
1. 加载——磁盘上的 YAML 能被正确解析
2. 版本——能按 `name@version` 精确取，也能取 active 版本
3. 渲染——变量齐全才渲染，缺变量必须**报错而不是静默填空**
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.prompts.registry import PromptRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]
PROMPT_DIR = REPO_ROOT / "packages" / "prompts"


@pytest.fixture
def registry() -> PromptRegistry:
    """指向仓库真实 Prompt 目录的注册中心。"""
    return PromptRegistry(PROMPT_DIR)


class TestLoad:
    """加载。"""

    def test_loads_real_prompts(self, registry: PromptRegistry) -> None:
        """真实 Prompt 目录必须能加载（防止 YAML 写坏导致线上起不来）。"""
        registry.load()
        assert registry.list_versions("coach"), "packages/prompts 下应有可加载的 Prompt"

    def test_missing_dir_is_not_fatal(self, tmp_path: Path) -> None:
        """目录不存在时不能抛异常——否则服务在缺配置的机器上直接起不来。"""
        reg = PromptRegistry(tmp_path / "not-exist")
        reg.load()
        assert reg.list_versions("coach") == []

    def test_broken_yaml_is_skipped(self, tmp_path: Path) -> None:
        """单个 YAML 损坏不应该让整个加载失败。"""
        (tmp_path / "good.yaml").write_text(
            "name: good\nversion: 1\nstatus: active\ntemplate: 'hello'\n", encoding="utf-8"
        )
        (tmp_path / "bad.yaml").write_text("name: [unclosed\n", encoding="utf-8")
        reg = PromptRegistry(tmp_path)
        reg.load()
        assert len(reg.list_versions("good")) == 1
        assert reg.list_versions("bad") == []


class TestVersioning:
    """版本管理。"""

    def test_get_active_version(self, registry: PromptRegistry) -> None:
        """不指定版本时取 active 版本。"""
        meta = registry.get("coach")
        assert meta.name == "coach"
        assert meta.status == "active"

    def test_get_specific_version(self, registry: PromptRegistry) -> None:
        """能按版本号精确取到历史版本——这是回滚的前提。

        这里不写死版本号：Prompt 的历史版本会随迭代增多，
        写死一个具体数字（比如 1）会让测试在新增/清理版本后莫名其妙地失败。
        取列表里的最早版本，验证的是「按版本号能精确取到」这个行为本身。
        """
        versions = registry.list_versions("coach")
        assert versions, "coach 至少应该有一个版本"
        oldest = versions[0]
        meta = registry.get("coach", oldest.version)
        assert meta.version == oldest.version
        assert meta.content_hash == oldest.content_hash

    def test_list_versions_is_sorted(self, registry: PromptRegistry) -> None:
        versions = registry.list_versions("coach")
        assert versions, "coach 至少应该有一个版本"
        assert [m.version for m in versions] == sorted(m.version for m in versions)

    def test_unknown_prompt_raises(self, registry: PromptRegistry) -> None:
        with pytest.raises(KeyError):
            registry.get("not-exist-prompt")

    def test_content_hash_is_stable(self, registry: PromptRegistry) -> None:
        """内容哈希用于识别「模板是否真的变了」。"""
        first = registry.get("coach")
        second = registry.get("coach")
        assert first.content_hash == second.content_hash
        assert first.content_hash

    def test_meta_records_eval_scores(self, registry: PromptRegistry) -> None:
        """Prompt 必须记录评测分数——没有分数就无法判断改动是好是坏。"""
        meta = registry.get("coach")
        assert meta.eval_scores, "coach prompt 应记录 eval_scores"
        assert "protocol_compliance" in meta.eval_scores

    def test_meta_records_changelog(self, registry: PromptRegistry) -> None:
        """必须有 changelog，说明每次改了什么。"""
        meta = registry.get("coach")
        assert meta.changelog.strip()


class TestRender:
    """渲染。"""

    def test_renders_with_all_variables(self, registry: PromptRegistry) -> None:
        """变量齐全时正常渲染。"""
        prompt = registry.render(
            "coach",
            {
                "emotion_context": "最近 7 天平均强度 3.2",
                "user_profile": "优势：坚持力",
                "context": "[1] 认知重构",
                "history": "（无历史对话）",
            },
        )
        assert "最近 7 天平均强度 3.2" in prompt.body
        assert "坚持力" in prompt.body
        assert "{{" not in prompt.body, "不应残留未渲染的模板变量"

    def test_missing_variable_raises(self, registry: PromptRegistry) -> None:
        """**关键**：缺变量必须报错，不能静默渲染成空串。

        静默填空会让模型收到残缺上下文，而线上根本看不出来——
        这类问题往往要到 BadCase 分析时才发现。
        """
        with pytest.raises(ValueError):
            registry.render("coach", {"emotion_context": "x"})

    def test_prompt_key_format(self, registry: PromptRegistry) -> None:
        prompt = registry.render(
            "coach",
            {
                "emotion_context": "x",
                "user_profile": "x",
                "context": "x",
                "history": "x",
            },
        )
        assert prompt.version_key == f"coach@{prompt.meta.version}"


class TestProtocolConstraints:
    """输出协议约束（从 Prompt 文本层面守护）。"""

    def test_coach_prompt_declares_four_sections(self, registry: PromptRegistry) -> None:
        """主提示词必须显式声明四段式标签——这是输出可解析的前提。"""
        meta = registry.get("coach")
        for tag in ("<共情>", "<洞察>", "<依据>", "<微行动>"):
            assert tag in meta.template, f"coach prompt 缺少 {tag} 标签声明"

    def test_coach_prompt_forbids_diagnosis(self, registry: PromptRegistry) -> None:
        """主提示词必须包含「不诊断」的显式禁止条款。"""
        meta = registry.get("coach")
        assert "不要诊断" in meta.template
