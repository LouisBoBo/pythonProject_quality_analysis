#!/usr/bin/env bash
# PCB 品质分析系统 — Linux 生产机一键安装（需 root）
# 用法：
#   sudo bash deploy/install.sh
#   sudo bash deploy/install.sh /opt/pcb-quality
set -euo pipefail

APP_NAME="pcb-quality"
INSTALL_DIR="${1:-/opt/pcb-quality}"
SERVICE_USER="${PCBQA_USER:-pcbqa}"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 执行: sudo bash deploy/install.sh"
  exit 1
fi

echo "========================================"
echo " 安装 PCB 品质分析 → ${INSTALL_DIR}"
echo " 源码: ${SRC_DIR}"
echo "========================================"

# 系统依赖：默认只装 Python（不装/不动 nginx，避免影响同机现有站点）
# 需要时显式: QA_INSTALL_SYSTEM_PKGS=1 sudo bash deploy/install.sh
if [[ "${QA_INSTALL_SYSTEM_PKGS:-0}" == "1" ]]; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    apt-get install -y python3 python3-venv python3-pip curl
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 python3-pip curl
    python3 -m ensurepip --upgrade || true
  else
    echo "[WARN] 未识别包管理器，请自行安装 python3 / venv"
  fi
else
  echo "[INFO] 跳过 apt/yum 安装（同机安全默认）。缺 Python 时再: QA_INSTALL_SYSTEM_PKGS=1 sudo bash deploy/install.sh"
fi

# 运行用户
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --home "${INSTALL_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}" || true
fi

# 同步代码：默认不 --delete，避免误删生产 data/日志；需要全量镜像时设 QA_RSYNC_DELETE=1
mkdir -p "${INSTALL_DIR}"
RSYNC_OPTS=(-a)
if [[ "${QA_RSYNC_DELETE:-0}" == "1" ]]; then
  RSYNC_OPTS+=(--delete)
  echo "[WARN] QA_RSYNC_DELETE=1：将删除目标多余文件"
fi
rsync "${RSYNC_OPTS[@]}" \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.git' \
  --exclude '.env' \
  --exclude 'logs/*.log' \
  --exclude 'output/*' \
  --exclude 'data/*.db' \
  "${SRC_DIR}/" "${INSTALL_DIR}/"

mkdir -p "${INSTALL_DIR}/data" "${INSTALL_DIR}/logs" "${INSTALL_DIR}/output"

# Python 虚环境
if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  python3 -m venv "${INSTALL_DIR}/.venv"
fi
"${INSTALL_DIR}/.venv/bin/pip" install -U pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

# .env
if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
  cp "${INSTALL_DIR}/deploy/env.production.example" "${INSTALL_DIR}/.env"
  echo "[INFO] 已生成 ${INSTALL_DIR}/.env ，请立即编辑生产库路径"
else
  echo "[INFO] 保留已有 .env"
fi

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
chmod 640 "${INSTALL_DIR}/.env" || true

# systemd
UNIT="/etc/systemd/system/${APP_NAME}.service"
sed "s|/opt/pcb-quality|${INSTALL_DIR}|g; s|User=pcbqa|User=${SERVICE_USER}|g; s|Group=pcbqa|Group=${SERVICE_USER}|g" \
  "${INSTALL_DIR}/deploy/pcb-quality.service" > "${UNIT}"

systemctl daemon-reload
systemctl enable "${APP_NAME}.service"

echo "[INFO] 已跳过 Nginx（同机安全默认，不碰 80/443）。"
echo "       API 起来且 18780 空闲后，再执行:"
echo "       sudo bash ${INSTALL_DIR}/deploy/enable-nginx-safe.sh ${INSTALL_DIR}"

echo
echo "端口约定（同机勿改占）："
echo "  API  127.0.0.1:18765   可选 Nginx :18780"
echo "  已占用勿碰: 80/443(nginx) 3306(mariadb) 8080(docker) 8787(python)"
echo
echo "下一步（建议按顺序，每步确认）："
echo "  1) sudo nano ${INSTALL_DIR}/.env   # QA_API_HOST=127.0.0.1 QA_API_PORT=18765"
echo "  2) sudo -u ${SERVICE_USER} ${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/deploy/preflight_check.py"
echo "  3) sudo systemctl restart ${APP_NAME} && sudo systemctl status ${APP_NAME}"
echo "  4) curl -sS http://127.0.0.1:18765/api/health"
echo "  5) （可选）sudo bash ${INSTALL_DIR}/deploy/enable-nginx-safe.sh ${INSTALL_DIR}"
echo
echo "安装完成（未改动现有 nginx 80/443）。"
