# -*- coding: utf-8 -*-
"""SQLite 桥接：把品质分析系统的 SQL Server 方言转成 SQLite，对接 zr_aicoding/erp.db。"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("PCB_Quality.Bridge")
_SEEN_SQL_ERRORS: set = set()

DEFAULT_ZR_DB = Path(
    "/Users/hebo/Desktop/中软项目/pythonProject_zr_aicoding/backend/erp.db"
)


def resolve_db_path() -> Path:
    env = os.environ.get("QA_ZR_DB") or os.environ.get("QA_SQLITE_DB")
    if env:
        return Path(env)
    if DEFAULT_ZR_DB.exists():
        return DEFAULT_ZR_DB
    # 相对兄弟目录兜底
    sibling = (
        Path(__file__).resolve().parents[3]
        / "pythonProject_zr_aicoding"
        / "backend"
        / "erp.db"
    )
    return sibling if sibling.exists() else DEFAULT_ZR_DB


def mssql_to_sqlite(sql: str) -> str:
    """将常见 T-SQL 片段转换为 SQLite 可执行 SQL。"""
    s = sql.strip().rstrip(";")

    # ISNULL(a,b) -> IFNULL(a,b)
    s = re.sub(r"\bISNULL\s*\(", "IFNULL(", s, flags=re.IGNORECASE)

    # GETDATE()
    s = re.sub(r"\bGETDATE\s*\(\s*\)", "datetime('now','localtime')", s, flags=re.IGNORECASE)

    # DATEADD(day, -N, x) / DATEADD(day, N, x)
    def _dateadd(m: re.Match) -> str:
        unit = m.group(1).lower()
        n = int(m.group(2))
        expr = m.group(3).strip()
        unit_map = {"day": "days", "month": "months", "year": "years", "hour": "hours"}
        u = unit_map.get(unit, "days")
        sign = f"+{n}" if n >= 0 else str(n)
        return f"datetime({expr}, '{sign} {u}')"

    s = re.sub(
        r"\bDATEADD\s*\(\s*(day|month|year|hour)\s*,\s*(-?\d+)\s*,\s*([^)]+)\)",
        _dateadd,
        s,
        flags=re.IGNORECASE,
    )

    # CONVERT(VARCHAR(n), expr, 120/112)
    def _convert(m: re.Match) -> str:
        length = m.group(1)
        expr = m.group(2).strip()
        style = m.group(3)
        if style == "112":
            return f"strftime('%Y%m%d', {expr})"
        if style == "120" and length == "7":
            return f"strftime('%Y-%m', {expr})"
        if style == "120":
            return f"strftime('%Y-%m-%d', {expr})"
        return f"CAST({expr} AS TEXT)"

    s = re.sub(
        r"\bCONVERT\s*\(\s*VARCHAR\s*\(\s*(\d+)\s*\)\s*,\s*(.+?)\s*,\s*(\d+)\s*\)",
        _convert,
        s,
        flags=re.IGNORECASE,
    )

    # CAST(... AS DECIMAL(p,s)) -> CAST(... AS REAL)
    s = re.sub(
        r"\bAS\s+DECIMAL\s*\(\s*\d+\s*,\s*\d+\s*\)",
        "AS REAL",
        s,
        flags=re.IGNORECASE,
    )

    # SELECT [DISTINCT] TOP N ... -> SELECT [DISTINCT] ... LIMIT N
    top = re.match(r"(?is)^\s*SELECT\s+(DISTINCT\s+)?TOP\s+(\d+)\s+(.+)$", s)
    if top:
        distinct = top.group(1) or ""
        n = top.group(2)
        body = top.group(3)
        s = f"SELECT {distinct}{body}"
        if not re.search(r"\bLIMIT\s+\d+\s*$", s, flags=re.IGNORECASE):
            s = f"{s} LIMIT {n}"

    return s


class SqliteConnector:
    """通用连接器，满足 get_erp()/get_mes() 期望的 .connected / connect / execute。"""

    def __init__(self, name: str = "sqlite", db_path: Optional[Path | str] = None):
        self.name = name
        self.connected = False
        self.db_path: Optional[Path] = Path(db_path) if db_path else None
        self._preferred_path: Optional[Path] = Path(db_path) if db_path else None
        self._conn: Optional[sqlite3.Connection] = None

    def set_db_path(self, db_path: Optional[Path | str]) -> None:
        """切换数据源路径（不断开已有连接；需随后 disconnect + connect）。"""
        self._preferred_path = Path(db_path) if db_path else None

    def connect(self) -> dict:
        try:
            self.db_path = self._preferred_path or resolve_db_path()
            if not self.db_path.exists():
                self.connected = False
                return {"success": False, "error": f"数据库不存在: {self.db_path}"}
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False, timeout=30
            )
            self._conn.row_factory = sqlite3.Row
            # 快速探测桥接表是否已同步
            cur = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='P_WO'"
            )
            if cur.fetchone() is None:
                try:
                    from quality_mes_bridge import sync_quality_bridge  # type: ignore
                    sync_quality_bridge(self.db_path)
                except Exception:
                    # 尝试通过路径导入 zr_aicoding 模块
                    import sys

                    zr_app = self.db_path.parent / "app"
                    if str(zr_app) not in sys.path:
                        sys.path.insert(0, str(zr_app))
                    from quality_mes_bridge import sync_quality_bridge  # type: ignore

                    sync_quality_bridge(self.db_path)
            self.connected = True
            return {"success": True, "db": str(self.db_path)}
        except Exception as e:
            self.connected = False
            return {"success": False, "error": str(e)}

    def disconnect(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self.connected = False

    def execute(self, sql: str, params: Any = None) -> dict:
        if not self.connected or self._conn is None:
            return {"success": False, "error": "未连接", "data": []}
        try:
            translated = mssql_to_sqlite(sql)
            cur = self._conn.execute(translated, params or [])
            if cur.description is None:
                self._conn.commit()
                return {"success": True, "data": [], "row_count": cur.rowcount}
            rows = [dict(r) for r in cur.fetchall()]
            return {"success": True, "data": rows, "row_count": len(rows)}
        except Exception as e:
            # 以前失败被静默吞掉，前端只看到 0 值；这里至少留痕（相同错误只记一次）
            key = (str(e)[:120], sql[:120])
            if key not in _SEEN_SQL_ERRORS and len(_SEEN_SQL_ERRORS) < 200:
                _SEEN_SQL_ERRORS.add(key)
                logger.warning("SQL 执行失败: %s | sql=%s", e, sql[:200])
            return {"success": False, "error": f"{e} | sql={sql[:200]}", "data": []}
