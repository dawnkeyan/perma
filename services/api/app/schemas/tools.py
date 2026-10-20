"""工具相关的 Pydantic 契约。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ToolStep(BaseModel):
    """工具流程中的一步。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=32)
    hint: str = Field(default="", max_length=200)


class ToolOut(BaseModel):
    """对外下发的工具定义。

    刻意**不下发** `focus`（检索侧重）：那是服务端内部的检索策略，
    暴露出去等于把检索实现细节交给客户端，以后改检索就得考虑客户端兼容。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    icon: str
    entry: str
    starter_prompt: str
    steps: list[ToolStep]
    recommended_for: list[str]
