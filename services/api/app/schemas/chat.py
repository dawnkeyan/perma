"""对话相关的 Pydantic 契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Emotion = Literal[
    "joy", "calm", "expectation", "gratitude",
    "fatigue", "anxiety", "sadness", "anger",
]

FeedbackReason = Literal[
    "too_generic", "not_empathetic", "wrong_understanding",
    "no_action", "too_long", "unrelated", "other",
]


class ChatMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatContextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion_window_days: int = Field(default=7, ge=1, le=90)
    include_profile: bool = True


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=4000)
    context: ChatContextIn = Field(default_factory=ChatContextIn)
    idempotency_key: str | None = Field(default=None, max_length=64)

    @field_validator("message")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message 不能为空")
        return v


class CitationOut(BaseModel):
    id: int
    chunk_id: str
    title: str
    score: float
    evidence_level: int
    quote: str
    heading_path: list[str] = Field(default_factory=list)


class ChatSections(BaseModel):
    empathy: str = ""
    insight: str = ""
    evidence: str = ""
    action: str = ""


class ChatResponse(BaseModel):
    message_id: str
    content: str
    sections: ChatSections
    citations: list[CitationOut] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    cost_cny: float = 0.0
    prompt_version: str = ""
    safety: dict[str, Any] = Field(default_factory=dict)
    trace: list[dict[str, Any]] = Field(default_factory=list)


class ChatFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64)
    rating: int = Field(ge=1, le=5)
    reason_code: FeedbackReason | None = None
    comment: str | None = Field(default=None, max_length=1000)


class JournalEntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion: Emotion
    intensity: int = Field(ge=1, le=5)
    tags: list[str] = Field(default_factory=list, max_length=3)
    content: str | None = Field(default=None, max_length=500)
    occurred_at: str | None = None


class JournalEntryOut(BaseModel):
    id: str
    emotion: Emotion
    intensity: int
    tags: list[str]
    content: str | None
    occurred_at: str
    created_at: str
