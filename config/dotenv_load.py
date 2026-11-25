# -*- coding: utf-8 -*-
"""轻量加载项目根目录 .env（无第三方依赖）。不覆盖已存在的系统环境变量。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_LOADED = False


def load_dotenv(dotenv_path: Optional[Path] = None, override: bool = False) -> Path:
    """读取 KEY=VALUE；默认不覆盖已有 os.environ。"""
    global _LOADED
    root = Path(__file__).resolve().parent.parent
    path = Path(dotenv_path) if dotenv_path else root / ".env"
    if not path.exists():
        _LOADED = True
        return path
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return path
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        if override or key not in os.environ:
            os.environ[key] = val
    _LOADED = True
    return path
