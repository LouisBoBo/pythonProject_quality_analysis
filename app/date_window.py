# -*- coding: utf-8 -*-
"""统一分析日期窗口：用户选择的截止日（含当天）→ 右开上界（次日 00:00）。

全系统口径：查询条件一律 `列 >= start AND 列 < end_exclusive(end)`，
避免同一结束日在不同报表里差一天。
"""

from datetime import datetime, timedelta
from typing import Any, Optional


def end_exclusive(end_date: Any) -> Optional[str]:
    """截止日（含当天）→ 次日 00:00；无法解析时原样返回，交由参数校验兜底。"""
    if end_date is None or str(end_date).strip() == "":
        return None
    raw = str(end_date).strip()
    try:
        day = datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return raw
    return (day + timedelta(days=1)).strftime("%Y-%m-%d")


def month_end_inclusive(fiscal_period: str) -> str:
    """YYYYMM → 当月最后一天（含），供内部月窗传参（函数内部再转右开）。"""
    y, m = int(fiscal_period[:4]), int(fiscal_period[4:6])
    nxt = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)
    return (nxt - timedelta(days=1)).strftime("%Y-%m-%d")
