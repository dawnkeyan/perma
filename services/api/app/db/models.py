"""SQLAlchemy ORM 模型定义。

对应文档：docs/DATA_MODEL.md（DDL 是权威来源，本文件必须与之保持一致）。

设计取舍说明：

1. **使用 SQLAlchemy 2.0 的 Mapped/mapped_column 风格**
   相比旧的 Column 风格，类型信息能被静态检查器识别，mypy 友好。

2. **主键统一用 TEXT**
   业务主键形如 `u_<ulid>` / `m_<hex>`，是应用层生成的 ULID/UUID 短码。
   原因：分库分表、离线导入、跨环境迁移时不需要处理自增序列冲突。

3. **时间统一用 TIMESTAMP WITH TIME ZONE**
   数据库存 UTC，展示层再按用户时区转换。绝不在库里存本地时间。

4. **JSONB 承载半结构化字段**
   例如 sections（四段式结果）、citations（引用列表）——
   结构会演进，用 JSONB 避免频繁改表；需要检索的字段才提升为独立列。

5. **敏感字段不落 ORM 的明文日志**
   content / user_input 等用户原文禁止出现在 repr 与日志中，
   因此统一覆写 __repr__。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


# --------------------------------------------------------------------------- #
# 自定义类型
# --------------------------------------------------------------------------- #


class VectorType(TypeDecorator):
    """pgvector 的 vector 类型包装（保留自定义入口以便统一管理维度）。

    实际类型来自 pgvector.sqlalchemy.Vector，这里只做一层薄封装，
    目的是让「维度」在整个项目里有唯一来源：settings.embedding_dim。
    """

    impl = Vector
    cache_ok = True

    def __init__(self, dim: int = 1024) -> None:
        super().__init__(dim=dim)


class TZDateTime(TypeDecorator):
    """带时区的时间戳。

    统一在 Python 侧用 UTC aware datetime，读取时自动转 UTC。
    避免「有时存 UTC 有时存本地时间」这类经典 bug。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            # 朴素 datetime 一律当作 UTC 处理，宁可统一也不要混用
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[str]: ARRAY(String),
    }


def _now() -> dt.datetime:
    """当前 UTC 时间。用作 Python 侧默认值（数据库侧另有 server_default）。"""
    return dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------- #
# 用户
# --------------------------------------------------------------------------- #


