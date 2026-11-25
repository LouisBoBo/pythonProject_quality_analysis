# -*- coding: utf-8 -*-
"""将报废 / IPQC / SPC / COPQ / 体检摘要导出为 Excel。"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


def _safe_name(s: str, fallback: str = "export") -> str:
    raw = (s or fallback).strip() or fallback
    for ch in '\\/:*?"<>|\n\r\t':
        raw = raw.replace(ch, "_")
    return raw[:80]


def _write_sheet(ws, headers: List[str], rows: List[List[Any]], title: str = ""):
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(color="FFFFFF", bold=True)
    if title:
        ws.append([title])
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(1, len(headers)))
        ws["A1"].font = Font(bold=True, size=13, color="1F4E79")
        ws.append([])
    ws.append(headers)
    header_row = ws.max_row
    for col, _ in enumerate(headers, 1):
        cell = ws.cell(header_row, col)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    for row in rows:
        ws.append([(x if x is not None else "") for x in row])
    for col in range(1, len(headers) + 1):
        letter = get_column_letter(col)
        maxlen = len(str(headers[col - 1] or ""))
        for r in range(header_row + 1, min(ws.max_row, header_row + 80) + 1):
            maxlen = max(maxlen, len(str(ws.cell(r, col).value or "")))
        ws.column_dimensions[letter].width = min(36, max(10, maxlen + 2))


def _kpi_rows(kpi: Dict[str, Any]) -> List[List[Any]]:
    return [[str(k), v] for k, v in (kpi or {}).items()]


def export_module_excel(
    module: str,
    data: Dict[str, Any],
    output_dir: str,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """
    按模块导出 Excel，返回文件路径。
    module: scrap | ipqc | spc | copq | health | process
    """
    module = (module or "").strip().lower()
    extra = extra or {}
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    part = _safe_name(str(extra.get("part") or data.get("part_number") or ""), "ALL")
    wb = Workbook()
    # remove default later if unused
    default = wb.active
    default.title = "概览"

    if module == "scrap":
        filename = f"Scrap_{part}_{stamp}.xlsx"
        default["A1"] = "报废分析导出"
        default["A2"] = f"料号: {extra.get('part') or '全部'}"
        default["A3"] = f"报废PCS: {data.get('total_scrap_pcs')}"
        default["A4"] = f"入库PCS: {data.get('total_in_stock_pcs')}"
        default["A5"] = f"报废率%: {data.get('scrap_rate')}"
        default["A6"] = f"周期: {data.get('fiscal_period') or data.get('date_filter') or ''}"
        ws = wb.create_sheet("缺陷TOP")
        _write_sheet(
            ws,
            ["缺陷", "工序", "PCS", "占比%"],
            [
                [
                    d.get("defect") or d.get("InspectionItem") or d.get("item") or "",
                    d.get("process") or d.get("InspectionProcess") or "",
                    d.get("pcs") or d.get("qty") or d.get("count") or 0,
                    d.get("ratio") or d.get("pct") or "",
                ]
                for d in (data.get("defect_top10") or [])
            ],
        )
        ws2 = wb.create_sheet("工序报废")
        _write_sheet(
            ws2,
            ["工序", "PCS", "占比%"],
            [
                [
                    p.get("process") or p.get("InspectionProcess") or p.get("name") or "",
                    p.get("pcs") or p.get("qty") or p.get("count") or 0,
                    p.get("ratio") or p.get("pct") or "",
                ]
                for p in (data.get("process_scrap") or [])
            ],
        )
        ws3 = wb.create_sheet("月度趋势")
        _write_sheet(
            ws3,
            ["周期/日期", "报废PCS", "入库PCS", "报废率%"],
            [
                [
                    t.get("period") or t.get("date") or t.get("FiscalPeriod") or "",
                    t.get("scrap_pcs") or t.get("pcs") or t.get("reject") or 0,
                    t.get("stock_pcs") or t.get("in_stock") or "",
                    t.get("scrap_rate") or t.get("rate") or "",
                ]
                for t in (data.get("monthly_trend") or [])
            ],
        )

    elif module == "ipqc":
        filename = f"IPQC_{stamp}.xlsx"
        kpi = data.get("kpi") or {}
        _write_sheet(default, ["指标", "值"], _kpi_rows(kpi), title="IPQC KPI")
        ws = wb.create_sheet("工序合格率")
        _write_sheet(
            ws,
            ["工序", "检验数", "合格", "不合格", "合格率%"],
            [
                [
                    p.get("process_name") or "",
                    p.get("total") or p.get("count") or 0,
                    p.get("pass_c") or p.get("pass") or 0,
                    p.get("ng_c") or p.get("ng") or 0,
                    p.get("pass_rate") or "",
                ]
                for p in (data.get("process_rates") or [])
            ],
        )
        ws2 = wb.create_sheet("日趋势")
        _write_sheet(
            ws2,
            ["日期", "检验数", "合格率%", "不合格率%"],
            [
                [
                    d.get("date") or d.get("day") or "",
                    d.get("total") or d.get("count") or 0,
                    d.get("pass_rate") or "",
                    d.get("ng_rate") or "",
                ]
                for d in (data.get("daily_trend") or [])
            ],
        )
        ws3 = wb.create_sheet("客户不合格TOP")
        _write_sheet(
            ws3,
            ["客户", "不合格数", "检验数", "不合格率%"],
            [
                [
                    c.get("customer_name") or c.get("customer_code") or "",
                    c.get("ng_count") or c.get("ng") or 0,
                    c.get("total") or "",
                    c.get("ng_rate") or "",
                ]
                for c in (data.get("customer_ng_top") or [])
            ],
        )

    elif module == "spc":
        filename = f"SPC_{stamp}.xlsx"
        summary = data.get("summary") or {}
        _write_sheet(default, ["指标", "值"], _kpi_rows(summary), title="SPC 汇总")
        ws = wb.create_sheet("控制图列表")
        _write_sheet(
            ws,
            ["ID", "编码", "名称", "工序", "特性", "类型", "Cp", "Cpk", "状态", "USL", "LSL", "UCL", "CL", "LCL"],
            [
                [
                    c.get("id"),
                    c.get("chart_code"),
                    c.get("chart_name"),
                    c.get("process_name"),
                    c.get("characteristic"),
                    c.get("chart_type"),
                    c.get("cp"),
                    c.get("cpk"),
                    c.get("status"),
                    c.get("usl"),
                    c.get("lsl"),
                    c.get("ucl"),
                    c.get("cl"),
                    c.get("lcl"),
                ]
                for c in (data.get("charts") or [])
            ],
        )
        detail = data.get("detail")
        if detail:
            pts = detail.get("data_points") or detail.get("points") or detail.get("measurements") or []
            ws2 = wb.create_sheet("测点明细")
            if pts and isinstance(pts[0], dict):
                keys = list(pts[0].keys())
                _write_sheet(ws2, keys, [[p.get(k) for k in keys] for p in pts])
            else:
                _write_sheet(ws2, ["value"], [[p] for p in pts])

    elif module == "copq":
        filename = f"COPQ_{stamp}.xlsx"
        kpi = data.get("kpi") or {}
        _write_sheet(default, ["指标", "值"], _kpi_rows(kpi), title="COPQ KPI")
        ws = wb.create_sheet("按客户")
        _write_sheet(
            ws,
            ["客户编码", "客户名称", "COPQ", "件数"],
            [
                [
                    c.get("customer_code") or "",
                    c.get("customer_name") or "",
                    c.get("copq") or c.get("copq_amount") or c.get("amount") or 0,
                    c.get("count") or c.get("total_count") or "",
                ]
                for c in (data.get("by_customer") or [])
            ],
        )
        ws2 = wb.create_sheet("按产品")
        _write_sheet(
            ws2,
            ["产品/料号", "名称", "COPQ", "件数"],
            [
                [
                    p.get("product_code") or p.get("part_number") or "",
                    p.get("product_name") or "",
                    p.get("copq") or p.get("copq_amount") or p.get("amount") or 0,
                    p.get("count") or "",
                ]
                for p in (data.get("by_product") or [])
            ],
        )
        ws3 = wb.create_sheet("客诉明细")
        recent = data.get("recent") or []
        if recent and isinstance(recent[0], dict):
            keys = [
                "complaint_no", "complaint_date", "customer_code", "customer_name",
                "product_code", "product_name", "category", "status",
                "copq_amount", "affected_qty", "description",
            ]
            present = [k for k in keys if any(k in r for r in recent[:5])] or list(recent[0].keys())[:12]
            _write_sheet(ws3, present, [[r.get(k) for k in present] for r in recent])
        ws4 = wb.create_sheet("月趋势")
        _write_sheet(
            ws4,
            ["月份", "COPQ", "件数"],
            [
                [m.get("month") or m.get("period") or "", m.get("copq") or m.get("amount") or 0, m.get("count") or ""]
                for m in (data.get("monthly") or [])
            ],
        )

    elif module == "health":
        filename = f"Health_{part}_{stamp}.xlsx"
        score = data.get("score") or {}
        _write_sheet(
            default,
            ["项", "值"],
            [
                ["料号", data.get("part_number") or extra.get("part") or "全厂"],
                ["报告号", data.get("report_no") or ""],
                ["总分", score.get("total")],
                ["等级", f"{score.get('grade') or ''} {score.get('grade_name') or ''}"],
                ["结论", (data.get("verdict") or "")[:500]],
            ],
            title="品质体检摘要",
        )
        ws = wb.create_sheet("维度得分")
        scores = score.get("scores") or {}
        _write_sheet(ws, ["维度", "得分"], [[k, v] for k, v in scores.items()])
        ws2 = wb.create_sheet("问题清单")
        _write_sheet(
            ws2,
            ["级别", "维度", "标题", "详情"],
            [
                [f.get("level"), f.get("dim"), f.get("title"), f.get("detail")]
                for f in (data.get("findings") or [])
            ],
        )
        ws3 = wb.create_sheet("改善建议")
        recs = data.get("recommendations") or []
        rows = []
        for r in recs:
            if isinstance(r, dict):
                rows.append([r.get("priority") or r.get("level") or "", r.get("title") or "", r.get("detail") or r.get("action") or ""])
            else:
                rows.append(["", str(r), ""])
        _write_sheet(ws3, ["优先级", "标题", "内容"], rows)

    elif module == "process":
        filename = f"Process_{part}_{stamp}.xlsx"
        _write_sheet(
            default,
            ["项", "值"],
            [
                ["料号", extra.get("part") or ""],
                ["平均良率%", data.get("avg_yield")],
                ["最低良率%", data.get("min_yield")],
                ["匹配工单", data.get("wo_matched")],
                ["检验条数", data.get("inspect_count")],
            ],
            title="工序过程分析",
        )
        ws = wb.create_sheet("工序良率")
        _write_sheet(
            ws,
            ["工序", "良率%", "投入", "不良", "检验数"],
            [
                [
                    p.get("process_name") or p.get("name") or "",
                    p.get("yield") or p.get("pass_rate") or p.get("yield_rate") or "",
                    p.get("input") or p.get("qty") or "",
                    p.get("defect") or p.get("ng") or "",
                    p.get("inspect_count") or "",
                ]
                for p in (data.get("process_yield") or [])
            ],
        )
        ws2 = wb.create_sheet("不良明细")
        _write_sheet(
            ws2,
            ["工序", "不良项", "数量"],
            [
                [
                    d.get("process_name") or d.get("process") or "",
                    d.get("defect") or d.get("item") or d.get("name") or "",
                    d.get("qty") or d.get("count") or 0,
                ]
                for d in (data.get("process_defects") or [])
            ],
        )
    else:
        raise ValueError(f"不支持的导出模块: {module}")

    path = str(out / filename)
    wb.save(path)
    return path
