"""工具目录 API。

客户端（小程序首页）通过这个接口拿到工具卡片列表。
之所以走接口而不是把文案硬编码在前端：
产品改一句话不需要发版，前端也不必跟着改。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.schemas.tools import ToolOut, ToolStep
from app.tools.catalog import ToolSpec, list_tools, tools_for_emotion

router = APIRouter()


def _to_out(spec: ToolSpec) -> ToolOut:
    """内部目录结构 -> 对外契约。

    注意 `focus`（检索侧重）**不下发**：那是服务端的检索策略，
    暴露出去会让客户端与检索实现耦合。
    """
    return ToolOut(
        id=spec.id,
        name=spec.name,
        description=spec.description,
        icon=spec.icon,
        entry=spec.entry,
        starter_prompt=spec.starter_prompt,
        steps=[ToolStep(title=s.title, hint=s.hint) for s in spec.steps],
        recommended_for=list(spec.recommended_for),
    )


@router.get("", response_model=list[ToolOut])
async def list_available_tools(
    emotion: str | None = Query(
        default=None,
        description="可选：当前情绪，用于把推荐工具排到前面",
    ),
) -> list[ToolOut]:
    """列出可用工具。

    带 `emotion` 时，返回顺序按「当前情绪推荐」排前——首页据此置顶展示。
    """
    specs = tools_for_emotion(emotion) if emotion else list_tools()
    return [_to_out(spec) for spec in specs]
