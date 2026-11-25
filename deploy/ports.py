# -*- coding: utf-8 -*-
"""
同机部署端口策略（与 MES/ERP 共用服务器时强制遵守）
================================================
本系统只使用「专用高位端口」，禁止占用 Web/DB/中间件等常见端口。
"""

from __future__ import annotations

# 本系统推荐端口（生产默认）
QA_DEFAULT_API_PORT = 18765          # API 仅监听 127.0.0.1
QA_DEFAULT_NGINX_PORT = 18780        # 对外 HTTP（不用 80）

# 禁止作为本系统 API / Nginx listen 的端口（同机 MES/ERP/常见服务）
# 注意：绝不占用 80/443，以免干扰机上已有 Web/MES 入口
FORBIDDEN_PORTS = frozenset({
    # Web（同机多半已被 MES/ERP/门户占用）
    80, 81, 443, 8000, 8001, 8080, 8081, 8443, 8888, 9000, 9090,
    # 中软桥接网关等
    8787,   # zr-gateway
    # 数据库
    1433, 1434,   # SQL Server
    1521,         # Oracle
    3306, 3307,   # MySQL
    5432,         # PostgreSQL
    27017,        # MongoDB
    # 缓存/消息
    6379, 11211, 5672, 15672, 9092,
    # 远程管理
    22, 3389, 5900,
})

# 生产推荐高位端口（预检会警告低于此值，但不因 8765 开发口直接失败）
MIN_SAFE_PROD_PORT = 10000


def is_forbidden_api_port(port: int, *, strict_prod: bool = True) -> str | None:
    """若端口在黑名单则返回原因，否则 None。strict_prod 保留参数兼容调用方。"""
    del strict_prod  # 黑名单对开发/生产一致；高位建议由预检 WARN 提示
    try:
        p = int(port)
    except Exception:
        return "端口不是有效整数"
    if p <= 0 or p > 65535:
        return f"端口越界: {p}"
    if p in FORBIDDEN_PORTS:
        return (
            f"端口 {p} 属于保留/常见服务端口，禁止占用"
            f"（同机部署勿抢 MES/ERP/DB/Web）。请改用 {QA_DEFAULT_API_PORT}"
        )
    return None


def suggest_high_port(port: int) -> str | None:
    """生产建议使用高位端口；返回提示文案或 None。"""
    try:
        p = int(port)
    except Exception:
        return None
    if p < MIN_SAFE_PROD_PORT:
        return (
            f"当前 API 端口 {p} < {MIN_SAFE_PROD_PORT}；"
            f"同机生产建议改用 {QA_DEFAULT_API_PORT}"
        )
    return None


def is_forbidden_nginx_port(port: int) -> str | None:
    """Nginx 对外端口校验（同样禁止 80/443 等）。"""
    return is_forbidden_api_port(port)
