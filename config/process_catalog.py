"""品质系统工序口径（与 MES/ERP process_catalog 对齐）。

标准工序：钻孔 / 蚀刻 / 阻焊 / 成型 / 待确认工序
旧 SMT 演示名经 LEGACY_PROCESS_ALIASES 归一，避免看板双口径。
"""

from __future__ import annotations

from typing import Iterable, Optional

# PCB / IPQC 主工序（流程顺序；待确认工序垫后）
STANDARD_PROCESSES: list[str] = ["钻孔", "蚀刻", "阻焊", "成型", "待确认工序"]

QUALITY_PROCESSES: list[str] = list(STANDARD_PROCESSES)

# IPQC 源工序 ID → 名称（与 MES ipqc_import 一致）
IPQC_PROCESS_NAME_MAP: dict[str, str] = {
    "3273661841000010": "成型",
    "3273661841000049": "钻孔",
    "3273661841000061": "阻焊",
    "3273661841000048": "待确认工序",
}

# 历史 SMT / 细分工序 → 标准目录
LEGACY_PROCESS_ALIASES: dict[str, str] = {
    "贴片": "阻焊",
    "焊接": "蚀刻",
    "AOI检测": "钻孔",
    "AOI": "钻孔",
    "功能测试": "成型",
    "包装": "待确认工序",
    "组装": "成型",
    "检验": "待确认工序",
    "测试": "待确认工序",
    "未知": "待确认工序",
    "未知工序": "待确认工序",
    # ERP 报废细分工序 → 标准桶
    "外层线路": "蚀刻",
    "内层线路": "蚀刻",
    "图形电镀": "蚀刻",
    "电镀": "蚀刻",
    "沉铜": "钻孔",
    "化学沉铜": "钻孔",
    "字符": "成型",
    "文字": "成型",
    "喷锡": "成型",
    "无铅喷锡": "成型",
    "沉金": "成型",
    "开料": "钻孔",
    "压合": "钻孔",
    "终检": "待确认工序",
    "FQC": "待确认工序",
    "FQC(内部客检)": "待确认工序",
    "FQC2": "待确认工序",
}

QUALITY_DEFECT_TYPES: list[str] = [
    "阻焊擦花",
    "阻焊露铜",
    "孔偏",
    "孔破",
    "外形超差",
    "崩边",
    "线宽偏细",
    "侧蚀超标",
]

IPQC_METRICS_LINE = "PCB产线"

# 空名 / 占位统一落到待确认
_EMPTY_ALIASES = {"", "-", "—", "null", "none", "n/a"}


def normalize_process_name(name: Optional[str]) -> Optional[str]:
    """任意工序名 → 标准目录；未知名称去空白后原样返回。"""
    if name is None:
        return None
    text = str(name).strip()
    if not text or text.lower() in _EMPTY_ALIASES:
        return "待确认工序"
    if text in STANDARD_PROCESSES:
        return text
    if text in IPQC_PROCESS_NAME_MAP:
        return IPQC_PROCESS_NAME_MAP[text]
    mapped = LEGACY_PROCESS_ALIASES.get(text)
    if mapped:
        return mapped
    # CID 形如 P-ZK / 数字 ID
    if text in ("P-ZK", "P-YS", "P-ZS", "P-CX", "P-QR"):
        return {
            "P-ZK": "钻孔",
            "P-YS": "蚀刻",
            "P-ZS": "阻焊",
            "P-CX": "成型",
            "P-QR": "待确认工序",
        }[text]
    return text


def process_sort_key(name: Optional[str]) -> tuple:
    """标准工序按工艺顺序；其余排后。"""
    n = normalize_process_name(name) or "待确认工序"
    try:
        return (0, STANDARD_PROCESSES.index(n), n)
    except ValueError:
        return (1, 999, n)


def aliases_including_self(standard: str) -> list[str]:
    """标准工序名 + 会映射到它的全部别名（用于 SQL IN）。"""
    std = normalize_process_name(standard) or str(standard or "").strip()
    out = [std] if std else []
    for k, v in LEGACY_PROCESS_ALIASES.items():
        if v == std and k not in out:
            out.append(k)
    for cid, name in IPQC_PROCESS_NAME_MAP.items():
        if name == std and cid not in out:
            out.append(cid)
    return out


def catalog_payload() -> dict:
    """供 /api/processes 返回。"""
    return {
        "processes": list(STANDARD_PROCESSES),
        "quality_processes": list(QUALITY_PROCESSES),
        "legacy_aliases": dict(LEGACY_PROCESS_ALIASES),
        "defect_types": list(QUALITY_DEFECT_TYPES),
        "formula": "与 MES/ERP process_catalog 对齐：钻孔→蚀刻→阻焊→成型→待确认工序",
        "data_source": "config/process_catalog.py",
    }


def merge_counts_by_process(
    rows: Iterable[dict],
    name_key: str = "ProcessName",
    sum_keys: Optional[list[str]] = None,
) -> list[dict]:
    """按归一化工序名合并计数类字段。"""
    sum_keys = sum_keys or [
        "InspectCount", "PassCount", "NgCount", "DefectCount",
        "total", "pass_count", "ng_count", "scrap", "pcs", "Qty",
    ]
    merged: dict[str, dict] = {}
    for row in rows or []:
        raw = row.get(name_key) or row.get("process_name") or row.get("process") or ""
        name = normalize_process_name(str(raw)) or "待确认工序"
        slot = merged.get(name)
        if slot is None:
            slot = dict(row)
            slot[name_key] = name
            if "process_name" in row or name_key == "process_name":
                slot["process_name"] = name
            if "process" in row:
                slot["process"] = name
            slot["_raw_names"] = []
            merged[name] = slot
        else:
            for k in sum_keys:
                if k in row or k in slot:
                    try:
                        slot[k] = int(slot.get(k) or 0) + int(row.get(k) or 0)
                    except (TypeError, ValueError):
                        pass
        if raw and str(raw).strip() != name:
            slot.setdefault("_raw_names", [])
            if str(raw).strip() not in slot["_raw_names"]:
                slot["_raw_names"].append(str(raw).strip())

    out = list(merged.values())
    out.sort(key=lambda r: process_sort_key(r.get(name_key) or r.get("process_name")))
    return out
