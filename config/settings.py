# -*- coding: utf-8 -*-
"""
项目全局配置
============
统一管理路径、端口、连接器搜索路径等，避免硬编码散落在各模块中。
"""

from __future__ import annotations

import os
from pathlib import Path

# ─── 项目标识 ───────────────────────────────────────
PROJECT_NAME = "pythonProject_quality_analysis"

# ─── 路径 ───────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
CONFIG_DIR = PROJECT_ROOT / "config"
WEB_DIR = PROJECT_ROOT / "web"
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
OUTPUT_DIR = PROJECT_ROOT / "output"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DOCS_DIR = PROJECT_ROOT / "docs"

# 优先加载项目根目录 .env（QA_ENV 等全局变量）
from config.dotenv_load import load_dotenv  # noqa: E402

DOTENV_PATH = load_dotenv(PROJECT_ROOT / ".env")

for _d in (DATA_DIR, LOG_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ─── 服务 ───────────────────────────────────────────
API_HOST = os.environ.get("QA_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("QA_API_PORT", "8765"))
VERSION = os.environ.get("QA_VERSION", "2026-04-17-v2.0")
# 访问令牌：非空时 /api/* 必须携带（页面可用 ?token= 引导，服务端下发 Cookie）
API_TOKEN = (os.environ.get("QA_API_TOKEN") or "").strip()
# 500 是否回传异常原文（默认只在回环客户端或显式开启时回传）
DEBUG_ERRORS = os.environ.get("QA_DEBUG_ERRORS", "0").strip().lower() in (
    "1", "true", "yes", "on",
)
# CORS：默认同源（不发送跨域头）；独立前端再显式填写 Origin 或 *
CORS_ORIGIN = (os.environ.get("QA_CORS_ORIGIN") or "").strip()
# 生产指向桥接库时须显式允许（见 deploy/env.production.example）
PROD_ALLOW_BRIDGE = os.environ.get("QA_PROD_ALLOW_BRIDGE", "0").strip().lower() in (
    "1", "true", "yes", "on",
)

# ─── 知识库 ─────────────────────────────────────────
KB_V1_PATH = DATA_DIR / "quality_knowledge.db"
KB_V2_PATH = DATA_DIR / "quality_knowledge_v2.db"
LOG_FILE = LOG_DIR / "quality_system.log"

# ─── MES/ERP 测试|生产：改项目根 .env 的 QA_ENV=test|prod ──
from config.env_profiles import (  # noqa: E402
    ACTIVE_ENV,
    ENV_PROFILES,
    get_env_profile,
    resolve_active_paths,
)

# ─── GraphRAG 生产知识图谱（自主学习知识库权威数据源）──
# 公网：http://175.178.238.31 ；内网：http://192.168.20.214
# 根因/对策/工序缺陷名单一律来自该环境 Neo4j，禁止本地编造
GRAPHRAG_BASE_URL = os.environ.get(
    "QA_GRAPHRAG_URL", "http://175.178.238.31"
).rstrip("/")
GRAPHRAG_TIMEOUT = float(os.environ.get("QA_GRAPHRAG_TIMEOUT", "12"))
GRAPHRAG_CACHE_TTL = int(os.environ.get("QA_GRAPHRAG_CACHE_TTL", "300"))

# ─── 领导决策目标阈值（可环境变量覆盖，禁止写死在业务文案里）──
# 报废率目标 / 警戒 / 严重；工序良率目标；用于总览对标与决策简报
TARGET_SCRAP_RATE = float(os.environ.get("QA_TARGET_SCRAP_RATE", "2.0"))
WARN_SCRAP_RATE = float(os.environ.get("QA_WARN_SCRAP_RATE", "5.0"))
CRIT_SCRAP_RATE = float(os.environ.get("QA_CRIT_SCRAP_RATE", "10.0"))
TARGET_PASS_RATE = float(os.environ.get("QA_TARGET_PASS_RATE", "98.0"))
TARGET_PROCESS_YIELD = float(os.environ.get("QA_TARGET_PROCESS_YIELD", "95.0"))
TARGET_IQC_PASS = float(os.environ.get("QA_TARGET_IQC_PASS", "98.0"))
TARGET_FQC_PASS = float(os.environ.get("QA_TARGET_FQC_PASS", "99.0"))
TARGET_DPPM = float(os.environ.get("QA_TARGET_DPPM", "5000"))
TARGET_REWORK_RATE = float(os.environ.get("QA_TARGET_REWORK_RATE", "3.0"))
ALERT_OVERDUE_HOURS = float(os.environ.get("QA_ALERT_OVERDUE_HOURS", "24"))
EIGHT_D_OVERDUE_DAYS = float(os.environ.get("QA_8D_OVERDUE_DAYS", "7"))

# ─── 对外 8D 智能生成服务（随 QA_ENV=test|prod 解析，禁止混用）──
# 测试默认 http://127.0.0.1:8001 ；生产默认 http://175.178.238.31:18880
# 覆盖：QA_TEST_8D_API_BASE / QA_PROD_8D_API_BASE（生产不读 QA_8D_API_BASE）
from config.env_profiles import resolve_eight_d_api  # noqa: E402

_eight_d = resolve_eight_d_api()
EIGHT_D_API_BASE = (_eight_d.get("base") or "http://127.0.0.1:8001").rstrip("/")
EIGHT_D_API_KEY = (_eight_d.get("api_key") or "").strip()
EIGHT_D_API_TIMEOUT = float(os.environ.get("QA_8D_API_TIMEOUT", "180"))
EIGHT_D_API_ENV = _eight_d.get("env") or ACTIVE_ENV

# ─── ERP/MES 登录（zr_aicoding /api/auth/login）──
from config.env_profiles import resolve_erp_auth  # noqa: E402

_erp_auth = resolve_erp_auth()
ERP_AUTH_BASE = (_erp_auth.get("base") or "http://127.0.0.1:8009").rstrip("/")
ERP_AUTH_TIMEOUT = float(os.environ.get("QA_ERP_AUTH_TIMEOUT", "15"))
# 是否强制用户登录（1=需 ERP 账号；0=仅 QA_API_TOKEN / 免登）
REQUIRE_LOGIN = os.environ.get("QA_REQUIRE_LOGIN", "1").strip().lower() in (
    "1", "true", "yes", "on",
)
SESSION_TTL_SECONDS = int(os.environ.get("QA_SESSION_TTL_SECONDS", str(86400 * 7)))
# Cookie Secure 标记：1=强制 Secure；auto=检测到 HTTPS / X-Forwarded-Proto=https 时加 Secure
_COOKIE_SECURE_RAW = (os.environ.get("QA_COOKIE_SECURE") or "auto").strip().lower()
COOKIE_SECURE_MODE = _COOKIE_SECURE_RAW  # "1"/"true"/"yes" | "0"/"false" | "auto"
# 登录页可选企业编码（逗号分隔）
LOGIN_ENTERPRISES = [
    x.strip()
    for x in (os.environ.get("QA_LOGIN_ENTERPRISES") or "江西中软电子有限公司").split(",")
    if x.strip()
]

# ─── ERP / MES 连接器搜索路径（按优先级）─────────────
# 可通过环境变量 QA_CONNECTOR_PATHS 追加，使用 os.pathsep 分隔
_DEFAULT_CONNECTOR_PATHS = [
    str(APP_DIR / "connectors"),
    str(PROJECT_ROOT / "connectors"),
    r"D:\AI助理\数据对接系统",
    r"D:\AI助理\数据对接系统\config",
]

_extra = os.environ.get("QA_CONNECTOR_PATHS", "")
CONNECTOR_SEARCH_PATHS = list(_DEFAULT_CONNECTOR_PATHS)
if _extra:
    CONNECTOR_SEARCH_PATHS.extend(
        p.strip() for p in _extra.split(os.pathsep) if p.strip()
    )
