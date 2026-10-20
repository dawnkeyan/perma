"""知识库分块与解析。

分块策略：**标题层级切分 + 父子块（small-to-big）**

- 小块（~256 token）用于检索，保证召回精确
- 父块（~1024 token）用于返回给模型，保证上下文完整

相比固定长度切分，这一策略在评测中 nDCG@5 提升约 0.13（见 docs/EVALUATION.md 消融表 H 行）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 估算 token 数：中文约 1 字 1.5 token，英文约 4 字符 1 token
_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[A-Za-z0-9]+")


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数。生产环境应替换为 tiktoken。"""
    cjk_chars = len(_CJK.findall(text))
    other_chars = len(text) - cjk_chars
    return int(cjk_chars * 1.5 + other_chars / 3.5) + 1


@dataclass(slots=True)
class Document:
    """解析后的文档。"""

    id: str
    source_path: str
    title: str
    content: str
    metadata: dict = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class Chunk:
    """文档块。"""

    id: str
    doc_id: str
    parent_id: str | None
    chunk_index: int
    content: str
    token_count: int
    heading_path: list[str]
    metadata: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_H2 = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_H3 = re.compile(r"^###\s+(.+)$", re.MULTILINE)


def parse_markdown(path: Path) -> Document | None:
    """解析带 frontmatter 的 Markdown 文档。"""
    raw = path.read_text(encoding="utf-8")

    metadata: dict = {}
    match = _FRONT_MATTER.match(raw)
    if match:
        try:
            metadata = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            metadata = {}
        raw = raw[match.end() :]

    title = metadata.get("title") or path.stem
    if not match:
        # 没有 frontmatter 时，用第一个 H1 作为标题
        h1 = re.search(r"^#\s+(.+)$", raw, re.MULTILINE)
        if h1:
            title = h1.group(1).strip()

    content = raw.strip()
    if not content:
        return None

    return Document(
        id=f"doc_{hashlib.sha256(str(path).encode()).hexdigest()[:12]}",
        source_path=str(path),
        title=title,
        content=content,
        metadata=metadata,
    )


# --------------------------------------------------------------------------- #
# 分块
# --------------------------------------------------------------------------- #
def split_by_heading(
    doc: Document,
    *,
    child_max_tokens: int = 256,
    parent_max_tokens: int = 1024,
    overlap_sections: int = 1,
) -> list[Chunk]:
    """按标题层级切分，生成 父子块。

    父块 = 一个 H2 章节（超长则再按 token 预算二次切分）
    子块 = 父块内部的段落级小块（超长则按句子边界再切）
    """
    chunks: list[Chunk] = []
    sections = _split_into_sections(doc.content)
    parent_index = 0
    child_index = 0

    for heading_path, section_text in sections:
        if not section_text.strip():
            continue

        for parent_text in _split_by_budget(section_text, parent_max_tokens, overlap_sections):
            parent_id = f"{doc.id}_p{parent_index}"
            chunks.append(
                Chunk(
                    id=parent_id,
                    doc_id=doc.id,
                    parent_id=None,
                    chunk_index=parent_index,
                    content=parent_text,
                    token_count=estimate_tokens(parent_text),
                    heading_path=heading_path,
                    metadata={**doc.metadata, "level": "parent"},
                )
            )

            for child_text in _split_by_budget(parent_text, child_max_tokens, overlap_sections):
                chunks.append(
                    Chunk(
                        id=f"{parent_id}_c{child_index}",
                        doc_id=doc.id,
                        parent_id=parent_id,
                        chunk_index=child_index,
                        content=child_text,
                        token_count=estimate_tokens(child_text),
                        heading_path=heading_path,
                        metadata={**doc.metadata, "level": "child"},
                    )
                )
                child_index += 1
            parent_index += 1

    return chunks


def _split_into_sections(content: str) -> list[tuple[list[str], str]]:
    """按 H2 切分，H3 作为子标题保留在 heading_path 中。"""
    lines = content.split("\n")
    sections: list[tuple[list[str], str]] = []
    current_h2 = ""
    current_h3 = ""
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            path = [p for p in (current_h2, current_h3) if p]
            sections.append((path, "\n".join(buffer).strip()))

    for line in lines:
        if line.startswith("## ") and not line.startswith("### "):
            flush()
            buffer = []
            current_h2 = line[3:].strip()
            current_h3 = ""
        elif line.startswith("### "):
            flush()
            buffer = []
            current_h3 = line[4:].strip()
        else:
            buffer.append(line)
    flush()

    # 前言部分（第一个 H2 之前）
    if sections and not sections[0][0]:
        pass

    return sections


def _split_by_budget(text: str, max_tokens: int, overlap: int) -> list[str]:
    """按 token 预算切分，优先在段落/句子边界断开。"""
    if estimate_tokens(text) <= max_tokens:
        return [text]

    paragraphs = text.split("\n\n")
    result: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = estimate_tokens(para)
        if current and current_tokens + para_tokens > max_tokens:
            result.append("\n\n".join(current))
            # 重叠：保留最后 overlap 段
            current = current[-overlap:] if overlap else []
            current_tokens = sum(estimate_tokens(p) for p in current)
        current.append(para)
        current_tokens += para_tokens

    if current:
        result.append("\n\n".join(current))

    # 单段超长时按句子切
    final: list[str] = []
    for part in result:
        if estimate_tokens(part) <= max_tokens:
            final.append(part)
            continue
        final.extend(_split_long_paragraph(part, max_tokens))
    return final


def _split_long_paragraph(paragraph: str, max_tokens: int) -> list[str]:
    sentences = re.split(r"(?<=[。！？!?；;])", paragraph)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if current and estimate_tokens(current + sentence) > max_tokens:
            chunks.append(current.strip())
            current = sentence
        else:
            current += sentence
    if current.strip():
        chunks.append(current.strip())
    return chunks


def load_documents(source_dir: str | Path) -> list[Document]:
    """递归加载目录下的所有 Markdown 文档。"""
    root = Path(source_dir)
    if not root.exists():
        return []
    docs: list[Document] = []
    for path in sorted(root.rglob("*.md")):
        if path.name.startswith("_"):
            continue
        doc = parse_markdown(path)
        if doc:
            docs.append(doc)
    return docs


def chunk_documents(docs: list[Document]) -> list[Chunk]:
    """对所有文档分块。"""
    chunks: list[Chunk] = []
    for doc in docs:
        chunks.extend(split_by_heading(doc))
    return chunks
