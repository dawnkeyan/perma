# Lumina · Makefile
# 常用命令入口：make help 查看全部
SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose
COMPOSE_PROD := docker compose -f docker-compose.yml -f docker-compose.prod.yml

.PHONY: help
help: ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------- 初始化 ----------
.PHONY: init
init: ## 首次初始化：复制 env、拉起依赖、初始化数据库
	@test -f .env || (cp .env.example .env && echo "已生成 .env，请填写后重试")
	$(COMPOSE) up -d postgres redis
	@$(MAKE) db-init

.PHONY: bootstrap
bootstrap: ## 本地 Python 环境一键安装（含 dev extras）
	python3 -m venv .venv && . .venv/bin/activate
	$(MAKE) install

.PHONY: install
install: ## 安装 API 依赖
	cd services/api && pip install -e ".[dev]"

.PHONY: db-init
db-init: ## 初始化数据库并创建向量扩展
	$(COMPOSE) run --rm api python -m app.db.init_db

.PHONY: db-migrate
db-migrate: ## 执行数据库迁移
	$(COMPOSE) run --rm api alembic upgrade head

.PHONY: db-seed
db-seed: ## 灌入知识库与演示数据
	$(COMPOSE) run --rm api python -m app.db.seed

# ---------- 开发 ----------
.PHONY: dev-api
dev-api: ## 本地启动 API（热重载）
	cd services/api && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

.PHONY: dev-worker
dev-worker: ## 本地启动异步任务进程
	cd services/api && python -m app.worker

.PHONY: miniapp
miniapp: ## 启动小程序开发服务
	cd apps/miniapp && npm run dev:weapp

# ---------- 质量 ----------
.PHONY: lint
lint: ## 代码风格与静态检查
	cd services/api && ruff check . && ruff format --check .
	cd apps/miniapp && npm run lint

.PHONY: fmt
fmt: ## 自动格式化
	cd services/api && ruff check --fix . && ruff format .
	cd apps/miniapp && npm run format

.PHONY: typecheck
typecheck: ## 类型检查
	cd services/api && mypy app
	cd apps/miniapp && npm run typecheck

.PHONY: test
test: ## 单元测试
	cd services/api && pytest -q --cov=app --cov-report=term-missing

.PHONY: test-e2e
test-e2e: ## 集成测试
	cd services/api && pytest -q -m integration

# ---------- 评测 ----------
.PHONY: eval
eval: ## 跑离线评测（RAGAS + LLM-as-judge）
	$(COMPOSE) run --rm api python -m app.eval.runner --dataset packages/datasets/golden

.PHONY: eval-report
eval-report: ## 生成评测报告到 reports/eval
	$(COMPOSE) run --rm api python -m app.eval.report

# ---------- 知识库 ----------
.PHONY: kb-ingest
kb-ingest: ## 导入心理学知识库到向量库与 BM25
	$(COMPOSE) run --rm api python -m app.rag.ingest --source packages/knowledge

# ---------- 部署 ----------
.PHONY: build
build: ## 构建生产镜像
	$(COMPOSE) build api worker

.PHONY: up-prod
up-prod: ## 以生产编排启动
	$(COMPOSE_PROD) up -d --build

.PHONY: down
down: ## 停止服务
	$(COMPOSE) down

.PHONY: clean
clean: ## 清理构建产物与缓存
	$(COMPOSE) down -v --remove-orphans
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
