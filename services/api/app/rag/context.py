"""上下文组装。

把检索结果整理成可以放进 Prompt 的引用块，并控制 token 预算。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.logging import get_logger
from app.rag.chunking import estimate_tokens
from app.rag.retriever import RetrievedChunk

logger = get_logger(__name__)


@dataclass(slots=True)
class Citation:
    """引用条目。"""

    id: int
    chunk_id: str
    title: str
    score: float
    evidence_level: int
    quote: str
    heading_path: list[str]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chunk_id": self.chunk_id,
            "title": self.title,
            "score": round(self.score, 4),
            "evidence_level": self.evidence_level,
            "quote": self.quote,
            "heading_path": self.heading_path,
        }


@dataclass(slots=True)
class ContextBlock:
    """组装好的上下文。"""

    text: str
    citations: list[Citation]
    token_count: int
    dropped: int = 0  # 因预算被丢弃的条目数
    deduped: int = 0  # 被去重合并的条目数

    @property
    def is_empty(self) -> bool:
        return not self.citations


def assemble_context(
    chunks: list[RetrievedChunk],
    *,
    token_budget: int = 3000,
    max_citations: int = 5,
    quote_max_chars: int = 220,
) -> ContextBlock:
    """把 chunk 组装成带编号的引用块。

    处理步骤：
    1. 按分数排序
    2. 内容去重（相似度高的条目只保留分数更高的）
    3. 按预算裁剪
    4. 重新编号（保证编号连续且从 1 开始）
    """
    if not chunks:
        return ContextBlock(text="", citations=[], token_count=0)

    ordered = sorted(chunks, key=lambda c: c.score, reverse=True)

    # 去重：内容高度重叠的条目只保留一个
    deduped: list[RetrievedChunk] = []
    seen_normalized: set[str] = set()
    dropped = 0
    for chunk in ordered:
        normalized = _normalize_for_dedup(chunk.content)
        if normalized in seen_normalized:
            dropped += 1
            continue
        seen_normalized.add(normalized)
        deduped.append(chunk)

    citations: list[Citation] = []
    blocks: list[str] = []
    used_tokens = 0

    for chunk in deduped:
        if len(citations) >= max_citations:
            dropped += 1
            continue

        quote = _make_quote(chunk.content, quote_max_chars)
        block = (
            f"[{len(citations) + 1}] 标题：{chunk.title}\n"
            f"    章节：{' / '.join(chunk.heading_path) if chunk.heading_path else '-'}\n"
            f"    证据等级：{chunk.metadata.get('evidence_level', '-')}/5\n"
            f"    内容：{quote}\n"
        )
        block_tokens = estimate_tokens(block)
        if used_tokens + block_tokens > token_budget and citations:
            dropped += 1
            continue

        citations.append(
            Citation(
                id=len(citations) + 1,
                chunk_id=chunk.id,
                title=chunk.title,
                score=chunk.score,
                evidence_level=int(chunk.metadata.get("evidence_level", 0) or 0),
                quote=quote,
                heading_path=chunk.heading_path,
            )
        )
        blocks.append(block)
        used_tokens += block_tokens

    text = (
        "<参考资料>\n" + "\n".join(blocks) + "</参考资料>\n"
        "要求：只能在 <依据> 中引用已给出的编号，不得生成不存在的编号；"
        "若参考资料不足以回答，明确说明并给出通用建议，不要编造依据。"
    )

    return ContextBlock(
        text=text,
        citations=citations,
        token_count=used_tokens,
        dropped=dropped,
        deduped=dropped,
    )


def _normalize_for_dedup(text: str) -> str:
    """粗粒度内容指纹：去标点与空白，取前 120 字。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    return cleaned[:120]


def _make_quote(content: str, max_chars: int) -> str:
    """从 chunk 中截取最有信息量的片段。

    优先保留首段（通常是定义或方法概述），
    若首段过短则拼接到下一个段落。
    """
    content = content.strip()
    if len(content) <= max_chars:
        return content

    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    if not paragraphs:
        return content[:max_chars] + "…"

    selected = paragraphs[0]
    for para in paragraphs[1:]:
        if len(selected) >= max_chars * 0.6:
            break
        selected += "\n" + para

    if len(selected) > max_chars:
        # 在句子边界截断，避免截半句
        truncated = selected[:max_chars]
        for sep in ("。", "；", "，", " "):
            idx = truncated.rfind(sep)
            if idx > max_chars * 0.5:
                truncated = truncated[: idx + 1]
                break
        return truncated + "…"
    return selected


def build_emotion_context(
    *,
    entries: list[dict],
    days: int = 7,
) -> str:
    """把情绪记录压缩成注入 Prompt 的上下文摘要。"""
    if not entries:
        return f"（用户近 {days} 天没有情绪记录）"

    intensities = [e.get("intensity", 0) for e in entries if e.get("intensity")]
    avg_intensity = sum(intensities) / len(intensities) if intensities else 0
    emotions: dict[str, int] = {}
    for entry in entries:
        emotion = entry.get("emotion")
        if emotion:
            emotions[emotion] = emotions.get(emotion, 0) + 1
    top_emotion = max(emotions, key=lambda name: emotions[name]) if emotions else "未知"

    tag_count: dict[str, int] = {}
    for entry in entries:
        for tag in entry.get("tags") or []:
            tag_count[tag] = tag_count.get(tag, 0) + 1
    top_tags = sorted(tag_count.items(), key=lambda kv: kv[1], reverse=True)[:3]

    lines = [
        f"用户近 {days} 天共记录 {len(entries)} 次情绪。",
        f"平均强度 {avg_intensity:.1f}/5，主要情绪为「{top_emotion}」。",
    ]
    if top_tags:
        lines.append("高频标签：" + "、".join(f"{tag}({count}次)" for tag, count in top_tags))
    return "\n".join(lines)
