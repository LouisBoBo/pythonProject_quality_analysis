#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生产启动预检：路径 / 库文件 / GraphRAG / 端口。
退出码：0=通过；1=失败（systemd ExecStartPre 会阻止启动）。
"""

from __future__ import annotations

import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def _warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)


def main() -> int:
    from config.dotenv_load import load_dotenv
    from config.env_profiles import resolve_active_paths
    from config import settings

    load_dotenv(ROOT / ".env")
    paths = resolve_active_paths()
    env = paths.get("env")
    errors: list[str] = []

    print("=" * 56)
    print(" PCB 品质分析 — 生产预检")
    print("=" * 56)
    print(f"环境: {paths.get('label')} ({env})")
    print(f"ERP : {paths['erp']}")
    print(f"MES : {paths['mes']}")
    print(f"KB  : {paths['kb']}")
    print(f"监听: {settings.API_HOST}:{settings.API_PORT}")
    print()

    if env != "prod":
        _warn(f"当前 QA_ENV={env}，非 prod；预检以宽松模式继续")

    # 库文件
    for label, p in (("ERP", paths["erp"]), ("MES", paths["mes"]), ("KB", paths["kb"])):
        if not p.exists():
            errors.append(f"{label} 库不存在: {p}")
        elif p.stat().st_size < 1024:
            errors.append(f"{label} 库过小（可能无效）: {p} ({p.stat().st_size} B)")
        else:
            _ok(f"{label} 库存在 ({p.stat().st_size // 1024} KB)")

    # 桥接库策略
    allow_bridge = os.environ.get("QA_PROD_ALLOW_BRIDGE", "0").strip() in ("1", "true", "yes")
    if env == "prod" and paths.get("using_bridge_fallback"):
        msg = "生产仍指向桥接库（非独立生产库）"
        if allow_bridge:
            _warn(msg + " — 已设置 QA_PROD_ALLOW_BRIDGE=1，允许启动")
        else:
            errors.append(msg + "；请配置 QA_PROD_ERP_DB/MES_DB，或临时设 QA_PROD_ALLOW_BRIDGE=1")

    # 运行目录可写
    for d in (settings.DATA_DIR, settings.LOG_DIR, settings.OUTPUT_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
            test = d / ".write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            _ok(f"可写目录 {d}")
        except Exception as e:
            errors.append(f"目录不可写 {d}: {e}")

    # 端口策略（同机 MES/ERP：禁止抢占常见端口）
    from deploy.ports import (
        FORBIDDEN_PORTS,
        QA_DEFAULT_API_PORT,
        QA_DEFAULT_NGINX_PORT,
        is_forbidden_api_port,
        is_forbidden_nginx_port,
        suggest_high_port,
    )

    port = int(settings.API_PORT)
    reason = is_forbidden_api_port(port, strict_prod=(env == "prod"))
    if reason:
        errors.append(reason)
    else:
        _ok(f"API 端口不在黑名单: {port}（生产推荐 {QA_DEFAULT_API_PORT}）")
        tip = suggest_high_port(port)
        if tip and env == "prod":
            _warn(tip)

    nginx_port = int(os.environ.get("QA_NGINX_PORT") or QA_DEFAULT_NGINX_PORT)
    nreason = is_forbidden_nginx_port(nginx_port)
    if nreason:
        errors.append(f"Nginx 对外端口不合规: {nreason}")
    else:
        _ok(f"Nginx 对外端口策略通过: {nginx_port}（禁止 80/443）")

    print(
        "保留端口黑名单（不可占用）: "
        + ", ".join(str(x) for x in sorted(FORBIDDEN_PORTS)[:14])
        + ", ..."
    )

    # 端口占用：生产默认「已占用则失败」，避免踩掉别人的服务；重启本服务可设 QA_ALLOW_PORT_REUSE=1
    host = settings.API_HOST if settings.API_HOST not in ("0.0.0.0", "") else "127.0.0.1"
    allow_reuse = os.environ.get("QA_ALLOW_PORT_REUSE", "0").strip().lower() in ("1", "true", "yes")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        in_use = sock.connect_ex((host, port)) == 0
    finally:
        sock.close()
    if in_use:
        msg = f"端口 {host}:{port} 已被占用"
        if allow_reuse:
            _warn(msg + " — 已设 QA_ALLOW_PORT_REUSE=1，允许继续（请确认是本服务）")
        else:
            errors.append(
                msg + "。同机部署禁止抢占；请换 QA_API_PORT，或确认是本服务重启时设 QA_ALLOW_PORT_REUSE=1"
            )
    else:
        _ok(f"端口 {host}:{port} 当前空闲")

    # 顺带探测 Nginx 对外口是否空闲（仅提示）
    ns = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ns.settimeout(1)
    try:
        if ns.connect_ex(("127.0.0.1", nginx_port)) == 0:
            _warn(f"对外端口 {nginx_port} 已有监听，启用 Nginx 配置前请先确认归属")
        else:
            _ok(f"对外端口 {nginx_port} 当前空闲")
    finally:
        ns.close()

    # GraphRAG
    gr_url = (paths.get("graphrag_url") or os.environ.get("QA_GRAPHRAG_URL") or "").rstrip("/")
    if not gr_url:
        _warn("未配置 GraphRAG URL，知识库/8D 对齐能力会降级")
    else:
        try:
            req = urllib.request.Request(gr_url + "/", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                _ok(f"GraphRAG 可达 {gr_url} (HTTP {resp.status})")
        except Exception as e:
            _warn(f"GraphRAG 暂不可达 {gr_url}: {e}（服务可启，知识检索可能失败）")

    # 关键依赖
    for mod in ("reportlab", "openpyxl"):
        try:
            __import__(mod)
            _ok(f"依赖 {mod}")
        except ImportError:
            errors.append(f"缺少依赖 {mod}，请 pip install -r requirements.txt")

    print()
    if errors:
        for e in errors:
            _fail(e)
        print("预检失败，请修复后重试。")
        return 1

    print("预检通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
