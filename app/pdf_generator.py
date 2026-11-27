# -*- coding: utf-8 -*-
"""
PDF报告生成模块
用于生成PCB品质体检报告PDF
"""

import os
from datetime import datetime
from io import BytesIO
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 注册中文字体
try:
    font_paths = [
        'C:/Windows/Fonts/msyh.ttc',
        'C:/Windows/Fonts/simhei.ttf',
        'C:/Windows/Fonts/simsun.ttc',
    ]
    chinese_font = 'MicrosoftYaHei'
    for fp in font_paths:
        if os.path.exists(fp):
            pdfmetrics.registerFont(TTFont('MicrosoftYaHei', fp))
            chinese_font = 'MicrosoftYaHei'
            break
    else:
        chinese_font = 'Helvetica'
except Exception:
    chinese_font = 'Helvetica'

FONT_NAME = chinese_font


def _safe_name(value: object, limit: int = 40) -> str:
    """文件名片段：仅保留字母/数字/汉字与 -_，防止路径穿越。"""
    text = str(value if value is not None else "").strip()
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text)[:limit] or "NA"


def _fmt_pct(value, digits: int = 2) -> str:
    """百分比展示：None/空值显示「—」，不把「无基数/无数据」印成 0%。"""
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


# 体检接口 score.scores 的维度键 → 中文名
_DIM_LABELS = {
    "scrap": "报废",
    "inspection": "检验",
    "process": "过程",
    "complaint": "客诉",
    "material": "原材料",
}


