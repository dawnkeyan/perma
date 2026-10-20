#!/usr/bin/env bash
# Lumina · 回滚脚本
#
# 用法：
#   ./ops/scripts/rollback.sh --to v1.0.0   # 回滚到指定 tag
#   ./ops/scripts/rollback.sh --previous    # 回滚到上一个 tag（默认）
#   ./ops/scripts/rollback.sh --db <file>   # 同时恢复数据库备份
#
# 原则：
#   - 回滚优先于修复
#   - 数据库回滚需要显式确认（迁移可能不可逆）
set -Eeuo pipefail

REPO_DIR="${REPO_DIR:-/opt/lumina}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"

log()  { printf '\033[36m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[33m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
die()  { printf '\033[31m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

TARGET=""
RESTORE_DB=""
MODE="previous"

while [ $# -gt 0 ]; do
    case "$1" in
        --to)       TARGET="${2:-}"; MODE="to"; shift 2 ;;
        --previous) MODE="previous"; shift ;;
        --db)       RESTORE_DB="${2:-}"; shift 2 ;;
        --help|-h)  sed -n '2,12p' "$0"; exit 0 ;;
        *)          die "未知参数：$1" ;;
    esac
done

cd "$REPO_DIR" || die "仓库目录不存在"

# ---------- 1. 解析目标版本 ----------
if [ "$MODE" = "previous" ]; then
    # 当前所在 tag 的上一个 tag
    CURRENT="$(git describe --tags --exact-match 2>/dev/null || echo '')"
    if [ -n "$CURRENT" ]; then
        TARGET="$(git describe --tags --abbrev=0 "$CURRENT^" 2>/dev/null || echo '')"
    fi
    [ -n "$TARGET" ] || die "无法确定上一个版本，请用 --to <tag> 显式指定"
fi

[ -n "$TARGET" ] || die "未指定回滚目标"

log "回滚目标：$TARGET"
git fetch --tags --force --quiet

if ! git rev-parse "tags/$TARGET" >/dev/null 2>&1; then
    die "tag 不存在：$TARGET"
fi

# ---------- 2. 数据库回滚（可选，需确认） ----------
if [ -n "$RESTORE_DB" ]; then
    warn "将同时回滚数据库，备份文件：$RESTORE_DB"
    read -r -p "确认回滚数据库？输入 yes 继续：" ans
    [ "$ans" = "yes" ] || die "已取消数据库回滚"
    "$REPO_DIR/ops/scripts/backup.sh" --restore "$RESTORE_DB"
else
    warn "未指定 --db，数据库结构保持在新版本；若新版本含破坏性迁移需手动处理"
fi

# ---------- 3. 代码回滚 ----------
git checkout --force "tags/$TARGET"
git clean -ffd -e 'ops/data' -e '.env' -e 'ops/certs'
log "代码已回滚到 $TARGET"

# ---------- 4. 重建并启动 ----------
log "重建镜像"
docker compose -f "$COMPOSE_FILE" build --pull api worker
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

# ---------- 5. 健康检查 ----------
log "等待服务恢复"
if ! timeout "$HEALTH_TIMEOUT" bash -c '
    until curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; do sleep 3; done
'; then
    die "回滚后服务仍未就绪，请人工介入"
fi

# ---------- 6. 冒烟 ----------
if [ -x "ops/scripts/smoke.sh" ]; then
    log "执行冒烟测试"
    "$REPO_DIR/ops/scripts/smoke.sh" || warn "冒烟测试未全部通过，请人工确认"
fi

log "回滚完成：$TARGET"
