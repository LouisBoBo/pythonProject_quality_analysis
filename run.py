#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PCB 智能品质分析系统 - 统一启动入口
====================================

用法:
  python run.py              # 启动 Web API（默认）
  python run.py api          # 启动 Web API v2.0
  python run.py legacy-api   # 启动旧版 API
  python run.py gui          # 启动桌面 GUI
  python run.py cli --help   # 命令行模式
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "app") not in sys.path:
    sys.path.insert(0, str(ROOT / "app"))


def cmd_api(open_browser: bool = True) -> None:
    from config.settings import API_PORT
    if open_browser:
        # 延迟打开，给服务一点启动时间
        import threading
        import time

        def _open():
            time.sleep(1.5)
            webbrowser.open(f"http://localhost:{API_PORT}/pcb_quality_system.html")

        threading.Thread(target=_open, daemon=True).start()

    from app.pcb_quality_api import main
    main()


def cmd_legacy_api() -> None:
    from app.quality_api_server import main
    main()


def cmd_gui() -> None:
    from app.quality_intelligence_main import QualityIntelligenceSystem
    from app.quality_intelligence_gui import start_gui
    system = QualityIntelligenceSystem()
    start_gui(system)


def cmd_cli(argv: list[str]) -> None:
    # 转交给 quality_intelligence_main 的 argparse
    sys.argv = ["quality_intelligence_main.py", *argv]
    from app import quality_intelligence_main as qim
    if hasattr(qim, "main"):
        qim.main()
    else:
        raise SystemExit("CLI 入口未找到，请检查 app/quality_intelligence_main.py")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PCB 智能品质分析系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="api",
        choices=["api", "legacy-api", "gui", "cli"],
        help="启动模式（默认: api）",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="启动 API 时不自动打开浏览器",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    # cli 模式需要透传后续参数
    if argv and argv[0] == "cli":
        cmd_cli(argv[1:])
        return

    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    if unknown and args.command != "cli":
        parser.error(f"未知参数: {unknown}")

    if args.command == "api":
        cmd_api(open_browser=not args.no_browser)
    elif args.command == "legacy-api":
        cmd_legacy_api()
    elif args.command == "gui":
        cmd_gui()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