def generate_health_check_pdf(health_data, part_number, fiscal_period=None, output_path=None, output_dir=None):
    """
    生成品质体检报告PDF
    """
    if not output_path:
        if output_dir:
            base_dir = output_dir
        else:
            try:
                from config.settings import OUTPUT_DIR
                base_dir = str(OUTPUT_DIR)
            except Exception:
                base_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "output",
                )
        os.makedirs(base_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_part = _safe_name(part_number)
        if fiscal_period:
            filename = f"品质体检报告_{safe_part}_{_safe_name(fiscal_period, 12)}_{timestamp}.pdf"
        else:
            filename = f"品质体检报告_{safe_part}_{timestamp}.pdf"
        output_path = os.path.join(base_dir, filename)

    # macOS / Linux 中文字体回退
    global FONT_NAME
    if FONT_NAME == 'Helvetica':
        for fp in (
            '/System/Library/Fonts/PingFang.ttc',
            '/System/Library/Fonts/STHeiti Light.ttc',
            '/Library/Fonts/Arial Unicode.ttf',
            'C:/Windows/Fonts/msyh.ttc',
            'C:/Windows/Fonts/simhei.ttf',
        ):
            if os.path.exists(fp):
                try:
                    pdfmetrics.registerFont(TTFont('ChineseFont', fp))
                    FONT_NAME = 'ChineseFont'
                    break
                except Exception:
                    continue

    doc = SimpleDocTemplate(
        output_path,
        pagesize=landscape(A4),
        rightMargin=15*mm,
        leftMargin=15*mm,
        topMargin=15*mm,
        bottomMargin=15*mm
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Title'],
        fontName=FONT_NAME,
        fontSize=20,
        textColor=colors.HexColor('#1a5276'),
        spaceAfter=10,
        alignment=TA_CENTER
    )

    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontName=FONT_NAME,
        fontSize=12,
        textColor=colors.HexColor('#566573'),
        spaceAfter=20,
        alignment=TA_CENTER
    )

    section_style = ParagraphStyle(
        'SectionTitle',
        parent=styles['Heading2'],
        fontName=FONT_NAME,
        fontSize=14,
        textColor=colors.HexColor('#1a5276'),
        spaceBefore=15,
        spaceAfter=8,
    )

    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontName=FONT_NAME,
        fontSize=10,
        leading=14
    )

    elements = []

    # 封面标题
    elements.append(Paragraph("PCB 品质全面体检报告", title_style))

    period_str = fiscal_period if fiscal_period else health_data.get('date_range', '全部')
    if fiscal_period and len(fiscal_period) == 6:
        period_str = f"{fiscal_period[:4]}年{fiscal_period[4:6]}月"

    elements.append(Paragraph(
        f"料号: {_pdf_escape(part_number or '—')} | 周期: {_pdf_escape(period_str)}",
        subtitle_style,
    ))
    elements.append(Spacer(1, 10))

    # 报告信息
    report_info = [
        ["报告编号", health_data.get('report_no', 'N/A')],
        ["生成时间", datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
        ["分析周期", period_str]
    ]

    info_table = Table(report_info, colWidths=[100, 200])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), FONT_NAME),
        ('FONTNAME', (1, 0), (1, -1), FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#566573')),
        ('TEXTCOLOR', (1, 0), (1, -1), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 20))

    # 综合评分（兼容体检接口 score.* 与旧版 overall.* 两种形状）
    overall = health_data.get('overall') or {}
    score_obj = health_data.get('score') or {}
    score = overall.get('score')
    if score is None:
        score = score_obj.get('total', 0)
    grade = overall.get('grade') or score_obj.get('grade') or 'N'
    grade_color = {
        'A': '#27ae60',
        'B': '#2980b9',
        'C': '#f39c12',
        'D': '#e67e22',
        'E': '#e74c3c',
    }.get(grade, '#7f8c8d')

    elements.append(Paragraph("一、综合评分", section_style))

    score_data = [
        ['综合评分', f'{score} 分', f'等级: {grade}'],
    ]
    score_table = Table(score_data, colWidths=[200, 150, 150])
    score_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#ecf0f1')),
        ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
        ('FONTSIZE', (0, 0), (0, 0), 12),
        ('FONTSIZE', (1, 0), (1, 0), 24),
        ('FONTSIZE', (2, 0), (2, 0), 14),
        ('TEXTCOLOR', (0, 0), (0, 0), colors.HexColor('#2c3e50')),
        ('TEXTCOLOR', (1, 0), (1, 0), colors.HexColor(grade_color)),
        ('TEXTCOLOR', (2, 0), (2, 0), colors.HexColor(grade_color)),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 15),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 15),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#bdc3c7')),
    ]))
    elements.append(score_table)
    elements.append(Spacer(1, 15))

    # 各维度评分
    dimensions = health_data.get('dimensions', {})

    dim_data = [['维度', '得分', '权重', '评价']]
    if dimensions:
        for dim_name, dim_info in dimensions.items():
            if isinstance(dim_info, dict):
                dim_score = dim_info.get('score', 0)
                dim_weight = dim_info.get('weight', 0)
                dim_level = dim_info.get('level', '一般')
                dim_data.append([
                    dim_name,
                    f'{dim_score}分',
                    f'{dim_weight}%',
                    dim_level
                ])
    else:
        # 体检接口返回 score.scores（{维度键: 得分|None}）+ score.dim_max（满分）
        dim_scores = score_obj.get('scores') or {}
        dim_max = score_obj.get('dim_max') or {}
        for key, val in dim_scores.items():
            maxv = dim_max.get(key)
            name = _DIM_LABELS.get(key, key)
            if val is None or not maxv:
                dim_data.append([name, '—', f'{maxv}%' if maxv else '—', '缺数据'])
                continue
            ratio = float(val) / float(maxv)
            level = '良好' if ratio >= 0.85 else ('一般' if ratio >= 0.6 else '偏低')
            dim_data.append([name, f'{int(val)}分', f'{maxv}%', level])

    dim_table = Table(dim_data, colWidths=[120, 80, 80, 100])
    dim_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#34495e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
    ]))
    elements.append(dim_table)
    elements.append(Spacer(1, 20))

    # 报废分析
    scrap = health_data.get('scrap', {})
    elements.append(Paragraph("二、报废分析", section_style))

    scrap_info = [
        ['总报废数量', f'{int(scrap.get("total_scrap_pcs") or 0):,} PCS'],
        ['总入库数量', f'{int(scrap.get("total_in_stock_pcs") or 0):,} PCS'],
        ['报废率', _fmt_pct(scrap.get("scrap_rate"))]
    ]

    scrap_table = Table(scrap_info, colWidths=[150, 200])
    scrap_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fef9e7')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#f39c12')),
    ]))
    elements.append(scrap_table)
    elements.append(Spacer(1, 10))

    # TOP10缺陷
    defect_top10 = scrap.get('defect_top10', [])
    if defect_top10:
        elements.append(Paragraph("TOP10 不良项目:", normal_style))

        defect_data = [['排名', '不良类型', '工序', '报废数量', '占比']]
        for i, defect in enumerate(defect_top10[:10], 1):
            defect_data.append([
                str(i),
                defect.get('defect_type', ''),
                defect.get('process', ''),
                f"{int(defect.get('scrap_pcs') or 0):,}",
                _fmt_pct(defect.get('ratio'), 1)
            ])

        defect_table = Table(defect_data, colWidths=[50, 150, 100, 100, 80])
        defect_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e74c3c')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fdf2f2')]),
        ]))
        elements.append(defect_table)

    # 工序良率
    process = health_data.get('process', {})
    elements.append(Spacer(1, 15))
    elements.append(Paragraph("三、工序良率分析", section_style))

    avg_yield = process.get('avg_yield')
    min_yield = process.get('min_yield')
    process_yield = process.get('process_yield', [])

    yield_info = [
        ['平均良率', _fmt_pct(avg_yield)],
        ['最低良率', _fmt_pct(min_yield)],
        ['涉及工序', f'{len(process_yield)} 道']
    ]

    yield_table = Table(yield_info, colWidths=[100, 100])
    yield_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#eafaf1')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#27ae60')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(yield_table)

    if process_yield:
        elements.append(Spacer(1, 8))
        proc_data = [['工序', '良率', '状态']]
        for proc in process_yield[:15]:
            proc_rate = proc.get('yield_rate', proc.get('PassRate'))
            rate_text = '—' if proc_rate is None else f'{float(proc_rate):.2f}%'
            if proc_rate is None:
                status = '缺数据'
            else:
                status = '正常' if proc_rate >= 90 else '预警' if proc_rate >= 80 else '异常'
            proc_data.append([
                proc.get('process_name') or proc.get('ProcessName', ''),
                rate_text,
                status
            ])

        proc_table = Table(proc_data, colWidths=[200, 100, 100])
        proc_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#27ae60')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0fff4')]),
        ]))
        elements.append(proc_table)

    # 改善建议
    elements.append(PageBreak())
    elements.append(Paragraph("四、改善建议", section_style))

    recommendations = health_data.get('recommendations', [])
    if recommendations:
        for i, rec in enumerate(recommendations, 1):
            priority = rec.get('priority', 'P2')
            priority_color = {
                'P0': '#e74c3c',
                'P1': '#e67e22',
                'P2': '#f39c12',
                'P3': '#3498db'
            }.get(priority, '#7f8c8d')

            rec_text = _pdf_escape(rec.get('title') or rec.get('content') or '')
            action = rec.get('action') or ''
            if action:
                rec_text += f"：{_pdf_escape(action)}"
            elements.append(Paragraph(f"<b>[{priority}]</b> {rec_text}", normal_style))
            elements.append(Spacer(1, 5))
    else:
        elements.append(Paragraph("暂无改善建议", normal_style))

    # 预警信息
    elements.append(Spacer(1, 15))
    elements.append(Paragraph("五、预警信息", section_style))

    alerts = health_data.get('alerts', [])
    if alerts:
        alert_data = [['预警类型', '内容', '等级', '建议']]
        for alert in alerts[:10]:
            alert_data.append([
                alert.get('type', ''),
                alert.get('message', ''),
                alert.get('level', ''),
                alert.get('suggestion', '')
            ])

        alert_table = Table(alert_data, colWidths=[80, 200, 60, 150])
        alert_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e67e22')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fef5e7')]),
        ]))
        elements.append(alert_table)
    else:
        elements.append(Paragraph("暂无预警信息", normal_style))

    # 页脚
    elements.append(Spacer(1, 30))
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontName=FONT_NAME,
        fontSize=8,
        textColor=colors.HexColor('#95a5a6'),
        alignment=TA_CENTER
    )
    elements.append(Paragraph(
        f"本报告由PCB品质分析系统自动生成 | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        footer_style
    ))

    doc.build(elements)

    return output_path


