# -*- coding: utf-8 -*-
"""
MES / ERP 环境配置（工程侧切换）
================================
**不要**在前端界面切换。优先改项目根目录 `.env`：

  QA_ENV=test   # 或 prod

也可：export QA_ENV=prod 后启动。路径类变量见 .env / .env.example。
默认 test 指向现有数据；切换不会删除任何库文件。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

_CONFIG_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _CONFIG_DIR.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_KB_V2 = _DATA_DIR / "quality_knowledge_v2.db"

# 确保直接 import 本模块时也能读到 .env
from config.dotenv_load import load_dotenv  # noqa: E402

load_dotenv(_PROJECT_ROOT / ".env")

# ═══════════════════════════════════════════════════
# ★ 全局切换：项目根 .env 中 QA_ENV=test|prod
# ═══════════════════════════════════════════════════
ACTIVE_ENV = os.environ.get("QA_ENV", "test").strip().lower()  # "test" | "prod"

# 默认桥接库（与历史行为一致）
_DEFAULT_BRIDGE_DB = Path(
    os.environ.get("QA_ZR_DB")
    or os.environ.get("QA_SQLITE_DB")
    or "/Users/hebo/Desktop/中软项目/pythonProject_zr_aicoding/backend/erp.db"
)

# ─── 8D 智能生成入口（与 QA_ENV 一一对应，禁止混用）───
# 测试：本机 8D 服务（pythonProject_8D_report 开发端口，常见 8001）
# 生产：公网 Nginx 入口 http://175.178.238.31:18880（禁止误指本机 8001）
_DEFAULT_TEST_8D_API = "http://127.0.0.1:8001"
_DEFAULT_PROD_8D_API = "http://175.178.238.31:18880"
# ERP/MES 登录（zr_aicoding POST /api/auth/login）；同机默认回环 8009
_DEFAULT_TEST_ERP_AUTH = "http://127.0.0.1:8009"
_DEFAULT_PROD_ERP_AUTH = "http://127.0.0.1:8009"

# ─── 测试环境：现有 MES/ERP 测试数据 + 现有知识库 ───
TEST_ENV: Dict[str, Any] = {
    "label": "测试环境",
    "erp_db_path": str(
        Path(os.environ.get("QA_TEST_ERP_DB", str(_DEFAULT_BRIDGE_DB)))
    ),
    "mes_db_path": str(
        Path(os.environ.get("QA_TEST_MES_DB", str(_DEFAULT_BRIDGE_DB)))
    ),
    "kb_path": str(Path(os.environ.get("QA_TEST_KB", str(_KB_V2)))),
    "graphrag_url": os.environ.get("QA_TEST_GRAPHRAG_URL", "").strip(),
    # 仅读 QA_TEST_*；兼容旧变量 QA_8D_API_BASE 作为测试覆盖
    "eight_d_api_base": (
        os.environ.get("QA_TEST_8D_API_BASE")
        or os.environ.get("QA_8D_API_BASE")
        or _DEFAULT_TEST_8D_API
    ).strip().rstrip("/"),
    "eight_d_api_key": (
        os.environ.get("QA_TEST_8D_API_KEY")
        or os.environ.get("QA_8D_API_KEY")
        or ""
    ).strip(),
    "erp_auth_base": (
        os.environ.get("QA_TEST_ERP_AUTH_BASE")
        or os.environ.get("QA_ERP_AUTH_BASE")
        or _DEFAULT_TEST_ERP_AUTH
    ).strip().rstrip("/"),
}

# ─── 生产环境：请填写生产 MES/ERP 库路径 ───────────
PROD_ENV: Dict[str, Any] = {
    "label": "生产环境",
    "erp_db_path": os.environ.get("QA_PROD_ERP_DB", "").strip(),
    "mes_db_path": os.environ.get("QA_PROD_MES_DB", "").strip(),
    "kb_path": str(
        Path(os.environ.get("QA_PROD_KB", str(_DATA_DIR / "quality_knowledge_prod.db")))
    ),
    "graphrag_url": os.environ.get(
        "QA_PROD_GRAPHRAG_URL",
        os.environ.get("QA_GRAPHRAG_URL", ""),
    ).strip(),
    # 生产只用 QA_PROD_8D_API_BASE，不继承测试的 QA_8D_API_BASE（防指到本机 8001）
    "eight_d_api_base": (
        os.environ.get("QA_PROD_8D_API_BASE") or _DEFAULT_PROD_8D_API
    ).strip().rstrip("/"),
    "eight_d_api_key": (
        os.environ.get("QA_PROD_8D_API_KEY")
        or os.environ.get("QA_8D_API_KEY")
        or ""
    ).strip(),
    # 生产同机部署走回环；若 MES 在远端再设 QA_PROD_ERP_AUTH_BASE
    "erp_auth_base": (
        os.environ.get("QA_PROD_ERP_AUTH_BASE") or _DEFAULT_PROD_ERP_AUTH
    ).strip().rstrip("/"),
}

ENV_PROFILES = {
    "test": TEST_ENV,
    "prod": PROD_ENV,
}


def normalize_env_name(name: Optional[str] = None) -> str:
    key = (name or ACTIVE_ENV or "test").strip().lower()
    if key in ("production", "prd", "live"):
        key = "prod"
    if key in ("testing", "dev", "local"):
        key = "test"
    if key not in ENV_PROFILES:
        key = "test"
    return key


def get_env_profile(env: Optional[str] = None) -> Dict[str, Any]:
    """返回指定或当前激活环境的配置副本。"""
    key = normalize_env_name(env)
    cfg = dict(ENV_PROFILES[key])
    cfg["env"] = key
    return cfg


def resolve_active_paths(env: Optional[str] = None) -> Dict[str, Any]:
    """解析当前环境实际路径；生产未配置时回退默认桥接库并给出 warnings。"""
    key = normalize_env_name(env)
    cfg = get_env_profile(key)
    bridge = _DEFAULT_BRIDGE_DB.resolve()
    erp = (cfg.get("erp_db_path") or "").strip()
    mes = (cfg.get("mes_db_path") or "").strip()
    kb = (cfg.get("kb_path") or "").strip()
    erp_path = Path(erp).expanduser() if erp else bridge
    mes_path = Path(mes).expanduser() if mes else erp_path
    kb_path = Path(kb).expanduser() if kb else _KB_V2
    warnings: List[str] = []
    # 仅当生产未配置 QA_PROD_* 而回退到默认库，或明确使用品质侧本地副本时，才算「桥接回退」
    # 注意：生产 MES 的 erp.db 可同时被 QA_ZR_DB 引用，不能把「路径等于 QA_ZR_DB」当成桥接库。
    using_bridge = False
    try:
        erp_res = str(erp_path.resolve())
        mes_res = str(mes_path.resolve())
    except Exception:
        erp_res = str(erp_path)
        mes_res = str(mes_path)

    def _looks_like_qa_local_copy(p: str) -> bool:
        name = Path(p).name.lower()
        return name in {"erp_quality_bridge.db", "erp_bridge.db"} or (
            "/opt/pcb-quality/data/" in p.replace("\\", "/")
            and name.startswith("erp")
            and "zr-aicoding" not in p.replace("\\", "/")
        )

    if key == "prod":
        if not erp:
            using_bridge = True
            warnings.append(
                "生产环境未配置 QA_PROD_ERP_DB（.env），已回退默认桥接库"
            )
        elif _looks_like_qa_local_copy(erp_res) or _looks_like_qa_local_copy(mes_res):
            using_bridge = True
            warnings.append(
                "生产环境 ERP/MES 仍指向品质侧本地桥接副本；请改为 MES/ERP 生产库路径"
            )
        if not mes:
            warnings.append(
                "生产环境未配置 QA_PROD_MES_DB（.env），已回退 ERP/默认路径"
            )
        if not erp_path.exists():
            warnings.append(f"生产 ERP 库文件不存在: {erp_path}")
        if not mes_path.exists():
            warnings.append(f"生产 MES 库文件不存在: {mes_path}")
    eight_d_base = (cfg.get("eight_d_api_base") or "").strip().rstrip("/")
    if key == "prod":
        # 同机生产常用 127.0.0.1:18801（pcb-8d 本机口）；仅对本机开发口 :8001 告警
        if eight_d_base.rstrip("/").endswith(":8001") or "/8001" in eight_d_base:
            warnings.append(
                f"生产 8D 入口疑似指向本机开发地址（{eight_d_base}）；"
                f"同机请用 http://127.0.0.1:18801，或公网 {_DEFAULT_PROD_8D_API}"
            )
        if not (os.environ.get("QA_PROD_8D_API_BASE") or "").strip():
            # 使用默认公网入口，写进 resolved 便于核对
            pass
    elif key == "test":
        if "18880" in eight_d_base or "175.178.238.31" in eight_d_base:
            warnings.append(
                f"测试 8D 入口指向生产公网（{eight_d_base}）；"
                f"本地调试请用 QA_TEST_8D_API_BASE={_DEFAULT_TEST_8D_API}"
            )

    return {
        "env": key,
        "label": cfg.get("label") or key,
        "erp": erp_path,
        "mes": mes_path,
        "kb": kb_path,
        "graphrag_url": (cfg.get("graphrag_url") or "").strip(),
        "eight_d_api_base": eight_d_base,
        "eight_d_api_key": (cfg.get("eight_d_api_key") or "").strip(),
        "erp_auth_base": (cfg.get("erp_auth_base") or "").strip().rstrip("/"),
        "config": cfg,
        "warnings": warnings,
        "using_bridge_fallback": bool(using_bridge),
        "bridge_db": str(bridge),
    }


def resolve_eight_d_api(env: Optional[str] = None) -> Dict[str, str]:
    """按当前 QA_ENV 解析 8D 服务 base/key（测试/生产严格隔离）。"""
    paths = resolve_active_paths(env)
    return {
        "env": paths["env"],
        "base": (paths.get("eight_d_api_base") or "").rstrip("/"),
        "api_key": (paths.get("eight_d_api_key") or "").strip(),
    }


def resolve_erp_auth(env: Optional[str] = None) -> Dict[str, str]:
    """按当前 QA_ENV 解析 ERP/MES 登录基址。"""
    paths = resolve_active_paths(env)
    return {
        "env": paths["env"],
        "base": (paths.get("erp_auth_base") or "").rstrip("/"),
    }
