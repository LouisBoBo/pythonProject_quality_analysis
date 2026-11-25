# PCB 品质分析系统 — 生产部署指南

目标：Linux 服务器 + systemd 守护 +（可选）Nginx 反代，内网可访问。

## ★ 同机部署端口铁律（与 MES/ERP 同一服务器）

| 用途 | 端口 | 说明 |
|------|------|------|
| 本系统 API | **127.0.0.1:18765** | 仅本机，不直接对公网 |
| 本系统 Nginx（可选） | **:18780** | 对外入口；**禁止用 80/443** |
| 禁止占用 | 80, 443, 1433, 3306, 8080, 8787… | 见 `deploy/ports.py` 黑名单 |

部署前务必检查：

```bash
ss -lntup | grep -E ':(80|443|1433|3306|8080|8787|18765|18780)\b'
```

`install.sh` **默认不装、不启 Nginx**。需要对外入口时用专用脚本（只加 18780，失败回滚）：

```bash
sudo bash deploy/enable-nginx-safe.sh /opt/pcb-quality
```

同机逐步操作见 **[SAFE_DEPLOY_CHECKLIST.md](SAFE_DEPLOY_CHECKLIST.md)**。

## 部署前准备

| 项 | 说明 |
|----|------|
| 服务器 | Linux x86_64，Python ≥ 3.10（可与 MES/ERP 同机） |
| 生产库 | 填写真实 `QA_PROD_ERP_DB` / `QA_PROD_MES_DB` |
| GraphRAG | 生产机可访问 `QA_GRAPHRAG_URL` |
| 端口 | API `127.0.0.1:18765`；可选 Nginx `:18780`（不用 80） |
| 账号 | 安装脚本默认创建系统用户 `pcbqa` |

> 当前若暂时没有独立生产库，可先把路径指到经校验的桥接库，并设 `QA_PROD_ALLOW_BRIDGE=1`。独立库就绪后改为正式路径并将该开关改为 `0`。

## 一键安装（推荐）

在**生产机**上，把本项目拷到临时目录后执行：

```bash
cd /path/to/pythonProject_quality_analysis
sudo bash deploy/install.sh /opt/pcb-quality
```

然后：

```bash
sudo nano /opt/pcb-quality/.env          # 改生产库路径、域名相关
sudo -u pcbqa /opt/pcb-quality/.venv/bin/python /opt/pcb-quality/deploy/preflight_check.py
sudo systemctl restart pcb-quality
sudo systemctl status pcb-quality
```

访问：

- 本机/SSH 隧道：`http://127.0.0.1:18765/pcb_quality_system.html`
- 启用专用 Nginx 后：`http://<服务器IP>:18780/pcb_quality_system.html`

## 手动步骤摘要

1. 安装依赖与 venv：`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
2. 复制 `deploy/env.production.example` → 项目根 `.env` 并填写
3. 跑 `python deploy/preflight_check.py`，必须通过
4. 安装 systemd unit：`deploy/pcb-quality.service`
5. 安装 Nginx：`deploy/nginx-pcb-quality.conf`
6. `systemctl enable --now pcb-quality`

## 常用运维

```bash
sudo systemctl restart pcb-quality
sudo journalctl -u pcb-quality -f
curl -s http://127.0.0.1:18765/api/health | python3 -m json.tool
```

更新代码：

```bash
# 在开发机同步后，生产机再执行 install（默认不删 data/.env）
sudo bash deploy/install.sh /opt/pcb-quality
sudo systemctl restart pcb-quality
```

## 安全说明（当前阶段）

- **尚未做登录/权限**：仅建议部署在**内网**，Nginx 可加 IP 白名单或 basic auth。
- API 默认仅监听 `127.0.0.1`，不直接暴露公网。
- 正式上线独立生产库后，务必将 `QA_PROD_ALLOW_BRIDGE=0`。

## HTTPS（可选）

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d qa.example.com
```

## 验收清单

- [ ] `/api/health` → `success=true`，`erp_connected` / `mes_connected` 为 true
- [ ] `warnings` 为空，或仅有已知情的桥接库提示
- [ ] 总览 / 报废 / 追溯 / 8D 导出 / Excel 导出可用
- [ ] GraphRAG 知识库页能拉到模式
- [ ] `systemctl` 重启后服务自动拉起