def _ensure_chinese_font():
    """确保中文字体可用（macOS / Windows / Linux）。"""
    global FONT_NAME
    if FONT_NAME and FONT_NAME != 'Helvetica':
        return FONT_NAME
    for fp in (
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/STHeiti Light.ttc',
        '/Library/Fonts/Arial Unicode.ttf',
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        'C:/Windows/Fonts/msyh.ttc',
        'C:/Windows/Fonts/simhei.ttf',
        'C:/Windows/Fonts/simsun.ttc',
    ):
        if os.path.exists(fp):
            try:
                pdfmetrics.registerFont(TTFont('ChineseFont8D', fp))
                FONT_NAME = 'ChineseFont8D'
                return FONT_NAME
            except Exception:
                continue
    return FONT_NAME or 'Helvetica'


def _pdf_escape(text: str) -> str:
    """ReportLab Paragraph 需要转义 XML 特殊字符，并保留换行。"""
    s = str(text or "")
    s = (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s.replace("\n", "<br/>")


def generate_8d_pdf(report: dict, output_path=None, output_dir=None) -> str:
    """
    生成 8D 改善报告 PDF（竖版 A4，便于打印归档）。
    report: eightd_reports 行或 /api/8d/get 返回的 report 对象
    """
    font = _ensure_chinese_font()
    part = (report.get("part_number") or "UNKNOWN").strip() or "UNKNOWN"
    report_no = (report.get("report_no") or report.get("id") or "8D").strip()
    safe_no = "".join(c if c.isalnum() or c in "-_" else "_" for c in report_no)[:48]

    if not output_path:
        if output_dir:
            base_dir = output_dir
        else:
            try:
                from config.settings import OUTPUT_DIR
                base_dir = str(OUTPUT_DIR)
            except Exception:
                base_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "output",
                )
        os.makedirs(base_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 文件名仅用 ASCII，避免下载 URL 中文编码问题；标题仍为中文
        safe_part = "".join(c if c.isalnum() or c in "-_" else "_" for c in part)[:40]
        filename = f"8D_{safe_no}_{safe_part}_{timestamp}.pdf"
        output_path = os.path.join(base_dir, filename)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "E8DTitle",
        parent=styles["Title"],
        fontName=font,
        fontSize=18,
        textColor=colors.HexColor("#1a5276"),
        spaceAfter=8,
        alignment=TA_CENTER,
    )
    meta_style = ParagraphStyle(
        "E8DMeta",
        parent=styles["Normal"],
        fontName=font,
        fontSize=10,
        textColor=colors.HexColor("#566573"),
        spaceAfter=6,
        alignment=TA_CENTER,
    )
    section_style = ParagraphStyle(
        "E8DSection",
        parent=styles["Heading2"],
        fontName=font,
        fontSize=12,
        textColor=colors.HexColor("#1a5276"),
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "E8DBody",
        parent=styles["Normal"],
        fontName=font,
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#2c3e50"),
    )
    label_style = ParagraphStyle(
        "E8DLabel",
        parent=styles["Normal"],
        fontName=font,
        fontSize=9,
        textColor=colors.HexColor("#566573"),
    )

    status_map = {"open": "进行中", "closed": "已关闭"}
    status = status_map.get(report.get("status") or "", report.get("status") or "—")
    severity = int(report.get("severity") or 0)
    created = str(report.get("created_at") or "").replace("T", " ")[:19]

    elements = []
    elements.append(Paragraph("PCB 品质 8D 改善报告", title_style))
    elements.append(
        Paragraph(
            f"报告编号：{_pdf_escape(report_no)}　｜　料号：{_pdf_escape(part)}",
            meta_style,
        )
    )
    elements.append(Spacer(1, 6))

    info_rows = [
        [
            Paragraph("客户", label_style),
            Paragraph(_pdf_escape(report.get("customer") or "—"), body_style),
            Paragraph("状态", label_style),
            Paragraph(_pdf_escape(status), body_style),
        ],
        [
            Paragraph("严重度", label_style),
            Paragraph(("★" * severity) if severity else "—", body_style),
            Paragraph("创建时间", label_style),
            Paragraph(_pdf_escape(created or "—"), body_style),
        ],
        [
            Paragraph("问题描述", label_style),
            Paragraph(_pdf_escape(report.get("problem") or "—"), body_style),
            "",
            "",
        ],
    ]
    info_table = Table(info_rows, colWidths=[70, 170, 70, 170])
    info_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f6f7")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d5d8dc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("SPAN", (1, 2), (3, 2)),
            ]
        )
    )
    elements.append(info_table)

    d_titles = [
        ("D1", "成立团队", report.get("d1")),
        ("D2", "问题描述", report.get("d2")),
        ("D3", "临时遏制措施", report.get("d3")),
        ("D4", "根本原因分析", report.get("d4")),
        ("D5", "永久纠正措施", report.get("d5")),
        ("D6", "措施验证", report.get("d6")),
        ("D7", "预防再发", report.get("d7")),
        ("D8", "团队表彰与关闭", report.get("d8")),
    ]
    for code, title, content in d_titles:
        elements.append(Paragraph(f"{code}　{title}", section_style))
        text = (content or "").strip() or "（未填写）"
        elements.append(Paragraph(_pdf_escape(text), body_style))
        elements.append(Spacer(1, 4))

    # 页脚说明
    foot = ParagraphStyle(
        "E8DFoot",
        parent=styles["Normal"],
        fontName=font,
        fontSize=8,
        textColor=colors.HexColor("#7f8c8d"),
        spaceBefore=16,
        alignment=TA_CENTER,
    )
    elements.append(
        Paragraph(
            f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　｜　"
            "指标来自 ERP/MES；D4/D5 知识库引用须现场复核　｜　PCB 品质智能分析系统",
            foot,
        )
    )

    doc.build(elements)
    return output_path


