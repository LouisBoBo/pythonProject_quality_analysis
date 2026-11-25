# -*- coding: utf-8 -*-
"""生产库桥接表后台同步：跟随 MES/ERP 动态写入刷新 P_WO / View_P_Inspection 等。"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("PCB-QA")

_lock = threading.Lock()
_state: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "last_ok_at": None,
    "last_error": None,
    "last_stats": None,
    "db_path": None,
    "interval_sec": 600,
}


def get_bridge_sync_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def _resolve_bridge_module(db_path: Path):
    """从 MES 工程文件路径加载 sync_quality_bridge（避免与品质侧 app 包名冲突）。"""
    import importlib.util

    candidates = [
        db_path.parent / "app" / "quality_mes_bridge.py",
        Path("/www/wwwroot/zr-aicoding/backend/app/quality_mes_bridge.py"),
    ]
    for mod_path in candidates:
        if not mod_path.exists():
            continue
        # 让桥接模块内部相对导入可用
        backend = mod_path.parent.parent
        if str(backend) not in sys.path:
            sys.path.append(str(backend))
        spec = importlib.util.spec_from_file_location(
            "zr_quality_mes_bridge",
            str(mod_path),
        )
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, "sync_quality_bridge", None)
        if callable(fn):
            return fn
    raise ImportError("找不到 quality_mes_bridge")


def run_bridge_sync(db_path: Optional[Path | str] = None) -> dict[str, Any]:
    """立即执行一次桥接同步（无演示造数）。"""
    path = Path(db_path or _state.get("db_path") or "")
    if not path.exists():
        raise FileNotFoundError(f"生产库不存在: {path}")
    sync_fn = _resolve_bridge_module(path)
    stats = sync_fn(path, include_demo=False)
    with _lock:
        _state["last_ok_at"] = datetime.now().isoformat(timespec="seconds")
        _state["last_error"] = None
        _state["last_stats"] = stats
        _state["db_path"] = str(path)
    logger.info("生产桥接同步完成: %s", stats)
    return stats


def _loop(db_path: Path, interval_sec: int) -> None:
    # 启动后稍等再跑，避免与服务初始化抢锁
    time.sleep(8)
    while True:
        try:
            with _lock:
                _state["running"] = True
            run_bridge_sync(db_path)
        except Exception as e:
            with _lock:
                _state["last_error"] = str(e)
            logger.warning("生产桥接同步失败: %s", e)
        finally:
            with _lock:
                _state["running"] = False
        time.sleep(max(60, int(interval_sec)))


def start_bridge_sync_worker(
    db_path: Optional[Path | str],
    interval_sec: Optional[int] = None,
) -> bool:
    """生产环境启动后台同步线程。"""
    if not db_path:
        return False
    path = Path(db_path)
    if not path.exists():
        logger.warning("跳过桥接同步线程：库不存在 %s", path)
        return False

    # 默认 10 分钟；可用 QA_BRIDGE_SYNC_SEC 覆盖
    if interval_sec is None:
        try:
            interval_sec = int(os.environ.get("QA_BRIDGE_SYNC_SEC", "600") or 600)
        except Exception:
            interval_sec = 600

    # 显式关闭
    if os.environ.get("QA_BRIDGE_SYNC", "1").strip().lower() in ("0", "false", "off", "no"):
        logger.info("QA_BRIDGE_SYNC=0，不启动桥接同步线程")
        return False

    with _lock:
        if _state.get("enabled"):
            return True
        _state["enabled"] = True
        _state["db_path"] = str(path)
        _state["interval_sec"] = interval_sec

    t = threading.Thread(
        target=_loop,
        args=(path, interval_sec),
        name="qa-bridge-sync",
        daemon=True,
    )
    t.start()
    logger.info("已启动生产桥接同步线程 interval=%ss db=%s", interval_sec, path)
    return True
