# -*- coding: utf-8 -*-
"""MES 连接器：对接 zr_aicoding SQLite（兼容 CIMOM 表结构）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

try:
    from sqlite_bridge import SqliteConnector
except ImportError:
    from app.connectors.sqlite_bridge import SqliteConnector

_mes = None


class MESConnector(SqliteConnector):
    def __init__(self, db_path: Optional[Union[Path, str]] = None):
        super().__init__(name="mes", db_path=db_path)


def get_mes(db_path: Optional[Union[Path, str]] = None):
    """获取 MES 连接器。传入 db_path 时强制重建单例以切换环境。"""
    global _mes
    if db_path is not None:
        if _mes is not None:
            try:
                _mes.disconnect()
            except Exception:
                pass
        _mes = MESConnector(db_path=db_path)
        return _mes
    if _mes is None:
        _mes = MESConnector()
    return _mes


def reset_mes() -> None:
    global _mes
    if _mes is not None:
        try:
            _mes.disconnect()
        except Exception:
            pass
    _mes = None