if __name__ == '__main__':
    test_data = {
        'report_no': 'QHC-20260417-ABC123',
        'overall': {'score': 78, 'grade': 'B'},
        'dimensions': {
            '报废': {'score': 18, 'weight': 25, 'level': '良好'},
            '检验': {'score': 16, 'weight': 20, 'level': '良好'},
            '过程': {'score': 15, 'weight': 20, 'level': '良好'},
            '设备': {'score': 12, 'weight': 15, 'level': '良好'},
            '客诉': {'score': 9, 'weight': 10, 'level': '良好'},
            '原材料': {'score': 8, 'weight': 10, 'level': '良好'},
        },
        'scrap': {
            'total_scrap_pcs': 31160,
            'total_in_stock_pcs': 310000,
            'scrap_rate': 9.13,
            'defect_top10': [
                {'defect_type': '喷锡擦花', 'process': '喷锡', 'scrap_pcs': 5000, 'ratio': 16.0},
                {'defect_type': '字符擦花', 'process': '字符', 'scrap_pcs': 4000, 'ratio': 12.8},
            ]
        },
        'process': {
            'avg_yield': 94.5,
            'min_yield': 88.2,
            'process_yield': [
                {'process_name': '钻孔', 'yield_rate': 99.2},
                {'process_name': '电镀', 'yield_rate': 96.5},
            ]
        },
        'recommendations': [
            {'priority': 'P0', 'content': '立即处理喷锡工序设备异常'},
            {'priority': 'P1', 'content': '加强字符印刷工序管控'},
        ],
        'alerts': []
    }

    output = generate_health_check_pdf(test_data, 'B60-10P06T17615A0', '202604')
    print(f'PDF生成成功: {output}')
