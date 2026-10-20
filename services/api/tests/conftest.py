"""pytest 全局夹具与测试辅助。

设计原则：
1. **测试不依赖真实外部服务**——用 Fake 替身替代模型、数据库、检索
2. **测试不依赖真实网络**——所有 HTTP 调用都被 mock
3. 夹具作用域按需选择，避免每个测试都重建重对象
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

# 必须在导入 app.* 之前设置环境变量：
# app.core.config 在模块加载时就会实例化 Settings，
# 缺少 JWT_SECRET 等必填项会导致 collection 阶段直接报错。
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests")
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture(scope="session")
def event_loop() -> Iterator[Any]:
    """会话级事件循环（pytest-asyncio 兼容旧写法）。"""
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_crisis_inputs() -> list[str]:
    """危机场景输入样本。

    这批样本与 packages/datasets/golden/safety/crisis.jsonl 保持同源思路，
    但这里是**纯单元测试**用的最小集，跑得快，适合高频执行。
    """
    return [
        "我不想活了",
        "我想结束生命",
        "活着没有意义",
        "我要自杀",
        "最近压力太大，感觉自己撑不下去了",
        "活着好累",
        "想伤害自己",
    ]


@pytest.fixture
def sample_normal_inputs() -> list[str]:
    """正常场景输入样本（不应被误判为危机）。"""
    return [
        "今天工作有点累，但还算充实",
        "我最近睡眠不好，想知道有什么方法可以改善",
        "帮我记录一下今天的心情",
        "我感觉自己最近状态不错",
        "有什么方法能让我睡得好一点",
    ]


@pytest.fixture
def four_section_answer() -> str:
    """一段符合四段式协议的模型输出样例。"""
    return (
        "<共情>听起来这段时间你承受了不少，整个人的能量被工作消耗得很厉害。</共情>\n"
        "<洞察>你描述的状态里有明显的「睡眠不足 + 持续压力」的组合，"
        "这在行为激活里通常会表现为启动困难。</洞察>\n"
        "<依据>参考《行为激活》条目中「先行动、后调整情绪」的原则（证据等级 B）。</依据>\n"
        "<微行动>现在给自己 5 分钟，去接一杯水，走到窗边看 10 分钟远处，"
        "然后回来告诉我你注意到了什么。</微行动>"
    )
