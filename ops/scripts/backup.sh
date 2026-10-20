#!/usr/bin/env bash
# Lumina · 数据库备份
#
# 用法：
#   ./ops/scripts/backup.sh                 # 全量备份
#   ./ops/scripts/backup.sh --pre-migrate   # 迁移前备份（带标记）
#   ./ops/scripts/backup.sh --restore <file>
#
# 输出目录：ops/data/backups/（已在 .gitignore 中排除）
set -Eeuo pipefail

BACKUP_DIR="${BACKUP_DIR:-ops/data/backups}"
PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-lumina}"
PGDATABASE="${PGDATABASE:-lumina}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

log() { printf '\033[36m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

MODE="full"
RESTORE_FILE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --pre-migrate) MODE="pre-migrate"; shift ;;
        --restore)    RESTORE_FILE="${2:-}"; MODE="restore"; shift 2 ;;
        --help|-h)    sed -n '2,12p' "$0"; exit 0 ;;
        *)            die "未知参数：$1" ;;
    esac
done

# ---------- 恢复 ----------
if [ "$MODE" = "restore" ]; then
    [ -f "$RESTORE_FILE" ] || die "备份文件不存在：$RESTORE_FILE"
    log "警告：将覆盖数据库 $PGDATABASE"
    read -r -p "确认恢复？输入 yes 继续：" ans
    [ "$ans" = "yes" ] || die "已取消"
    log "恢复到 $RESTORE_FILE"
    gunzip -c "$RESTORE_FILE" | PGPASSWORD="${PGPASSWORD:-}" psql \
        -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -v ON_ERROR_STOP=1
    log "恢复完成"
    exit 0
fi

# ---------- 备份 ----------
mkdir -p "$BACKUP_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
SUFFIX=""
[ "$MODE" = "pre-migrate" ] && SUFFIX="-premigrate"
OUT="$BACKUP_DIR/lumina-${STAMP}${SUFFIX}.sql.gz"

log "开始备份 $PGDATABASE → $OUT"
PGPASSWORD="${PGPASSWORD:-}" pg_dump \
    -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
    --format=plain --no-owner --no-acl \
    | gzip -9 > "$OUT"

SIZE="$(du -h "$OUT" | cut -f1)"
log "备份完成：$OUT（$SIZE）"

# 记录校验和，便于校验完整性
shasum -a 256 "$OUT" > "${OUT}.sha256"
log "校验和：${OUT}.sha256"

# 清理过期备份
log "清理 ${RETENTION_DAYS} 天前的备份"
find "$BACKUP_DIR" -name 'lumina-*.sql.gz' -type f -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || true

log "完成"
