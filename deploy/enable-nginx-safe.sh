#!/usr/bin/env bash
# 安全启用 Nginx 反代：只增加 listen 18780，不改 80/443 现有站点。
# 任一步失败则不 reload，并尽量撤回本次新增文件。
#
# 用法：
#   sudo bash deploy/enable-nginx-safe.sh [/opt/pcb-quality]
set -euo pipefail

INSTALL_DIR="${1:-/opt/pcb-quality}"
APP_NAME="pcb-quality"
API_PORT=18765
NGINX_PORT=18780
SRC_CONF="${INSTALL_DIR}/deploy/nginx-pcb-quality.conf"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root: sudo bash deploy/enable-nginx-safe.sh"
  exit 1
fi

echo "========================================"
echo " 安全启用 Nginx（仅 :${NGINX_PORT}）"
echo " 不会修改现有 80/443 站点"
echo "========================================"

if [[ ! -f "${SRC_CONF}" ]]; then
  echo "[FAIL] 找不到配置: ${SRC_CONF}"
  exit 1
fi

if ! command -v nginx >/dev/null 2>&1; then
  echo "[FAIL] 未安装 nginx。请勿用本脚本强装；确认业务方同意后再安装。"
  exit 1
fi

# 1) 禁止配置里出现 listen 80/443
if grep -E '^\s*listen\s+(\[::\]:)?(80|443)\b' "${SRC_CONF}" >/dev/null; then
  echo "[FAIL] 配置含 listen 80/443，已中止（保护现有站点）"
  exit 1
fi
echo "[OK] 配置未声明 80/443"

# 2) API 必须已在本机监听
if ! ss -lnt | grep -qE "127\.0\.0\.1:${API_PORT}\b"; then
  echo "[FAIL] 本机未监听 127.0.0.1:${API_PORT}。请先启动 pcb-quality 服务："
  echo "       systemctl start pcb-quality && curl -s http://127.0.0.1:${API_PORT}/api/health"
  exit 1
fi
echo "[OK] API 已监听 127.0.0.1:${API_PORT}"

# 3) 18780 必须空闲
if ss -lnt | grep -qE ":${NGINX_PORT}\b"; then
  echo "[FAIL] 端口 ${NGINX_PORT} 已被占用，拒绝启用以免冲突："
  ss -lntup | grep -E ":${NGINX_PORT}\b" || true
  exit 1
fi
echo "[OK] 端口 ${NGINX_PORT} 空闲"

# 4) 写入独立站点文件（不碰 default / 现有 80·443）
# 支持：Debian sites-available、conf.d、宝塔 vhost
TARGET=""
BACKUP=""
CREATED_LINK=0
LINK=""
mkdir -p /www/wwwlogs 2>/dev/null || true

if [[ -d /www/server/panel/vhost/nginx ]]; then
  TARGET="/www/server/panel/vhost/nginx/${APP_NAME}-18780.conf"
elif [[ -d /etc/nginx/sites-available ]]; then
  TARGET="/etc/nginx/sites-available/${APP_NAME}"
  LINK="/etc/nginx/sites-enabled/${APP_NAME}"
elif [[ -d /etc/nginx/conf.d ]]; then
  TARGET="/etc/nginx/conf.d/${APP_NAME}.conf"
else
  echo "[FAIL] 未找到 nginx 站点目录（sites-available / conf.d / 宝塔 vhost）"
  exit 1
fi

if [[ -e "${TARGET}" ]]; then
  BACKUP="${TARGET}.bak.$(date +%Y%m%d%H%M%S)"
  cp -a "${TARGET}" "${BACKUP}"
  echo "[OK] 已备份旧配置 → ${BACKUP}"
fi
cp "${SRC_CONF}" "${TARGET}"
# 宝塔环境：日志目录改为 /www/wwwlogs（若配置仍写 /var/log/nginx 则替换）
if [[ -d /www/wwwlogs ]]; then
  sed -i 's|/var/log/nginx/|/www/wwwlogs/|g' "${TARGET}" || true
fi
if [[ -n "${LINK}" && ! -e "${LINK}" ]]; then
  ln -s "${TARGET}" "${LINK}"
  CREATED_LINK=1
fi
echo "[OK] 已写入 ${TARGET}"

rollback() {
  echo "[ROLLBACK] nginx -t 失败，撤回本次变更"
  if [[ -n "${BACKUP}" && -f "${BACKUP}" ]]; then
    mv -f "${BACKUP}" "${TARGET}"
  else
    rm -f "${TARGET}"
  fi
  if [[ "${CREATED_LINK}" -eq 1 && -n "${LINK}" ]]; then
    rm -f "${LINK}"
  fi
}

# 5) 语法检查；失败则回滚，不 reload
if ! nginx -t; then
  rollback
  exit 1
fi
echo "[OK] nginx -t 通过"

# 6) 平滑 reload（不断开现有连接策略由 nginx 自身处理；不用 restart）
systemctl reload nginx
echo "[OK] 已 systemctl reload nginx（非 restart）"

# 7) 验证
sleep 1
if ! ss -lnt | grep -qE ":${NGINX_PORT}\b"; then
  echo "[WARN] reload 后未见 :${NGINX_PORT} 监听，请检查 error.log"
  exit 1
fi
echo "[OK] 已监听 :${NGINX_PORT}"

# 现有 80/443 仍应在
if ss -lnt | grep -qE ':80\b'; then
  echo "[OK] 原 80 仍在监听（未破坏）"
else
  echo "[WARN] 未检测到 :80 监听，请人工确认原站点是否正常"
fi
if ss -lnt | grep -qE ':443\b'; then
  echo "[OK] 原 443 仍在监听（未破坏）"
fi

echo
echo "验证："
echo "  curl -sS http://127.0.0.1:${API_PORT}/api/health"
echo "  curl -sS http://127.0.0.1:${NGINX_PORT}/api/health"
echo "  浏览器: http://<服务器IP>:${NGINX_PORT}/pcb_quality_system.html"
echo
echo "完成。现有 80/443 服务未改配置文件。"
