"""知识库分块与解析单元测试。

分块策略是 RAG 召回质量的上游依赖：
检索用小块（精确），返回用父块（完整）。
如果分块逻辑坏了，评测里的 Recall@5 会直接掉下来，
而这类问题在端到端表现上只体现为「答得不好」，很难定位。

所以这里对分块做**契约级**断言：
- 父子关系必须自洽
- token 预算必须被遵守
- 标题路径必须可追溯
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.chunking import (
    Document,
    chunk_documents,
    estimate_tokens,
    load_documents,
    parse_markdown,
    split_by_heading,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestEstimateTokens:
    """token 估算。"""

    def test_empty_text(self) -> None:
        assert estimate_tokens("") >= 1

    def test_chinese_costs_more_than_english(self) -> None:
        """中文通常 1 字 ≈ 1~2 token，英文 1 词 ≈ 1 token。

        同样是 10 个字符，中文应该比英文贵——这个比例关系错了，
        整个 token 预算就会失真。
        """
        chinese = estimate_tokens("情绪压力与睡眠质量的关系研究")
        english = estimate_tokens("abcdefghij")
        assert chinese > english

    def test_monotonic(self) -> None:
        """文本越长，token 估计不应该变少。"""
        short = estimate_tokens("今天有点累")
        long = estimate_tokens("今天有点累" * 20)
        assert long > short


class TestParseMarkdown:
    """Markdown 解析。"""

    def test_parses_frontmatter(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.md"
        path.write_text(
            "---\ntitle: 认知重构\nsource: CBT\n---\n\n# 忽略我\n\n正文内容。\n",
            encoding="utf-8",
        )
        doc = parse_markdown(path)
        assert doc is not None
        assert doc.title == "认知重构"
        assert doc.metadata["source"] == "CBT"
        assert "忽略我" not in doc.content, "frontmatter 之后的内容不应被误当标题"

    def test_fallback_to_h1_title(self, tmp_path: Path) -> None:
        """没有 frontmatter 时，用第一个 H1 当标题。"""
        path = tmp_path / "perma.md"
        path.write_text("# 积极心理学\n\nPERMA 模型介绍。\n", encoding="utf-8")
        doc = parse_markdown(path)
        assert doc is not None
        assert doc.title == "积极心理学"

    def test_empty_document_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.md"
        path.write_text("", encoding="utf-8")
        assert parse_markdown(path) is None

    def test_content_hash_is_stable(self, tmp_path: Path) -> None:
        """内容哈希用于增量导入时判断「是否变更过」，必须稳定。"""
        path = tmp_path / "a.md"
        path.write_text("# 标题\n\n内容。\n", encoding="utf-8")
        first = parse_markdown(path)
        second = parse_markdown(path)
        assert first is not None and second is not None
        assert first.content_hash == second.content_hash


class TestSplitByHeading:
    """父子块分块。"""

    @staticmethod
    def _doc() -> Document:
        return Document(
            id="doc_test",
            source_path="memory://test.md",
            title="测试文档",
            content=(
                "## 第一节\n\n"
                "认知重构的第一步是识别自动想法。\n\n"
                "## 第二节\n\n"
                "接着要寻找证据支持或反驳它。\n\n"
                "### 补充\n\n"
                "如果证据不足，就先做一次小实验来验证。\n"
            ),
            metadata={"source": "test"},
        )

    def test_produces_parent_and_child(self) -> None:
        chunks = split_by_heading(self._doc())
        parents = [c for c in chunks if c.parent_id is None]
        children = [c for c in chunks if c.parent_id is not None]
        assert parents, "必须生成父块"
        assert children, "必须生成子块"

    def test_child_points_to_existing_parent(self) -> None:
        """子块的 parent_id 必须指向真实存在的父块。

        这条不成立的话，small-to-big 回溯会取不到完整上下文。
        """
        chunks = split_by_heading(self._doc())
        parent_ids = {c.id for c in chunks if c.parent_id is None}
        for child in chunks:
            if child.parent_id is not None:
                assert child.parent_id in parent_ids, f"父块缺失：{child.parent_id}"

    def test_heading_path_is_recorded(self) -> None:
        """标题路径必须保留，用于回显「依据来自哪一节」。"""
        chunks = split_by_heading(self._doc())
        paths = [tuple(c.heading_path) for c in chunks]
        assert ("第一节",) in paths
        assert ("第二节",) in paths
        # H3 应当带上父级 H2
        assert ("第二节", "补充") in paths

    def test_child_respects_token_budget(self) -> None:
        """子块不得超过配置的 token 预算（允许少量误差）。"""
        doc = Document(
            id="doc_long",
            source_path="memory://long.md",
            title="长文",
            content="## 第一节\n\n" + ("这是一段用于测试分块预算的中文内容。" * 80),
        )
        chunks = split_by_heading(doc, child_max_tokens=256, parent_max_tokens=1024)
        for child in chunks:
            if child.parent_id is None:
                continue
            assert child.token_count <= 256 + 32, f"子块超出预算：{child.token_count}"

    def test_empty_content_yields_no_chunks(self) -> None:
        doc = Document(id="d", source_path="m://x", title="t", content="")
        assert split_by_heading(doc) == []

    def test_metadata_is_propagated(self) -> None:
        """文档级 metadata（如证据等级）必须传到块上，否则过滤会失效。"""
        doc = Document(
            id="doc_meta",
            source_path="memory://meta.md",
            title="t",
            content="## A\n\n内容。\n",
            metadata={"evidence_level": 2, "source": "manual"},
        )
        chunks = split_by_heading(doc)
        assert chunks
        for chunk in chunks:
            assert chunk.metadata["evidence_level"] == 2
            assert chunk.metadata["source"] == "manual"
            assert chunk.metadata["level"] in ("parent", "child")


class TestLoadDocuments:
    """目录加载。"""

    def test_loads_real_knowledge_base(self) -> None:
        """真实知识库必须能被正常加载（防止格式写坏导致线上导入失败）。"""
        docs = load_documents(REPO_ROOT / "packages" / "knowledge")
        assert docs, "packages/knowledge 下应有可加载的文档"
        assert all(d.content.strip() for d in docs)

    def test_skips_underscore_files(self, tmp_path: Path) -> None:
        """下划线开头的文件视为「不导入」（草稿/模板）。"""
        (tmp_path / "_draft.md").write_text("# 草稿\n\n内容。\n", encoding="utf-8")
        (tmp_path / "real.md").write_text("# 正文\n\n内容。\n", encoding="utf-8")
        docs = load_documents(tmp_path)
        assert len(docs) == 1
        assert "real" in docs[0].source_path

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert load_documents(tmp_path / "not-exist") == []

    def test_chunk_documents_covers_all_docs(self) -> None:
        docs = load_documents(REPO_ROOT / "packages" / "knowledge")
        chunks = chunk_documents(docs)
        assert chunks
        assert {c.doc_id for c in chunks} == {d.id for d in docs}


@pytest.mark.parametrize("filename", ["perma.md", "cognitive-restructuring.md"])
def test_known_knowledge_files_parse(filename: str) -> None:
    """已知的知识文件必须能解析（防止误改 frontmatter 格式）。"""
    path = REPO_ROOT / "packages" / "knowledge"
    candidates = list(path.rglob(filename))
    if not candidates:
        pytest.skip(f"未找到 {filename}")
    doc = parse_markdown(candidates[0])
    assert doc is not None
    assert doc.title
