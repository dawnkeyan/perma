"""Prompt 注册中心。

职责：
1. 从 `packages/prompts/**.yaml` 加载 Prompt 定义
2. 按 `name@version` 精确获取，或取某个 name 的 active 版本
3. 用 Jinja 渲染变量
4. 记录版本元信息（changelog、评测分数），供线上追溯

**为什么 Prompt 要版本化而不是直接改代码？**
因为 Prompt 是「模型行为的一部分」，它的变更等同于代码变更，
需要评测门禁、需要回滚、需要 A/B 对比。
把 Prompt 放在 YAML 里只是第一步，真正的价值在于**版本号 + 评测分数 + 线上可追溯**。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined, Template

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class PromptMeta:
    name: str
    version: int
    description: str = ""
    changelog: str = ""
    model_scope: list[str] = field(default_factory=list)
    eval_scores: dict[str, float] = field(default_factory=dict)
    status: str = "draft"
    template: str = ""
    content_hash: str = ""

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(slots=True)
class Prompt:
    """渲染后的 Prompt。"""

    meta: PromptMeta
    body: str
    variables: dict[str, Any] = field(default_factory=dict)

    @property
    def version_key(self) -> str:
        return self.meta.key


class PromptRegistry:
    """Prompt 仓库。"""

    def __init__(self, prompt_dir: str | Path | None = None) -> None:
        self.prompt_dir = Path(prompt_dir or settings.prompt_dir)
        self._cache: dict[str, PromptMeta] = {}
        self._loaded = False

    # ------------------------------------------------------------------ #
    # 加载
    # ------------------------------------------------------------------ #
    def load(self, *, force: bool = False) -> None:
        """扫描目录加载所有 Prompt。"""
        if self._loaded and not force:
            return

        self._cache.clear()
        root = self.prompt_dir
        if not root.exists():
            logger.warning("prompt_dir_missing", path=str(root))
            self._loaded = True
            return

        for path in sorted(root.rglob("*.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                logger.error("prompt_parse_failed", path=str(path), error=str(exc))
                continue
            if not isinstance(raw, dict):
                continue

            name = str(raw.get("name") or path.stem)
            version = int(raw.get("version") or 1)
            body = str(raw.get("template") or raw.get("body") or "")

            meta = PromptMeta(
                name=name,
                version=version,
                description=str(raw.get("description") or ""),
                changelog=str(raw.get("changelog") or ""),
                model_scope=list(raw.get("model_scope") or []),
                eval_scores=dict(raw.get("eval_scores") or {}),
                status=str(raw.get("status") or "active"),
                template=body,
                content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest()[:16],
            )
            self._cache[meta.key] = meta

        logger.info("prompts_loaded", count=len(self._cache), dir=str(root))
        self._loaded = True

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def get(self, name: str, version: int | None = None) -> PromptMeta:
        """获取指定版本的 Prompt 元信息。"""
        self.load()
        if version is None:
            active = self._active_version(name)
            if active is None:
                raise KeyError(f"未找到 active 版本的 prompt: {name}")
            version = active
        key = f"{name}@{version}"
        if key not in self._cache:
            raise KeyError(f"未找到 prompt: {key}")
        return self._cache[key]

    def _active_version(self, name: str) -> int | None:
        candidates = [m for m in self._cache.values() if m.name == name]
        if not candidates:
            return None
        actives = [m for m in candidates if m.status == "active"]
        pool = actives or candidates
        return max(m.version for m in pool)

    def list_versions(self, name: str) -> list[PromptMeta]:
        self.load()
        return sorted(
            (m for m in self._cache.values() if m.name == name),
            key=lambda m: m.version,
        )

    def render(self, name: str, variables: dict[str, Any] | None = None, version: int | None = None) -> Prompt:
        """渲染 Prompt。"""
        meta = self.get(name, version)
        variables = variables or {}
        try:
            body = Template(meta.template, undefined=StrictUndefined).render(**variables)
        except Exception as exc:
            raise ValueError(f"Prompt 渲染失败 ({meta.key}): {exc}") from exc
        return Prompt(meta=meta, body=body, variables=variables)

    def reload(self) -> None:
        """热加载（开发环境用）。"""
        self.load(force=True)


_registry: PromptRegistry | None = None


def get_registry() -> PromptRegistry:
    global _registry
    if _registry is None:
        _registry = PromptRegistry()
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None
