# -*- coding: utf-8 -*-
"""
项目环境（只读封装）
====================
环境在工程配置 config/env_profiles.py（或 QA_ENV 等环境变量）中切换，
本模块仅解析当前激活环境，供连接器与 API 启动时使用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from config.env_profiles import (
    ACTIVE_ENV,
    ENV_PROFILES,
    get_env_profile,
    normalize_env_name,
    resolve_active_paths,
)


_SECRET_CONFIG_KEYS = frozenset({
    "eight_d_api_key",
    "api_key",
    "password",
    "secret",
    "token",
})


def _public_config(cfg: Dict[str, Any] | None) -> Dict[str, Any]:
    """对外暴露的配置副本：剔除密钥类字段。"""
    out: Dict[str, Any] = {}
    for k, v in (cfg or {}).items():
        lk = str(k).lower()
        if lk in _SECRET_CONFIG_KEYS or lk.endswith("_key") or lk.endswith("_secret"):
            continue
        out[k] = v
    return out


def get_active_context() -> Dict[str, Any]:
    paths = resolve_active_paths()
    return {
        "project_id": "default",
        "project_name": "默认项目",
        "env": paths["env"],
        "env_label": paths["label"],
        "config": _public_config(paths.get("config")),
        "active_env_source": ".env 的 QA_ENV（或系统环境变量）",
    }


def resolve_paths(env_cfg: Dict[str, Any] | None = None) -> Dict[str, Path]:
    """兼容旧调用：忽略传入 cfg，一律按工程激活环境解析。"""
    paths = resolve_active_paths()
    return {"erp": paths["erp"], "mes": paths["mes"], "kb": paths["kb"]}


def describe_for_api() -> Dict[str, Any]:
    paths = resolve_active_paths()
    return {
        "project_id": "default",
        "project_name": "默认项目",
        "env": paths["env"],
        "env_label": paths["label"],
        # 禁止把 eight_d_api_key 等密钥回传前端
        "config": _public_config(paths.get("config")),
        "resolved": {
            "erp_db_path": str(paths["erp"]),
            "mes_db_path": str(paths["mes"]),
            "kb_path": str(paths["kb"]),
            "erp_exists": paths["erp"].exists(),
            "mes_exists": paths["mes"].exists(),
            "kb_exists": paths["kb"].exists(),
            "using_bridge_fallback": bool(paths.get("using_bridge_fallback")),
            "bridge_db": paths.get("bridge_db"),
            "eight_d_api_base": paths.get("eight_d_api_base") or "",
            "erp_auth_base": paths.get("erp_auth_base") or "",
            "graphrag_url": paths.get("graphrag_url") or "",
            "eight_d_api_key_configured": bool(
                (paths.get("eight_d_api_key") or "").strip()
            ),
        },
        "warnings": list(paths.get("warnings") or []),
        "env_keys": list(ENV_PROFILES.keys()),
        "active_env_setting": ACTIVE_ENV,
        "howto": (
            "在项目根 .env 中设置 QA_ENV=test|prod 后重启服务；"
            "也可 export QA_ENV=...；前端不提供切换。"
            "生产请显式填写 QA_PROD_ERP_DB / QA_PROD_MES_DB。"
            "8D：测试 QA_TEST_8D_API_BASE（默认本机:8001）；"
            "生产 QA_PROD_8D_API_BASE（默认 http://175.178.238.31:18880）。"
        ),
    }


# 保留符号供旧 import 不报错（运行时切换已废弃）
def set_active_env(*_a, **_k):
    raise RuntimeError(
        "环境请在工程配置中切换（config/env_profiles.py 或 QA_ENV），不支持运行时/前端切换"
    )


def update_project_env(*_a, **_k):
    raise RuntimeError("请直接编辑 config/env_profiles.py 中的 TEST_ENV / PROD_ENV")


def list_projects():
    return [{"id": "default", "name": "默认项目", "active_env": normalize_env_name()}]


def get_project(_project_id=None):
    ctx = get_active_context()
    return {
        "id": "default",
        "name": "默认项目",
        "active_env": ctx["env"],
        "environments": {
            k: _public_config(dict(v)) for k, v in ENV_PROFILES.items()
        },
    }


def upsert_project(*_a, **_k):
    raise RuntimeError("多项目请在 config/env_profiles.py 扩展，不支持 API 写入")
