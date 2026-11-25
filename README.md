# pythonProject_quality_analysis

PCB 智能品质分析系统 v2.0 — 专业 PCB 制造品质管理平台：全流程追溯、品质体检、8D 报告、预警与知识库学习。

## 目录结构

```
pythonProject_quality_analysis/
├── run.py                      # 统一启动入口
├── requirements.txt            # Python 依赖
├── config/                     # 全局配置（路径/端口/连接器搜索）
│   └── settings.py
├── app/                        # 应用代码
│   ├── pcb_quality_api.py      # Web API v2.0（主服务）
│   ├── quality_api_server.py   # 旧版 API（兼容）
│   ├── quality_intelligence_core.py
│   ├── quality_intelligence_main.py
│   ├── quality_intelligence_gui.py
│   ├── pdf_generator.py
│   └── connectors/             # 放置 erp_connector / mes_connector
├── web/                        # 前端静态页面
│   ├── pcb_quality_system.html
│   └── trace_report.html
├── scripts/
│   ├── start.sh                # macOS/Linux 启动
│   ├── 启动品质分析系统.py
│   ├── windows/                # Windows bat 脚本
│   └── tools/                  # 调试/校验脚本（功能保留）
├── docs/                       # 设计文档与模板
├── data/                       # SQLite 知识库
├── logs/                       # 运行日志
├── output/                     # 导出的 PDF 报告
└── archive/                    # 历史调试输出归档
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置数据连接器（可选，用于真实 ERP/MES 数据）

将 `erp_connector.py`、`mes_connector.py` 放到：

```
app/connectors/
```

或设置环境变量：

```bash
export QA_CONNECTOR_PATHS="/path/to/connectors"
```

### 3. 启动

```bash
# 推荐：Web API + 自动打开浏览器
python run.py

# 或
./scripts/start.sh

# 不自动打开浏览器
python run.py api --no-browser

# 桌面 GUI
python run.py gui

# 命令行模式
python run.py cli --help
```

访问：http://localhost:8765/pcb_quality_system.html

> 若 `.env` 设置了 `QA_API_TOKEN`，首次打开请带上令牌：`/pcb_quality_system.html?token=<QA_API_TOKEN>`，
> 服务端会写入 HttpOnly Cookie，之后正常访问即可。

## 生产部署

与 MES/ERP **同机**时：API 用 `127.0.0.1:18765`，可选 Nginx `:18780`，**绝不占用 80/443** 及其它业务端口。

```bash
sudo bash deploy/install.sh /opt/pcb-quality   # 默认不启用 Nginx，避免抢端口
# 编辑 /opt/pcb-quality/.env（QA_API_PORT=18765）后
sudo systemctl restart pcb-quality
```

完整步骤见 **[deploy/DEPLOY.md](deploy/DEPLOY.md)**。

## 核心功能

| 功能 | 说明 |
|------|------|
| 品质体检 | 报废/检验/过程/客诉等多维分析 |
| 料号追溯 | 工单、MRB、MES 工序良率 |
| 8D 报告 | 自动生成与管理 |
| 智能预警 | 阈值/趋势预警 |
| 知识库 | 不良模式学习与积累 |
| PDF 导出 | 品质体检报告 |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/dashboard` | 仪表板 |
| GET | `/api/health_check?part=` | 品质体检 |
| GET | `/api/scrap` | 报废分析 |
| GET | `/api/process` | 工序良率 |
| GET | `/api/trace?part=` | 一键追溯 |
| GET | `/api/8d/list` | 8D 列表 |
| GET | `/api/alerts` | 预警列表 |
| GET | `/api/knowledge` | 知识库 |

## 配置项

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `QA_API_HOST` | `127.0.0.1` | 监听地址；非回环地址必须配置 `QA_API_TOKEN`，否则拒绝启动 |
| `QA_API_PORT` | `8765` | 端口 |
| `QA_API_TOKEN` | — | 访问令牌；非空时 `/api/*`、`/download/*` 必须携带（Cookie 或 `X-API-Token` 头） |
| `QA_ENV` | `test` | `test` / `prod` |
| `QA_PROD_ALLOW_BRIDGE` | `0` | 生产仍用桥接库时须设 `1` |
| `QA_CORS_ORIGIN` | 空 | 默认仅同源；独立前端再填 Origin 或 `*` |
| `QA_DEBUG_ERRORS` | `0` | 500 是否回传异常原文（排查用） |
| `QA_CONNECTOR_PATHS` | — | 额外连接器搜索路径（`os.pathsep` 分隔） |

## 调试工具

历史调试脚本完整保留在 `scripts/tools/`，可直接运行：

```bash
cd scripts/tools
python debug_scrap7.py
```

## 停止服务

```bash
# macOS / Linux
lsof -ti :8765 | xargs kill

# Windows
# 关闭 API 窗口，或使用 scripts/windows/重启API服务.bat
```

## 版本

- v2.0.0 — 工程化重构：标准目录、统一入口、配置集中、功能完整保留
- v1.0.0 — 初始追溯 / 8D / 预警 / 学习能力
