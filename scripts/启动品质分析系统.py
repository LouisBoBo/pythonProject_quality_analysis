# -*- coding: utf-8 -*-
"""
PCB品质分析系统 v2.0 启动器
- 自动检测并清理旧进程
- 等待服务就绪
- 打开浏览器
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import API_PORT, PROJECT_ROOT  # noqa: E402


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def kill_process_on_port(port: int) -> None:
    if sys.platform.startswith("win"):
        result = subprocess.run(
            f'netstat -ano | findstr ":{port}" | findstr "LISTENING"',
            shell=True, capture_output=True, text=True
        )
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split()
            if parts and parts[-1] != "0":
                pid = parts[-1]
                print(f"  终止旧进程 PID: {pid}")
                subprocess.run(f"taskkill /F /PID {pid}", shell=True)
    else:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True
        )
        for pid in result.stdout.strip().split():
            if pid:
                print(f"  终止旧进程 PID: {pid}")
                subprocess.run(["kill", "-9", pid])


def wait_for_api(port: int, timeout: int = 30):
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.urlopen(f"http://localhost:{port}/api/health", timeout=3)
            data = json.loads(req.read().decode())
            if data.get("success"):
                return True, data
        except Exception:
            pass
        time.sleep(1)
    return False, None


def main() -> bool:
    port = API_PORT
    print("=" * 50)
    print("  PCB品质分析系统 v2.0 启动器")
    print("=" * 50)
    print()

    if is_port_in_use(port):
        print(f"[1/4] 端口 {port} 被占用，正在清理...")
        kill_process_on_port(port)
        time.sleep(2)
    else:
        print(f"[1/4] 端口 {port} 可用")

    print("[2/4] 启动API服务...")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "run.py"), "api", "--no-browser"],
            cwd=str(PROJECT_ROOT),
        )
        print(f"     进程已启动 PID: {proc.pid}")
    except Exception as e:
        print(f"[错误] 无法启动服务: {e}")
        return False

    print("[3/4] 等待服务就绪...")
    ready, data = wait_for_api(port, timeout=25)

    print("[4/4] 验证服务状态...")
    if ready:
        print()
        print("=" * 50)
        print("  ✅ 系统启动成功！")
        print("=" * 50)
        print()
        print(f"  API地址: http://localhost:{port}")
        print(f"  ERP连接: {'✅ 已连接' if data.get('erp_connected') else '❌ 未连接'}")
        print(f"  MES连接: {'✅ 已连接' if data.get('mes_connected') else '❌ 未连接'}")
        print()
        print("  正在打开浏览器...")
        time.sleep(0.5)
        url = f"http://localhost:{port}/pcb_quality_system.html"
        if sys.platform == "darwin":
            subprocess.run(["open", url])
        elif sys.platform.startswith("win"):
            subprocess.run(f"start {url}", shell=True)
        else:
            subprocess.run(["xdg-open", url])
        return True

    print()
    print("=" * 50)
    print("  ⚠️  API服务已启动，但健康检查超时")
    print("=" * 50)
    return False


if __name__ == "__main__":
    main()
    print()
    try:
        input("按回车键退出...")
    except EOFError:
        pass
