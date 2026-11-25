# -*- coding: utf-8 -*-
"""ERP 连接器：对接 zr_aicoding SQLite（兼容 atomLive 表结构）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

try:
    from sqlite_bridge import SqliteConnector
except ImportError:
    from app.connectors.sqlite_bridge import SqliteConnector

_erp = None


class ERPConnector(SqliteConnector):
    def __init__(self, db_path: Optional[Union[Path, str]] = None):
        super().__init__(name="erp", db_path=db_path)


def get_erp(db_path: Optional[Union[Path, str]] = None):
    """获取 ERP 连接器。传入 db_path 时强制重建单例以切换环境。"""
    global _erp
    if db_path is not None:
        if _erp is not None:
            try:
                _erp.disconnect()
            except Exception:
                pass
        _erp = ERPConnector(db_path=db_path)
        return _erp
    if _erp is None:
        _erp = ERPConnector()
    return _erp


def reset_erp() -> None:
    global _erp
    if _erp is not None:
        try:
            _erp.disconnect()
        except Exception:
            pass
    _erp = None
