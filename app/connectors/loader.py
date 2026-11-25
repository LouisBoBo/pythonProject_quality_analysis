# -*- coding: utf-8 -*-
"""ERP / MES 连接器动态加载。"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger("QualitySystem")


def _search_paths():
    try:
        from config.settings import CONNECTOR_SEARCH_PATHS
        return list(CONNECTOR_SEARCH_PATHS)
    except Exception:
        return [
            os.path.join(os.path.dirname(__file__)),
            r"D:\AI助理\数据对接系统",
            r"D:\AI助理\数据对接系统\config",
        ]


def import_connectors() -> Tuple[Optional[Callable], Optional[Callable]]:
    """导入 get_erp / get_mes，找不到时返回 (None, None)。"""
    erp_path = None
    mes_path = None

    for base in _search_paths():
        if not base:
            continue
        erp_file = os.path.join(base, "erp_connector.py")
        mes_file = os.path.join(base, "mes_connector.py")
        if os.path.exists(erp_file) and erp_path is None:
            erp_path = os.path.dirname(erp_file)
        if os.path.exists(mes_file) and mes_path is None:
            mes_path = os.path.dirname(mes_file)

    try:
        if erp_path and erp_path not in sys.path:
            sys.path.insert(0, erp_path)
        if mes_path and mes_path not in sys.path:
            sys.path.insert(0, mes_path)

        from erp_connector import get_erp  # type: ignore
        from mes_connector import get_mes  # type: ignore
        return get_erp, get_mes
    except ImportError as e:
        logger.warning(f"无法导入数据连接器: {e}")
        return None, None


def get_erp() -> Any:
    get_erp_fn, _ = import_connectors()
    if get_erp_fn is None:
        raise ImportError("erp_connector 未找到，请放到 app/connectors/ 或配置 QA_CONNECTOR_PATHS")
    return get_erp_fn()


def get_mes() -> Any:
    _, get_mes_fn = import_connectors()
    if get_mes_fn is None:
        raise ImportError("mes_connector 未找到，请放到 app/connectors/ 或配置 QA_CONNECTOR_PATHS")
    return get_mes_fn()
