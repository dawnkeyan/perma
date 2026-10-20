#!/usr/bin/env bash
# Lumina · 服务器初始化脚本
#
# 用途：在全新的 Ubuntu 服务器上一次性完成环境准备。
#   sudo ./ops/scripts/bootstrap.sh
#
# 安装内容：
#   - Docker + Compose v2
#   - 常用运维工具（jq、curl、htop、postgresql-client）
#   - 目录结构
#   - swap（低配机器防止 OOM）
#   - 基础安全加固（ssh 端口、防火墙）
set -Eeuo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
REPO_DIR="${REPO_DIR:-/opt/lumina}"
SWAP_SIZE="${SWAP_SIZE:-2G}"

log()  { printf '\033[36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m[warn] %s\033[0m\n' "$*"; }
die()  { printf '\033[31m[error] %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "请以 root 运行：sudo $0"
. /etc/os-release || die "无法识别操作系统"
log "系统：$PRETTY_NAME"

# ---------- 1. 依赖 ----------
log "安装基础依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg lsb-release \
    jq htop tree rsync postgresql-client-16 openssl

# ---------- 2. Docker ----------
if ! command -v docker >/dev/null 2>&1; then
    log "安装 Docker"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
    log "Docker 安装完成"
else
    log "Docker 已安装：$(docker --version)"
fi

# ---------- 3. swap ----------
if ! swapon --show | grep -q .; then
    log "创建 ${SWAP_SIZE} swap（缓解内存不足导致的 OOM）"
    fallocate -l "$SWAP_SIZE" /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    sysctl -w vm.swappiness=10 >/dev/null
else
    log "swap 已存在，跳过"
fi

# ---------- 4. 内核参数 ----------
log "调整内核参数"
cat > /etc/sysctl.d/99-lumina.conf <<'EOF'
vm.swappiness = 10
vm.overcommit_memory = 1
fs.file-max = 65535
net.core.somaxconn = 4096
EOF
sysctl --system >/dev/null

# ---------- 5. 部署用户与目录 ----------
log "创建部署用户与目录"
if ! id -u "$DEPLOY_USER" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$DEPLOY_USER"
    usermod -aG docker "$DEPLOY_USER"
    log "已创建用户 $DEPLOY_USER（已加入 docker 组）"
fi
mkdir -p "$REPO_DIR" "$REPO_DIR/ops/data/backups" "$REPO_DIR/ops/certs"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$REPO_DIR"
chmod 750 "$REPO_DIR/ops/certs"

# ---------- 6. 防火墙 ----------
if command -v ufw >/dev/null 2>&1; then
    log "配置防火墙"
    ufw allow 22/tcp >/dev/null 2>&1 || true
    ufw allow 80/tcp  >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    ufw --force enable >/dev/null 2>&1 || true
fi

# ---------- 7. 日志轮转 ----------
log "配置 Docker 日志轮转"
cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
systemctl restart docker

# ---------- 8. 免密部署 ----------
log "配置发布通道免密（仅当提供公钥）"
if [ -n "${DEPLOY_SSH_PUBKEY:-}" ]; then
    install -d -m 700 -o "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
    echo "$DEPLOY_SSH_PUBKEY" > "/home/$DEPLOY_USER/.ssh/authorized_keys"
    chmod 600 "/home/$DEPLOY_USER/.ssh/authorized_keys"
    chown "$DEPLOY_USER:$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh/authorized_keys"
    log "免密已配置"
else
    warn "未提供 DEPLOY_SSH_PUBKEY，跳过免密配置"
fi

log "初始化完成。下一步："
cat <<EOF

  1. 克隆仓库：  sudo -u $DEPLOY_USER git clone <repo-url> $REPO_DIR
  2. 配置环境：  cp .env.example .env  并从密钥库填写敏感项
  3. 首次部署：  DEPLOY_BRANCH=main $REPO_DIR/ops/scripts/deploy.sh
  4. 验证：      $REPO_DIR/ops/scripts/smoke.sh

EOF