class User(Base):
    """用户表。

    对应微信小程序用户。wechat_openid 是唯一业务标识，
    openid 只在特定公众号下唯一，因此不设为主键。
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    wechat_openid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    unionid: Mapped[str | None] = mapped_column(String(64))
    nickname: Mapped[str | None] = mapped_column(String(64))
    avatar_url: Mapped[str | None] = mapped_column(Text)

    # active | deleted | banned
    # 软删除：用户行使删除权时置 deleted_at，保留记录用于合规审计
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, default=_now, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, default=_now, onupdate=_now, server_default=func.now()
    )
    deleted_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)

    # 关系
    journal_entries: Mapped[list[JournalEntry]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    profile: Mapped[UserProfile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )

    def __repr__(self) -> str:
        # 刻意不输出 openid / nickname：这些属于用户身份信息
        return f"<User id={self.id!r} status={self.status!r}>"


class UserProfile(Base):
    """用户画像表。

    由 worker 从历史对话中异步抽取：优势、盲点、偏好、长期主题。
    作用是让对话有「连续性」——用户第二次来时，教练记得他的处境。
    """

    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    strengths: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    blind_spots: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    preferences: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    long_term_theme: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, default=_now, onupdate=_now, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="profile")

    def __repr__(self) -> str:
        return f"<UserProfile user_id={self.user_id!r}>"


# --------------------------------------------------------------------------- #
# 情绪日记
# --------------------------------------------------------------------------- #


class JournalEntry(Base):
    """情绪日记条目。

    这是产品「有效干预次数」指标的数据来源之一：
    用户记录情绪 + 执行微行动 = 一次有效干预。
    """

    __tablename__ = "journal_entries"
    __table_args__ = (
        CheckConstraint("intensity BETWEEN 1 AND 5", name="ck_journal_intensity"),
        Index("idx_journal_user_time", "user_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # joy|calm|expectation|gratitude|anxiety|sadness|anger|shame
    emotion: Mapped[str] = mapped_column(String(32), nullable=False)
    intensity: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # work|interpersonal|body|finance|self|future
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    content: Mapped[str | None] = mapped_column(Text)

    occurred_at: Mapped[dt.datetime] = mapped_column(TZDateTime, nullable=False, default=_now)
    created_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, default=_now, server_default=func.now()
    )

    # manual|import|agent
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")

    user: Mapped[User] = relationship(back_populates="journal_entries")

    def __repr__(self) -> str:
        # 不输出 content：用户原文
        return f"<JournalEntry id={self.id!r} emotion={self.emotion!r} intensity={self.intensity}>"


# --------------------------------------------------------------------------- #
# 对话
# --------------------------------------------------------------------------- #


class ChatSession(Base):
    """对话会话。

    summary / summary_state 用于「滚动压缩」：
    轮次变多时，把最早的对话压成结构化摘要，而不是无限增长历史。
    """

    __tablename__ = "chat_sessions"
    __table_args__ = (Index("idx_sessions_user_time", "user_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(128))
    summary: Mapped[str | None] = mapped_column(Text)
    summary_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_cny: Mapped[float] = mapped_column(
        Numeric(10, 6), nullable=False, default=0
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, default=_now, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, default=_now, onupdate=_now, server_default=func.now()
    )

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<ChatSession id={self.id!r} turn_count={self.turn_count}>"


class ChatMessage(Base):
    """单条对话消息。

    同时承担三个职责：
    1. 对话历史（供上下文组装）
    2. 质量分析（sections / citations / intent / safety_verdict）
    3. 成本归因（prompt_version / latency_ms）
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("idx_messages_session_time", "session_id", "created_at"),
        Index("idx_messages_trace", "trace_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # user|assistant|system
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # 四段式解析结果：{empathy, insight, evidence[], action}
    sections: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 引用列表：[{id, chunk_id, title, score, evidence_level}]
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    intent: Mapped[str | None] = mapped_column(String(32))
    # 安全判定：{is_crisis, is_medical, model_used, flags[]}
    safety_verdict: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    prompt_version: Mapped[str | None] = mapped_column(String(32))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, default=_now, server_default=func.now()
    )

    session: Mapped[ChatSession] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        # 不输出 content：用户原文
        return f"<ChatMessage id={self.id!r} role={self.role!r}>"


# --------------------------------------------------------------------------- #
# 模型调用流水
# --------------------------------------------------------------------------- #


class LLMCall(Base):
    """模型调用流水表。

    成本归因的**唯一权威数据源**：
    - 按 feature（chat/intent/guard/...）拆解成本
    - 按 provider / model 对比性价比
    - 缓存命中率（cached=true 的比例）
    - 降级链触发情况（status=fallback）

    生产环境应该定期归档（按月分区或冷备），否则表会无限增长。
    """

    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("idx_llm_calls_time", "created_at"),
        Index("idx_llm_calls_feature_time", "feature", "created_at"),
        Index("idx_llm_calls_trace", "trace_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(64))

    # chat|insight|reflection|intent|guard|summary|embedding|rerank
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    # dashscope|deepseek|vllm|local
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(32))

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    # 首 token 延迟：流式场景下用户感知的真实响应速度
    ttft_ms: Mapped[int | None] = mapped_column(Integer)

    # ok|error|timeout|fallback|cached
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    error_code: Mapped[str | None] = mapped_column(String(64))
    cost_cny: Mapped[float] = mapped_column(Numeric(10, 8), nullable=False, default=0)

    # temperature / max_tokens / attempt 序号等
    request_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, default=_now, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<LLMCall feature={self.feature!r} model={self.model!r} "
            f"status={self.status!r} cost={self.cost_cny}>"
        )


