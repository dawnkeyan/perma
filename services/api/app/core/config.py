"""应用配置。

所有配置通过环境变量注入，禁止在代码中硬编码常量型配置。
使用 pydantic-settings 以获得类型校验 + 单例缓存。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 应用 ----------
    app_name: str = "lumina"
    app_env: Literal["development", "staging", "production", "test"] = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    debug: bool = False
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"
    timezone: str = "Asia/Shanghai"

    # ---------- 数据层 ----------
    database_url: str = "postgresql+psycopg://lumina:lumina@localhost:5432/lumina"
    postgres_pool_size: int = 20
    postgres_max_overflow: int = 10
    db_echo: bool = False

    redis_url: str = "redis://localhost:6379/0"
    redis_cache_ttl: int = 300
    redis_session_ttl: int = 86400

    # ---------- 向量与检索 ----------
    pgvector_table: str = "kb_chunks"
    embedding_dim: int = 1024
    embedding_batch_size: int = 32
    embedding_provider: str = "local"
    embedding_base_url: str = "http://localhost:8001/v1"
    embedding_api_key: str = "EMPTY"
    embedding_model: str = "BAAI/bge-m3"

    search_backend: Literal["elasticsearch", "quickwit", "none"] = "elasticsearch"
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index: str = "lumina-kb"
    quickwit_url: str = "http://localhost:6333"
    rerank_enabled: bool = True
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_base_url: str = "http://localhost:8002"
    rerank_top_n: int = 5
    retrieval_top_k: int = 8
    retrieval_candidate_k: int = 40
    rrf_k: int = 60
    hnsw_ef_search: int = 200

    # ---------- 模型网关 ----------
    llm_provider: str = "openai"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen-plus"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048
    llm_timeout: int = 60
    llm_max_retries: int = 3
    llm_fallback_models: list[str] = Field(default_factory=list)
    llm_reasoning_model: str = ""
    llm_reasoning_temperature: float = 0.3
    llm_local_enabled: bool = False
    llm_local_base_url: str = "http://localhost:8003/v1"
    llm_local_api_key: str = "EMPTY"
    llm_local_model: str = "lumina-intent-lora"

    # ---------- ModelScope（魔搭）----------
    # 用于拉取基座模型 / 推送微调产物 / vLLM 直接从魔搭加载权重
    # Token 在 https://modelscope.cn 右上角头像 → 访问令牌 获取
    modelscope_token: str = ""
    modelscope_endpoint: str = "https://www.modelscope.cn/api/v1"
    modelscope_cache_dir: str = "~/.cache/modelscope"
    # 微调产物在魔搭上的模型仓库 ID（形如 组织名/模型名）
    modelscope_repo_id: str = ""
    # 本地推理服务的模型来源：modelscope 表示从魔搭拉权重，其余走本地目录
    local_model_source: Literal["modelscope", "local", "huggingface"] = "modelscope"
    local_model_id: str = "Qwen/Qwen2.5-1.5B-Instruct"

    # 语义缓存
    semantic_cache_enabled: bool = True
    semantic_cache_threshold: float = 0.95
    semantic_cache_ttl: int = 1800

    # 成本护栏
    llm_daily_budget_cny: float = 200.0
    llm_user_daily_budget_cny: float = 5.0
    llm_max_attempts: int = 3

    # ---------- Prompt ----------
    prompt_dir: str = "../../packages/prompts"

    # ---------- 安全 ----------
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7
    cors_origins: list[str] = Field(default_factory=list)
    rate_limit_per_min: int = 60
    safety_crisis_detection: bool = True
    safety_crisis_keywords: list[str] = Field(
        default_factory=lambda: [
            "自杀", "自残", "轻生", "不想活", "结束生命", "伤害自己", "活不下去",
        ]
    )
    safety_crisis_hotline: str = "全国心理援助热线 12356"

    # ---------- 观测 ----------
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    prometheus_enabled: bool = True
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # ---------- 评测 ----------
    eval_dataset_dir: str = "../../packages/datasets/golden"
    eval_batch_size: int = 20
    eval_concurrency: int = 4
    eval_fail_threshold: float = 0.15

    @field_validator("cors_origins", "llm_fallback_models", "safety_crisis_keywords", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """允许环境变量用逗号分隔列表。"""
        if isinstance(v, str) and not v.strip().startswith("["):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def effective_llm_fallbacks(self) -> list[str]:
        """降级链 = 主模型 -> 备用模型 -> 本地模型。"""
        chain = [self.llm_model, *self.llm_fallback_models]
        if self.llm_local_enabled and self.llm_local_model:
            chain.append(self.llm_local_model)
        # 去重且保持顺序
        seen: set[str] = set()
        return [m for m in chain if m and not (m in seen or seen.add(m))]


@lru_cache
def get_settings() -> Settings:
    """配置单例。测试中用 get_settings.cache_clear() 重置。"""
    return Settings()


settings = get_settings()
