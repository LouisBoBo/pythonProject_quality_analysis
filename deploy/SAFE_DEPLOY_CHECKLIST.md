# 同机谨慎部署清单（对照你当前服务器）

根据端口扫描，当前已占用且**禁止触碰**：

| 端口 | 进程 | 动作 |
|------|------|------|
| 80 / 443 | nginx | 不改配置、不 restart，最多 **reload**（且仅新增 18780） |
| 3306 | mariadbd | 不动 |
| 8080 | docker-proxy | 不动 |
| 8787 | python | 不动（zr-gateway） |

本系统只用：

- `127.0.0.1:18765` — API  
- `:18780` — 可选对外（独立 server，与 80/443 无关）

---

## 阶段 A：只上 API（零影响现有 Web）

在服务器上（代码已放到例如 `/opt/pcb-quality` 或先拷贝再装）：

```bash
# 1. 安装应用（默认不装 nginx、不启用站点、不删 data）
sudo bash deploy/install.sh /opt/pcb-quality

# 2. 编辑环境（必须）
sudo nano /opt/pcb-quality/.env
```

`.env` 关键项：

```env
QA_ENV=prod
QA_API_HOST=127.0.0.1
QA_API_PORT=18765
QA_NGINX_PORT=18780
QA_PROD_ALLOW_BRIDGE=1
QA_PROD_ERP_DB=...   # 你的库路径
QA_PROD_MES_DB=...
```

```bash
# 3. 预检
sudo -u pcbqa /opt/pcb-quality/.venv/bin/python /opt/pcb-quality/deploy/preflight_check.py

# 4. 只启动本服务（不影响 nginx/mysql/docker）
sudo systemctl restart pcb-quality
sudo systemctl status pcb-quality --no-pager

# 5. 验证 API
curl -sS http://127.0.0.1:18765/api/health

# 6. 再确认没误占别人的口
ss -lntup | grep -E ':(80|443|3306|8080|8787|18765|18780)\b'
```

此时访问可用 SSH 隧道（不暴露新端口）：

```bash
# 在你电脑上
ssh -L 18765:127.0.0.1:18765 root@<服务器>
# 浏览器打开 http://127.0.0.1:18765/pcb_quality_system.html
```

**到这里为止：没有改 nginx，对 80/443 零影响。**

---

## 阶段 B：可选 — 增加 :18780 入口（仍不碰 80/443）

确认 API 正常且 `18780` 空闲后：

```bash
ss -lnt | grep 18780 || echo "18780 free"
sudo bash /opt/pcb-quality/deploy/enable-nginx-safe.sh /opt/pcb-quality
```

脚本会：检查配置无 80/443 → API 已监听 → 18780 空闲 → `nginx -t` → 失败回滚 → 成功才 `reload`（不用 restart）。

验证：

```bash
curl -sS http://127.0.0.1:18780/api/health
# 浏览器 http://<服务器IP>:18780/pcb_quality_system.html
ss -lntup | grep -E ':(80|443|18780)\b'   # 80/443 仍应在
```

回滚 Nginx（如需要）：

```bash
sudo rm -f /etc/nginx/sites-enabled/pcb-quality
sudo rm -f /etc/nginx/sites-available/pcb-quality
# 或 conf.d/pcb-quality.conf
sudo nginx -t && sudo systemctl reload nginx
```

---

## 明确不要做的事

- 不要 `systemctl restart nginx`（用 reload）
- 不要改现有 default 站点的 `listen 80/443`
- 不要把 `QA_API_PORT` 设成 80/443/3306/8080/8787
- 不要对 `/opt/pcb-quality` 盲目 `rsync --delete` 覆盖 `.env` / `data/*.db`
