# -*- coding: utf-8 -*-
"""将项目根目录与 app 目录加入 sys.path，供调试脚本复用。"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TOOLS_DIR.parents[1]
_APP_DIR = _PROJECT_ROOT / "app"

for p in (_PROJECT_ROOT, _APP_DIR):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)