# --------------------------------------------------------------------------- #
# 知识库
# --------------------------------------------------------------------------- #


class KBDocument(Base):
    """知识库文档。

    对应 packages/knowledge/ 下的一个 Markdown 文件。
    content_hash 用于内容变更检测——重新 ingest 时，
    哈希没变就跳过向量化，节省大量算力。
    """

    __tablename__ = "kb_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_path: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    theory: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    # 1~5，5 表示最强证据
    evidence_level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3,
        server_default="3",
    )
    applicable_to: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    contraindications: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    license: Mapped[str] = mapped_column(String(64), nullable=False, default="original")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    doc_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ingested_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    created_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, default=_now, server_default=func.now()
    )

    chunks: Mapped[list[KBChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<KBDocument id={self.id!r} path={self.source_path!r}>"


class KBChunk(Base):
    """知识库分块。

    父子块（small-to-big）结构：
    - 小块（parent_id 为空）：约 256 token，用于向量检索命中
    - 父块（自身即父块）：约 1024 token，命中子块后回溯返回给模型

    embedding 用自定义 Vector 类型，维度由 EMBEDDING_DIM 决定。
    换嵌入模型必须重建索引（维度会变）。
    """

    __tablename__ = "kb_chunks"
    __table_args__ = (
        Index("idx_kb_chunks_doc", "doc_id"),
        Index("idx_kb_chunks_parent", "parent_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[str | None] = mapped_column(String(64))
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 标题层级路径，如 ["认知重构", "操作步骤"]
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    embedding: Mapped[Any] = mapped_column(VectorType(1024), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, default=_now, server_default=func.now()
    )

    document: Mapped[KBDocument] = relationship(back_populates="chunks")

    def __repr__(self) -> str:
        return f"<KBChunk id={self.id!r} doc_id={self.doc_id!r} tokens={self.token_count}>"


# --------------------------------------------------------------------------- #
# BadCase 闭环
# --------------------------------------------------------------------------- #


class BadCase(Base):
    """BadCase 记录表。

    质量闭环的核心：线上问题 → 归因 → 修复 → 沉淀为回归用例 → 关闭。
    字段与 docs/SOP.md 的 SOP-4 一一对应。
    """

    __tablename__ = "bad_cases"
    __table_args__ = (
        CheckConstraint(
            "priority IN ('P0','P1','P2','P3')", name="ck_badcase_priority"
        ),
        Index("idx_badcases_status", "status", "priority", "created_at"),
        Index("idx_badcases_category", "category", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(64))
    message_id: Mapped[str | None] = mapped_column(String(64))

    priority: Mapped[str] = mapped_column(String(2), nullable=False, default="P2")
    # 8 类归因标签：retrieval/prompt/model/safety/format/context/cost/other
    category: Mapped[str | None] = mapped_column(String(32))
    # user_report|manual|auto_rule|monitor
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")

    user_input: Mapped[str | None] = mapped_column(Text)
    ai_output: Mapped[str | None] = mapped_column(Text)

    auto_tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    # open|processing|fixed|wontfix|invalid
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    assignee: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)

    # 提升为黄金集用例后的 id，用于追溯「这个用例是从哪来的」
    promoted_case_id: Mapped[str | None] = mapped_column(String(64))
    # 修复该问题的 PR 编号
    fixed_by: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, default=_now, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, default=_now, onupdate=_now, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<BadCase id={self.id!r} priority={self.priority!r} status={self.status!r}>"


# --------------------------------------------------------------------------- #
# 埋点
# --------------------------------------------------------------------------- #


class TrackingEvent(Base):
    """客户端埋点事件表。

    北极星指标「有效干预次数」= 用户执行了微行动的事件计数。
    只存事件名与参数，**不存对话内容**。
    """

    __tablename__ = "tracking_events"
    __table_args__ = (Index("idx_tracking_name_time", "event_name", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64))
    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    session_id: Mapped[str | None] = mapped_column(String(64))
    platform: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, default=_now, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<TrackingEvent name={self.event_name!r}>"
