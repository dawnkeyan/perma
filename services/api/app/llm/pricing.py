"""模型定价表与成本估算。

定价数据会变，这里以「代码内默认值 + 可被数据库覆盖」的方式维护。
上线后应从 `model_pricing` 表读取，支持不重启调整价格。
"""

from __future__ import annotations

from app.llm.schemas import Usage

# 单位：元 / 1K tokens（输入，输出）
PRICING: dict[str, tuple[float, float]] = {
    # provider: (输入单价, 输出单价)
    "qwen-plus": (0.0008, 0.002),
    "qwen-turbo": (0.0003, 0.0006),
    "qwen-max": (0.0024, 0.0096),
    "qwen-plus-latest": (0.0008, 0.002),
    "deepseek-chat": (0.001, 0.002),
    "deepseek-reasoner": (0.0035, 0.014),
    "glm-4-flash": (0.0001, 0.0001),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.018, 0.072),
    # 本地部署：只计电费摊销（按 token 等价折算）
    "lumina-intent-lora": (0.00005, 0.00005),
}

# 本地模型的等效自部署成本（按 GPU 折旧 + 电费摊销到每千 token）
LOCAL_SELF_HOSTED_FALLBACK = (0.0001, 0.0002)


def get_price(model: str) -> tuple[float, float]:
    """返回 (输入单价, 输出单价)，单位 元/1K tokens。"""
    return PRICING.get(model, LOCAL_SELF_HOSTED_FALLBACK)


def estimate_cost(provider: str, model: str, usage: Usage) -> float:
    """估算一次调用的成本（元）。"""
    price_in, price_out = get_price(model)
    cost = (
        usage.prompt_tokens / 1000.0 * price_in
        + usage.completion_tokens / 1000.0 * price_out
    )
    # 本地模型不计云端价格，但保留极小的自部署摊销
    if provider == "local" and model not in PRICING:
        cost *= 1.0
    return round(cost, 8)
