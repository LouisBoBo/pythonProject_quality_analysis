# -*- coding: utf-8 -*-
"""
ERP ↔ GraphRAG 缺陷/工序映射
============================
- 不改 ERP 原始缺陷名，只在分析 / 8D / 知识库检索时对齐到图谱名
- 主配置：config/defect_mapping.json（可热更新，改完无需改代码）
- 可用环境变量 QA_DEFECT_MAPPING 指定其它 JSON 路径
"""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_CONFIG_DIR = Path(__file__).resolve().parent
_DEFAULT_PATH = _CONFIG_DIR / "defect_mapping.json"
_lock = threading.RLock()
_cache: Dict[str, Any] = {"mtime": None, "path": None, "data": None}

_BUILTIN_DEFECT: Dict[str, List[str]] = {
    "阻焊气泡": ["绿油气泡"],
    "阻焊露铜": ["断阻焊桥", "阻焊断桥", "阻焊入孔"],
    "阻焊擦花": ["字符擦花", "擦花金手指"],
}

_BUILTIN_PROCESS: Dict[str, List[str]] = {
    "阻焊": ["显影", "开油", "丝印", "曝光", "前处理"],
    "绿油": ["显影", "开油", "丝印", "曝光"],
}


def mapping_file_path() -> Path:
    env = (os.environ.get("QA_DEFECT_MAPPING") or "").strip()
    return Path(env) if env else _DEFAULT_PATH


def _normalize_map(raw: Any) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        key = str(k or "").strip()
        if not key:
            continue
        if isinstance(v, str):
            vals = [v.strip()] if v.strip() else []
        elif isinstance(v, list):
            vals = [str(x).strip() for x in v if str(x).strip()]
        else:
            continue
        # 去重保序
        seen = set()
        uniq = []
        for x in vals:
            if x not in seen:
                seen.add(x)
                uniq.append(x)
        if uniq:
            out[key] = uniq
    return out


def load_mapping(force: bool = False) -> Dict[str, Any]:
    """加载映射；文件变更时自动重读。"""
    path = mapping_file_path()
    with _lock:
        mtime = path.stat().st_mtime if path.exists() else None
        if (
            not force
            and _cache["data"] is not None
            and _cache["path"] == str(path)
            and _cache["mtime"] == mtime
        ):
            return deepcopy(_cache["data"])

        data = {
            "version": 1,
            "description": "",
            "defect_map": dict(_BUILTIN_DEFECT),
            "process_map": dict(_BUILTIN_PROCESS),
            "source_path": str(path),
            "loaded": False,
        }
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data["version"] = int(raw.get("version") or 1)
                    data["description"] = str(raw.get("description") or "")
                    file_defects = _normalize_map(raw.get("defect_map"))
                    file_procs = _normalize_map(raw.get("process_map"))
                    # 文件优先覆盖内置
                    data["defect_map"].update(file_defects)
                    data["process_map"].update(file_procs)
                    data["loaded"] = True
            except Exception as e:
                data["load_error"] = str(e)

        _cache["data"] = data
        _cache["path"] = str(path)
        _cache["mtime"] = mtime
        return deepcopy(data)


def get_defect_candidates(erp_defect: str) -> List[str]:
    """ERP 缺陷 → 候选 GraphRAG 名列表（不含自身，调用方可再拼）。"""
    name = (erp_defect or "").strip()
    if not name:
        return []
    m = load_mapping()["defect_map"]
    return list(m.get(name) or [])


def get_process_candidates(erp_process: str) -> List[str]:
    name = (erp_process or "").strip()
    if not name:
        return []
    try:
        from config.process_catalog import normalize_process_name
        std = normalize_process_name(name) or name
    except Exception:
        std = name
    m = load_mapping()["process_map"]
    # 先标准名，再原始名；去重保序
    out: List[str] = []
    for key in (std, name):
        for x in (m.get(key) or []):
            if x not in out:
                out.append(x)
    return out


def map_erp_defect(
    erp_defect: str,
    resolver=None,
) -> Dict[str, Any]:
    """
    对齐单条 ERP 缺陷。
    resolver: 可选 callable(name)->Optional[str]，一般为 GraphRAGClient.resolve_defect_name
    """
    erp = (erp_defect or "").strip()
    result = {
        "erp_defect": erp,
        "graphrag_defect": None,
        "candidates": [],
        "match_type": "unmapped",
        "mapped": False,
    }
    if not erp:
        return result

    candidates = get_defect_candidates(erp)
    # 自身也作为候选（图谱可能已用同名）
    try_list = []
    for c in [erp] + candidates:
        if c and c not in try_list:
            try_list.append(c)
    result["candidates"] = list(candidates)

    if resolver is None:
        if candidates:
            result["graphrag_defect"] = candidates[0]
            result["match_type"] = "config_primary"
            result["mapped"] = True
        elif erp:
            result["graphrag_defect"] = erp
            result["match_type"] = "passthrough"
            result["mapped"] = False
        return result

    for i, cand in enumerate(try_list):
        try:
            resolved = resolver(cand)
        except Exception:
            resolved = None
        if not resolved:
            continue
        result["graphrag_defect"] = resolved
        result["mapped"] = True
        if cand == erp and resolved == erp:
            result["match_type"] = "exact"
        elif cand in candidates or resolved in candidates:
            result["match_type"] = "config_alias"
        elif i == 0 and cand == erp:
            result["match_type"] = "fuzzy"
        else:
            result["match_type"] = "resolved"
        return result

    # resolver 全部失败时仍采用配置首项，便于报告展示「应对齐到哪」
    if candidates:
        result["graphrag_defect"] = candidates[0]
        result["match_type"] = "config_primary"
        result["mapped"] = True
        return result

    result["match_type"] = "unmapped"
    result["mapped"] = False
    result["graphrag_defect"] = None
    return result


def map_erp_defects(
    erp_defects: List[str],
    resolver=None,
) -> Dict[str, Any]:
    """批量对齐，返回 alignments + 汇总。"""
    alignments = []
    seen = set()
    for raw in erp_defects or []:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        alignments.append(map_erp_defect(name, resolver=resolver))

    mapped = [a for a in alignments if a.get("mapped")]
    unmapped = [a for a in alignments if not a.get("mapped")]
    return {
        "alignments": alignments,
        "mapped_count": len(mapped),
        "unmapped_count": len(unmapped),
        "unmapped": [a["erp_defect"] for a in unmapped],
        "summary_lines": [
            (
                f"ERP「{a['erp_defect']}」→ 图谱「{a['graphrag_defect']}」"
                f"（{a['match_type']}）"
                if a.get("mapped") and a.get("graphrag_defect")
                else f"ERP「{a['erp_defect']}」→ 未对齐（请补 config/defect_mapping.json）"
            )
            for a in alignments
        ],
        "config_path": str(mapping_file_path()),
    }


def describe_mapping_for_api() -> Dict[str, Any]:
    data = load_mapping()
    return {
        "version": data.get("version"),
        "description": data.get("description"),
        "source_path": data.get("source_path"),
        "loaded": data.get("loaded"),
        "load_error": data.get("load_error"),
        "defect_map": data.get("defect_map"),
        "process_map": data.get("process_map"),
        "defect_count": len(data.get("defect_map") or {}),
        "process_count": len(data.get("process_map") or {}),
        "howto": (
            "编辑 config/defect_mapping.json 增加 ERP缺陷→GraphRAG缺陷列表；"
            "保存后下次解析自动生效（不改 ERP 源数据）。"
        ),
    }
