# syntax=docker/dockerfile:1.7
# ============================================================
# Lumina API 镜像
# 多阶段构建：依赖层 -> 编译层 -> 运行时层
# ============================================================

ARG PYTHON_VERSION=3.12-slim

# ---------- base ----------
FROM python:${PYTHON_VERSION} AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        build-essential \
        libpq-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

# ---------- builder ----------
FROM base AS builder

COPY services/api/pyproject.toml /app/services/api/pyproject.toml
COPY services/api/app /app/services/api/app

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install /app/services/api

# ---------- runtime ----------
FROM base AS runtime

# 非 root 运行
RUN groupadd -r lumina && useradd -r -g lumina -d /app lumina

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 应用代码
COPY --chown=lumina:lumina services/api/app /app/app
COPY --chown=lumina:lumina packages/prompts /app/packages/prompts
COPY --chown=lumina:lumina packages/knowledge /app/packages/knowledge
COPY --chown=lumina:lumina packages/datasets /app/packages/datasets

RUN mkdir -p /app/reports && chown -R lumina:lumina /app

USER lumina

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
