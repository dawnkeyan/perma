#!/usr/bin/env bash
# Lumina · 部署脚本
#
# 用法（在目标服务器上，由 release.yml 通过 SSH 调用）:
#   DEPLOY_TAG=v1.0.0 ./ops/scripts/deploy.sh
#
# 行为：
#   1. 拉取指定 tag 的代码
#   2. 构建并启动新容器（滚动）
#   3. 执行数据库迁移
#   4. 调用 smoke.sh 做冒烟验证
#   5. 失败自动回滚到上一个 tag
set -Eeuo pipefail

log()  { printf '\033[36m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[33m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
die()  { printf '\033[31m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

REPO_DIR="${REPO_DIR:-/opt/lumina}"
REPO_SLUG="${REPO_SLUG:-your-org/lumina}"
BRANCH="${DEPLOY_BRANCH:-main}"
DEPLOY_TAG="${DEPLOY_TAG:-}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"

# 第一个位置参数为环境名（staging / production），缺省从 DEPLOY_ENV 读取
TARGET_ENV="${1:-${DEPLOY_ENV:-staging}}"
case "$TARGET_ENV" in
    staging)    COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}" ;;
    production) COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}" ;;
    *) log "未知环境：$TARGET_ENV，仅支持 staging / production"; exit 1 ;;
esac
ENV_FILE="${ENV_FILE:-.env}"

trap 'die "部署失败，行号 $LINENO"' ERR

# ---------- 0. 前置检查 ----------
command -v docker >/dev/null 2>&1 || die "未找到 docker"
docker compose version >/dev/null 2>&1 || die "未找到 docker compose v2"
[ -f "$ENV_FILE" ] || die "缺少 $ENV_FILE，请先由运维从密钥库下发"

cd "$REPO_DIR" || die "仓库目录不存在：$REPO_DIR"

# ---------- 1. 记录当前版本（用于回滚） ----------
PREVIOUS_TAG="$(git describe --tags --abbrev=0 2>/dev/null || echo 'none')"
log "目标环境：$TARGET_ENV（编排文件：$COMPOSE_FILE）"
log "当前版本：$PREVIOUS_TAG → 目标版本：${DEPLOY_TAG:-<latest branch>}"

# ---------- 2. 拉取代码 ----------
if [ -n "$DEPLOY_TAG" ]; then
    log "拉取 tag $DEPLOY_TAG"
    git fetch --tags --force --quiet
    git checkout --force "tags/$DEPLOY_TAG"
else
    log "拉取分支 $BRANCH"
    git fetch --quiet origin "$BRANCH"
    git checkout --force "origin/$BRANCH"
fi
git clean -ffd -e 'ops/data' -e '.env' -e 'ops/certs'

# ---------- 3. 备份数据库（迁移前必做） ----------
if [ -x "ops/scripts/backup.sh" ]; then
    log "迁移前自动备份"
    ./ops/scripts/backup.sh --pre-migrate || warn "备份失败但继续（请人工确认）"
fi

# ---------- 4. 构建镜像 ----------
log "构建镜像（可能耗时较久）"
docker compose -f "$COMPOSE_FILE" build --pull api worker

# ---------- 5. 数据库迁移 ----------
log "执行数据库迁移"
docker compose -f "$COMPOSE_FILE" run --rm api alembic upgrade head

# ---------- 6. 启动新版本 ----------
log "启动服务"
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans
docker compose -f "$COMPOSE_FILE" ps

# ---------- 7. 健康检查 ----------
log "等待服务就绪（最长 ${HEALTH_TIMEOUT}s）"
if ! timeout "$HEALTH_TIMEOUT" bash -c '
    until curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; do sleep 3; done
'; then
    warn "健康检查超时，开始回滚"
    "$REPO_DIR/ops/scripts/rollback.sh" --to "$PREVIOUS_TAG" || true
    die "部署失败，已回滚到 $PREVIOUS_TAG"
fi

# ---------- 8. 冒烟测试 ----------
log "执行冒烟测试"
if ! "$REPO_DIR/ops/scripts/smoke.sh"; then
    warn "冒烟测试失败，开始回滚"
    "$REPO_DIR/ops/scripts/rollback.sh" --to "$PREVIOUS_TAG" || true
    die "部署失败，已回滚到 $PREVIOUS_TAG"
fi

# ---------- 9. 清理旧镜像 ----------
log "清理悬空镜像"
docker image prune -f --filter "until=168h" >/dev/null 2>&1 || true

log "部署完成：${DEPLOY_TAG:-$BRANCH}"
