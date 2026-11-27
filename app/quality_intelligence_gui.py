# -*- coding: utf-8 -*-
"""
智能品质分析系统 - GUI图形界面
===================================
基于Tkinter的图形用户界面，包含四大追溯模块

作者: WorkBuddy AI
日期: 2026-04-14
"""

import os
import sys
import json
import math
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from datetime import datetime, timedelta
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# 图表绘制工具类
# ═══════════════════════════════════════════════════════════════

class ChartCanvas:
    """使用Tkinter Canvas绑制简单图表"""

    def __init__(self, parent, width=400, height=250):
        self.canvas = tk.Canvas(parent, width=width, height=height, bg='#1e1e2e', highlightthickness=0)
        self.width = width
        self.height = height
        self.padding = {'left': 50, 'right': 20, 'top': 30, 'bottom': 40}

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)

    def clear(self):
        self.canvas.delete('all')

    def draw_bar_chart(self, data, title="", labels=None, colors=None):
        """绘制柱状图"""
        self.clear()
        if not data:
            return

        if colors is None:
            colors = ['#00d4ff', '#00ff88', '#ff9f43', '#ff6b6b', '#888888', '#9b59b6', '#3498db']

        plot_w = self.width - self.padding['left'] - self.padding['right']
        plot_h = self.height - self.padding['top'] - self.padding['bottom']

        max_val = max(data) if data else 1
        if max_val == 0:
            max_val = 1

        if title:
            self.canvas.create_text(self.width/2, 15, text=title, fill='#ffffff', font=('Microsoft YaHei', 10, 'bold'))

        self.canvas.create_line(
            self.padding['left'], self.padding['top'],
            self.padding['left'], self.height - self.padding['bottom'],
            fill='#444444'
        )

        for i in range(5):
            y = self.padding['top'] + plot_h * i / 4
            val = max_val * (4 - i) / 4
            self.canvas.create_text(self.padding['left'] - 8, y, text=f'{val:.0f}', fill='#888888', anchor='e', font=('Arial', 8))
            self.canvas.create_line(
                self.padding['left'], y,
                self.width - self.padding['right'], y,
                fill='#333333', dash=(2, 4)
            )

        bar_count = len(data)
        bar_width = min(40, plot_w / bar_count - 10)
        gap = (plot_w - bar_width * bar_count) / (bar_count + 1)

        for i, val in enumerate(data):
            x = self.padding['left'] + gap + i * (bar_width + gap)
            bar_height = (val / max_val) * plot_h if max_val > 0 else 0
            y_top = self.height - self.padding['bottom'] - bar_height

            color = colors[i % len(colors)]
            self.canvas.create_rectangle(x, y_top, x + bar_width, self.height - self.padding['bottom'],
                                         fill=color, outline='')
            self.canvas.create_text(x + bar_width/2, y_top - 5, text=f'{val:.0f}',
                                    fill='#ffffff', anchor='s', font=('Arial', 8))

            if labels and i < len(labels):
                self.canvas.create_text(x + bar_width/2, self.height - self.padding['bottom'] + 8,
                                        text=labels[i], fill='#888888', font=('Arial', 8))

    def draw_pie_chart(self, data, title="", labels=None, colors=None):
        """绘制饼图"""
        self.clear()
        if not data:
            return

        if colors is None:
            colors = ['#00d4ff', '#00ff88', '#ff9f43', '#ff6b6b', '#888888', '#9b59b6', '#3498db']

        total = sum(data)
        if total == 0:
            return

        if title:
            self.canvas.create_text(self.width/2, 15, text=title, fill='#ffffff', font=('Microsoft YaHei', 10, 'bold'))

        cx, cy = self.width // 2 - 30, self.height // 2 + 10
        radius = min(self.width, self.height) // 2 - 60

        start_angle = 0
        for i, val in enumerate(data):
            if val <= 0:
                continue
            extent = (val / total) * 360
            color = colors[i % len(colors)]

            x1 = cx - radius
            y1 = cy - radius
            x2 = cx + radius
            y2 = cy + radius

            self.canvas.create_arc(x1, y1, x2, y2, start=start_angle, extent=extent,
                                   fill=color, outline='#1e1e2e', width=2)

            if extent > 15:
                mid_angle = math.radians(start_angle + extent/2)
                label_r = radius * 0.7
                lx = cx + label_r * math.cos(mid_angle)
                ly = cy - label_r * math.sin(mid_angle)
                pct = val / total * 100
                self.canvas.create_text(lx, ly, text=f'{pct:.1f}%', fill='#ffffff',
                                        font=('Arial', 8, 'bold'))

            start_angle += extent

        legend_x = self.width - 80
        legend_y = 40
        for i, (val, label) in enumerate(zip(data, labels or [])):
            color = colors[i % len(colors)]
            self.canvas.create_rectangle(legend_x, legend_y + i*18, legend_x + 12, legend_y + i*18 + 12,
                                         fill=color, outline='')
            self.canvas.create_text(legend_x + 18, legend_y + i*18 + 6, text=label[:6],
                                    fill='#888888', anchor='w', font=('Arial', 7))

    def draw_line_chart(self, data, title="", labels=None, colors=None, target_line=None):
        """绘制折线图"""
        self.clear()
        if not data:
            return

        if colors is None:
            colors = ['#00d4ff', '#00ff88']

        plot_w = self.width - self.padding['left'] - self.padding['right']
        plot_h = self.height - self.padding['top'] - self.padding['bottom']

        if title:
            self.canvas.create_text(self.width/2, 15, text=title, fill='#ffffff', font=('Microsoft YaHei', 10, 'bold'))

        self.canvas.create_line(
            self.padding['left'], self.padding['top'],
            self.padding['left'], self.height - self.padding['bottom'],
            fill='#444444'
        )

        self.canvas.create_line(
            self.padding['left'], self.height - self.padding['bottom'],
            self.width - self.padding['right'], self.height - self.padding['bottom'],
            fill='#444444'
        )

        max_val = max(data) * 1.1 if data else 1
        min_val = min(data) * 0.9 if data else 0
        val_range = max_val - min_val if max_val != min_val else 1

        for i in range(5):
            y = self.padding['top'] + plot_h * i / 4
            val = max_val - val_range * i / 4
            self.canvas.create_text(self.padding['left'] - 8, y, text=f'{val:.1f}',
                                    fill='#888888', anchor='e', font=('Arial', 8))
            self.canvas.create_line(self.padding['left'], y,
                                    self.width - self.padding['right'], y,
                                    fill='#333333', dash=(2, 4))

        if target_line is not None:
            y_target = self.padding['top'] + plot_h * (1 - (target_line - min_val) / val_range)
            self.canvas.create_line(self.padding['left'], y_target,
                                    self.width - self.padding['right'], y_target,
                                    fill='#ff6b6b', dash=(5, 3))
            self.canvas.create_text(self.width - self.padding['right'] + 5, y_target,
                                    text='目标', fill='#ff6b6b', anchor='w', font=('Arial', 8))

        points = []
        for i, val in enumerate(data):
            x = self.padding['left'] + plot_w * i / (len(data) - 1) if len(data) > 1 else self.padding['left'] + plot_w / 2
            y = self.padding['top'] + plot_h * (1 - (val - min_val) / val_range)
            points.append((x, y))

            self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=colors[0], outline='')
            self.canvas.create_text(x, y - 10, text=f'{val:.1f}', fill='#ffffff',
                                    anchor='s', font=('Arial', 7))

        for i in range(len(points) - 1):
            self.canvas.create_line(points[i][0], points[i][1],
                                    points[i+1][0], points[i+1][1],
                                    fill=colors[0], width=2)

        if labels:
            step = max(1, len(labels) // 8)
            for i in range(0, len(labels), step):
                x = self.padding['left'] + plot_w * i / (len(labels) - 1) if len(labels) > 1 else self.padding['left'] + plot_w / 2
                self.canvas.create_text(x, self.height - self.padding['bottom'] + 8,
                                        text=labels[i], fill='#888888', font=('Arial', 8))


# ═══════════════════════════════════════════════════════════════
# 统计卡片组件
# ═══════════════════════════════════════════════════════════════

class StatCard(ttk.Frame):
    """统计卡片组件"""

    def __init__(self, parent, title, value="0", unit="", icon="", trend=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.configure(style='StatCard.TFrame')

        self.color_bar = tk.Frame(self, height=3)
        self.color_bar.pack(fill=tk.X)

        content = ttk.Frame(self)
        content.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

        ttk.Label(content, text=title, font=('Microsoft YaHei', 11)).pack(anchor='w')
        value_frame = ttk.Frame(content)
        value_frame.pack(fill=tk.X, pady=5)

        self.value_label = ttk.Label(value_frame, text=str(value), font=('Microsoft YaHei', 28, 'bold'))
        self.value_label.pack(side=tk.LEFT)

        if unit:
            ttk.Label(value_frame, text=unit, font=('Microsoft YaHei', 12), foreground='#888888').pack(side=tk.LEFT, padx=(5, 0))

        if trend is not None:
            trend_text = f"{'↑' if trend > 0 else '↓'} {abs(trend):.1f}%"
            trend_color = '#00ff88' if trend < 0 else '#ff6b6b'
            tk.Label(value_frame, text=trend_text, fg=trend_color, font=('Arial', 10)).pack(side=tk.RIGHT)


# ═══════════════════════════════════════════════════════════════
# 四大模块Tab组件
# ═══════════════════════════════════════════════════════════════

class TraceQueryModule(ttk.Frame):
    """精确查询模块"""

    def __init__(self, parent, system, gui):
        super().__init__(parent)
        self.system = system
        self.gui = gui
        self._create_widgets()

    def _create_widgets(self):
        query_frame = ttk.LabelFrame(self, text="查询条件", padding=15)
        query_frame.pack(fill=tk.X, padx=10, pady=10)

        row1 = ttk.Frame(query_frame)
        row1.pack(fill=tk.X, pady=5)

        ttk.Label(row1, text="料号:").pack(side=tk.LEFT, padx=(0, 5))
        self.part_entry = ttk.Entry(row1, width=25)
        self.part_entry.pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(row1, text="订单号:").pack(side=tk.LEFT, padx=(0, 5))
        self.order_entry = ttk.Entry(row1, width=20)
        self.order_entry.pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(row1, text="工单号:").pack(side=tk.LEFT, padx=(0, 5))
        self.wo_entry = ttk.Entry(row1, width=20)
        self.wo_entry.pack(side=tk.LEFT)

        row2 = ttk.Frame(query_frame)
        row2.pack(fill=tk.X, pady=5)

        ttk.Label(row2, text="生产日期:").pack(side=tk.LEFT, padx=(0, 5))
        self.start_date = ttk.Entry(row2, width=12)
        self.start_date.pack(side=tk.LEFT)
        self.start_date.insert(0, (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))

        ttk.Label(row2, text="~").pack(side=tk.LEFT, padx=5)
        self.end_date = ttk.Entry(row2, width=12)
        self.end_date.pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(row2, text="周期:").pack(side=tk.LEFT, padx=(0, 5))
        self.period_var = tk.StringVar(value="本月")
        period_menu = ttk.Combobox(row2, textvariable=self.period_var, width=10,
                                    values=["本周", "本月", "本季度", "自定义"])
        period_menu.pack(side=tk.LEFT)

        ttk.Button(row2, text="追溯查询", style='Primary.TButton',
                   command=self._do_query).pack(side=tk.LEFT, padx=20)

        cards_frame = ttk.Frame(self)
        cards_frame.pack(fill=tk.X, padx=10, pady=5)

        self.card_wo = StatCard(cards_frame, "工单数量", "0", "个")
        self.card_wo.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.card_batch = StatCard(cards_frame, "生产批次", "0", "批")
        self.card_batch.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.card_input = StatCard(cards_frame, "投入面积", "0", "m2")
        self.card_input.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.card_output = StatCard(cards_frame, "良品面积", "0", "m2")
        self.card_output.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.card_scrap = StatCard(cards_frame, "报废率", "0.0", "%")
        self.card_scrap.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        charts_frame = ttk.Frame(self)
        charts_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.chart_trend = ChartCanvas(charts_frame, width=450, height=220)
        self.chart_trend.pack(side=tk.LEFT, padx=5)

        self.chart_process = ChartCanvas(charts_frame, width=350, height=220)
        self.chart_process.pack(side=tk.LEFT, padx=5)

        flow_frame = ttk.LabelFrame(self, text="工艺流程追溯", padding=10)
        flow_frame.pack(fill=tk.X, padx=10, pady=5)

        self.flow_canvas = tk.Canvas(flow_frame, height=50, bg='#1e1e2e', highlightthickness=0)
        self.flow_canvas.pack(fill=tk.X)
        self._draw_flow([])

        table_frame = ttk.LabelFrame(self, text="工单明细", padding=10)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ("工单号", "料号", "批次", "投入面积", "良品面积", "报废面积", "开工日期", "状态")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)

        widths = {"工单号": 120, "料号": 150, "批次": 80, "投入面积": 90,
                  "良品面积": 90, "报废面积": 90, "开工日期": 110, "状态": 80}
        for col in columns:
            self.table.heading(col, text=col)
            self.table.column(col, width=widths[col])
        self.table.pack(fill=tk.BOTH, expand=True)

    def _draw_flow(self, process_list):
        self.flow_canvas.delete('all')
        processes = ["开料", "钻孔", "沉铜", "外层线路", "图电", "蚀刻", "阻焊", "文字", "成型", "终检"]
        colors = ['#00ff88' if p in process_list else '#444444' for p in processes]

        x = 20
        for i, (proc, color) in enumerate(zip(processes, colors)):
            self.flow_canvas.create_oval(x, 15, x + 60, 40, fill=color, outline='')
            self.flow_canvas.create_text(x + 30, 28, text=proc, fill='#ffffff', font=('Microsoft YaHei', 8))
            if i < len(processes) - 1:
                self.flow_canvas.create_line(x + 65, 28, x + 80, 28, fill='#666666', width=2)
            x += 90

    def _do_query(self):
        part = self.part_entry.get().strip()
        order = self.order_entry.get().strip()
        wo = self.wo_entry.get().strip()

        # 至少需要一个查询条件
        if not part and not order and not wo:
            messagebox.showwarning("输入错误", "请输入料号/订单号/工单号至少一个查询条件")
            return

        self.gui._set_status("正在精确查询...")
        self.gui.root.update()

        def do_query():
            # 使用新的工单追溯方法
            result = self.system.trace_work_orders(
                part or None,
                self.start_date.get().strip() or None,
                self.end_date.get().strip() or None
            )
            self.gui.root.after(0, lambda: self._on_query_result(result, part))

        threading.Thread(target=do_query, daemon=True).start()

    def _on_query_result(self, result, query_part=""):
        self.gui._set_status("已连接")

        for item in self.table.get_children():
            self.table.delete(item)

        if result.get("success"):
            data = result.get("data", {})

            # 更新统计卡片 - 使用真实数据
            total_wo = data.get("total_wo", 0)
            total_batch = data.get("total_batch", 0)
            input_pcs = data.get("total_input_pcs", 0)
            reject_pcs = data.get("total_reject_pcs", 0)
            scrap_rate = data.get("reject_rate", 0)

            self.card_wo.value_label.config(text=str(total_wo))
            self.card_batch.value_label.config(text=str(total_batch))
            self.card_input.value_label.config(text=f"{input_pcs:,}")
            self.card_output.value_label.config(text=f"{input_pcs - reject_pcs:,}")
            self.card_scrap.value_label.config(text=f"{scrap_rate:.2f}" if scrap_rate else "0")

            # 填充工单明细表 - 使用真实数据
            work_orders = data.get("work_orders", [])
            if work_orders:
                for wo in work_orders:
                    self.table.insert("", tk.END, values=(
                        wo.get("woNumber", ""),
                        wo.get("PartNumber", ""),
                        wo.get("PartName", ""),
                        f"{wo.get('PcsProduced', 0):,}",
                        f"{wo.get('PcsProduced', 0) - wo.get('RejectPcs', 0):,}",
                        f"{wo.get('RejectPcs', 0):,}",
                        str(wo.get("createDate", ""))[:10],
                        wo.get("status", "")
                    ))
            else:
                # 无数据时显示提示
                self.table.insert("", tk.END, values=("无工单数据", "", "", "", "", "", "", ""))

            # 绘制图表 - 使用真实数据
            self._show_charts(data)
        else:
            self._show_empty_state()

    def _show_charts(self, data):
        """使用真实数据绘制图表"""
        # 每日趋势图
        daily_trend = data.get("daily_trend", [])
        if daily_trend:
            trend_labels = [d["date"][-5:] for d in daily_trend[-10:]]
            trend_values = [d["input_pcs"] for d in daily_trend[-10:]]
            self.chart_trend.draw_line_chart(
                trend_values if trend_values else [0],
                title="每日投入PCS趋势",
                labels=trend_labels if trend_labels else ["无数据"]
            )
        else:
            self.chart_trend.draw_line_chart([0], title="每日投入PCS趋势", labels=["无数据"])

        # 工序分布饼图
        process_dist = data.get("process_distribution", [])
        if process_dist:
            process_labels = [p.get("ProcessName", p.get("ProcessID", ""))[:6] for p in process_dist[:5]]
            process_values = [p.get("RecordCount", 0) for p in process_dist[:5]]
            self.chart_process.draw_pie_chart(
                process_values if process_values else [1],
                title="工序分布",
                labels=process_labels if process_labels else ["无数据"]
            )
        else:
            self.chart_process.draw_pie_chart([1], title="工序分布", labels=["无数据"])

    def _on_shared_result(self, trace_data):
        """处理共享追溯数据（由主界面统一调用）"""
        wo_result = trace_data.get("wo", {})
        if wo_result.get("success"):
            self._on_query_result(wo_result, trace_data.get("part", ""))
        else:
            self._show_empty_state()

    def _show_empty_state(self):
        """无真实数据：清空显示，不展示估算值"""
        self.card_wo.value_label.config(text="0")
        self.card_batch.value_label.config(text="0")
        self.card_input.value_label.config(text="0")
        self.card_output.value_label.config(text="0")
        self.card_scrap.value_label.config(text="0")

        for item in self.table.get_children():
            self.table.delete(item)
        self.table.insert("", tk.END, values=("请先执行追溯查询", "", "", "", "", "", "", ""))

        self.chart_trend.draw_line_chart([0], title="每日产出面积趋势 (m2)", labels=["无数据"])
        self.chart_process.draw_pie_chart([1], title="工序分布", labels=["请连接数据库"])


class MRBAnalysisModule(ttk.Frame):
    """MRB数据分析模块"""

    def __init__(self, parent, system, gui):
        super().__init__(parent)
        self.system = system
        self.gui = gui
        self.query_part = ""  # 保存当前查询料号
        self._create_widgets()

    def _create_widgets(self):
        # 查询条件栏
        query_frame = ttk.LabelFrame(self, text="查询条件", padding=10)
        query_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        row = ttk.Frame(query_frame)
        row.pack(fill=tk.X)

        ttk.Label(row, text="料号:").pack(side=tk.LEFT, padx=(0, 5))
        self.part_entry = ttk.Entry(row, width=20)
        self.part_entry.pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(row, text="日期:").pack(side=tk.LEFT, padx=(0, 5))
        self.start_date = ttk.Entry(row, width=12)
        self.start_date.pack(side=tk.LEFT)
        self.start_date.insert(0, (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))

        ttk.Label(row, text="~").pack(side=tk.LEFT, padx=5)
        self.end_date = ttk.Entry(row, width=12)
        self.end_date.pack(side=tk.LEFT, padx=(0, 15))

        ttk.Button(row, text="执行MRB分析", style='Primary.TButton',
                   command=self._do_query).pack(side=tk.LEFT, padx=10)

        cards_frame = ttk.Frame(self)
        cards_frame.pack(fill=tk.X, padx=10, pady=5)

        self.card_scrap_area = StatCard(cards_frame, "报废总面积", "0", "m2")
        self.card_scrap_area.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.card_scrap_rate = StatCard(cards_frame, "报废率", "0.00", "%")
        self.card_scrap_rate.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.card_rework = StatCard(cards_frame, "返修面积", "0", "m2")
        self.card_rework.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.card_mrb_count = StatCard(cards_frame, "MRB单数", "0", "单")
        self.card_mrb_count.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        charts_frame = ttk.Frame(self)
        charts_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        defect_frame = ttk.LabelFrame(charts_frame, text="报废缺陷TOP10 (柏拉图)", padding=5)
        defect_frame.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.chart_defect = ChartCanvas(defect_frame, width=420, height=250)
        self.chart_defect.pack()

        respon_frame = ttk.LabelFrame(charts_frame, text="责任工序分布", padding=5)
        respon_frame.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.chart_respon = ChartCanvas(respon_frame, width=320, height=250)
        self.chart_respon.pack()

        trend_frame = ttk.LabelFrame(charts_frame, text="报废率月度趋势", padding=5)
        trend_frame.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.chart_trend = ChartCanvas(trend_frame, width=320, height=250)
        self.chart_trend.pack()

        table_frame = ttk.LabelFrame(self, text="报废缺陷明细", padding=10)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ("缺陷类型", "缺陷数量", "报废面积(m2)", "占比%", "累计占比%", "责任工序")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)

        widths = {"缺陷类型": 120, "缺陷数量": 80, "报废面积(m2)": 100, "占比%": 70, "累计占比%": 80, "责任工序": 100}
        for col in columns:
            self.table.heading(col, text=col)
            self.table.column(col, width=widths[col])
        self.table.pack(fill=tk.BOTH, expand=True)

        # 初始化空图表
        self._show_empty_charts()

    def _show_empty_charts(self):
        """显示空图表"""
        self.chart_defect.draw_bar_chart([0], title="缺陷数量", labels=["无数据"])
        self.chart_respon.draw_pie_chart([1], title="责任占比", labels=["无数据"])
        self.chart_trend.draw_line_chart([0], title="报废率趋势 (%)", labels=["无数据"], target_line=1.5)

    def _do_query(self):
        self.query_part = self.part_entry.get().strip()
        self.gui._set_status("正在MRB分析...")

        def do_query():
            result = self.system.trace_mrb_analysis(
                self.query_part or None,
                self.start_date.get().strip() or None,
                self.end_date.get().strip() or None
            )
            self.gui.root.after(0, lambda: self._on_query_result(result))

        threading.Thread(target=do_query, daemon=True).start()

    def _on_query_result(self, result):
        self.gui._set_status("已连接")

        for item in self.table.get_children():
            self.table.delete(item)

        if result["success"]:
            data = result.get("data", {})

            # 更新统计卡片 - 使用正确的字段名
            total_input = data.get("total_input_pcs", 0)
            total_reject = data.get("total_reject_pcs", 0)
            scrap_rate = data.get("scrap_rate", 0)
            mrb_count = len(data.get("defect_top10", []))

            self.card_scrap_area.value_label.config(text=f"{total_input:,}")
            self.card_scrap_rate.value_label.config(text=f"{scrap_rate:.2f}")
            self.card_rework.value_label.config(text=f"{total_reject:,}")
            self.card_mrb_count.value_label.config(text=str(mrb_count) if mrb_count else "1")

            # 填充缺陷明细表 - 使用正确的字段名
            defect_top10 = data.get("defect_top10", [])
            if defect_top10:
                total_reject_for_pct = total_reject if total_reject > 0 else 1
                cumulative = 0
                for d in defect_top10:
                    reject_count = d.get("TotalScrap", d.get("total_scrap", 0))
                    cumulative += reject_count
                    pct = (reject_count / total_reject_for_pct * 100)
                    self.table.insert("", tk.END, values=(
                        d.get("PartNumber", d.get("part_number", "")),
                        reject_count,
                        f"{reject_count:,}",
                        f"{pct:.1f}%",
                        f"{cumulative/total_reject_for_pct*100:.1f}%",
                        ""  # 责任工序字段暂不显示
                    ))
            else:
                # 无明细数据时显示汇总行
                self.table.insert("", tk.END, values=(
                    data.get("part_number", "汇总"),
                    total_reject,
                    f"{total_reject:,}",
                    "100.0%",
                    "100.0%",
                    ""
                ))

            # 绘制图表
            self._show_charts(data)

            msg = f"MRB分析完成: {mrb_count} 个MRB单, 报废率={scrap_rate}%"
            if self.query_part:
                msg = f"料号 {self.query_part}: {msg}"
            messagebox.showinfo("分析完成", msg)
        else:
            messagebox.showwarning("分析提示", result.get('error', '查询失败') + "\n\n已清空显示，不做估算。")
            self._show_empty_state()

    def _show_charts(self, data):
        """使用真实数据绘制图表"""
        # 缺陷TOP10柱状图 - 使用正确的字段名
        defect_top10 = data.get("defect_top10", [])
        if defect_top10:
            labels = [str(d.get("PartNumber", d.get("part_number", "")))[:8] for d in defect_top10[:8]]
            values = [d.get("TotalScrap", d.get("total_scrap", 0)) for d in defect_top10[:8]]
            colors = ['#ff6b6b' if v > 10 else '#ff9f43' if v > 5 else '#00d4ff' for v in values]
            self.chart_defect.draw_bar_chart(values, title="报废PCS TOP8", labels=labels, colors=colors)
        else:
            # 无明细时用汇总数据
            total_reject = data.get("total_reject_pcs", 0)
            self.chart_defect.draw_bar_chart([total_reject] if total_reject else [0], 
                title="报废PCS", labels=[data.get("part_number", "汇总")[:8]] if total_reject else ["无数据"])

        # 责任工序饼图 - 使用 process_distribution 或默认
        process_dist = data.get("process_distribution", [])
        if process_dist:
            labels = [p.get("ProcessName", p.get("process_name", ""))[:8] for p in process_dist[:6]]
            values = [p.get("RecordCount", p.get("count", 0)) for p in process_dist[:6]]
            self.chart_respon.draw_pie_chart(values, title="报废分布", labels=labels)
        else:
            self.chart_respon.draw_pie_chart([1], title="报废分布", labels=["无明细"])

        # 报废率趋势（使用月度数据）
        monthly_trend = data.get("monthly_trend", [])
        if monthly_trend:
            labels = [m["month"][-5:] for m in monthly_trend[-6:]]
            values = [m.get("scrap_rate", 0) for m in monthly_trend[-6:]]
            self.chart_trend.draw_line_chart(values, title="报废率趋势 (%)",
                                            labels=labels, target_line=1.5)
        else:
            # 如果没有月度数据，显示当前报废率值
            scrap_rate = data.get("scrap_rate", 0)
            self.chart_trend.draw_line_chart([scrap_rate], title="报废率 (%)",
                                            labels=["当前"], target_line=2.0)

    def _on_shared_result(self, trace_data):
        """处理共享追溯数据（由主界面统一调用）"""
        mrb_result = trace_data.get("mrb", {})
        if mrb_result.get("success"):
            self._on_query_result(mrb_result)
        else:
            self._show_empty_state()

    def _show_empty_state(self):
        """无数据状态：清空卡片与图表，不展示任何估算值"""
        for card in (self.card_scrap_area, self.card_scrap_rate, self.card_rework, self.card_mrb_count):
            card.value_label.config(text="—")

        for item in self.table.get_children():
            self.table.delete(item)
        self.table.insert("", tk.END, values=("无数据（未查询或查询失败）", "", "", "", "", ""))

        self.chart_defect.draw_bar_chart([0], title="报废PCS", labels=["无数据"])
        self.chart_respon.draw_pie_chart([1], title="报废分布", labels=["无数据"])
        self.chart_trend.draw_line_chart([0], title="报废率 (%)", labels=["无数据"], target_line=1.5)


class MESDataModule(ttk.Frame):
    """MES生产数据模块"""

    def __init__(self, parent, system, gui):
        super().__init__(parent)
        self.system = system
        self.gui = gui
        self.query_part = ""
        self._create_widgets()

    def _create_widgets(self):
        # 查询条件栏
        query_frame = ttk.LabelFrame(self, text="查询条件", padding=10)
        query_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        row = ttk.Frame(query_frame)
        row.pack(fill=tk.X)

        ttk.Label(row, text="料号:").pack(side=tk.LEFT, padx=(0, 5))
        self.part_entry = ttk.Entry(row, width=20)
        self.part_entry.pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(row, text="日期:").pack(side=tk.LEFT, padx=(0, 5))
        self.start_date = ttk.Entry(row, width=12)
        self.start_date.pack(side=tk.LEFT)
        self.start_date.insert(0, (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"))

        ttk.Label(row, text="~").pack(side=tk.LEFT, padx=5)
        self.end_date = ttk.Entry(row, width=12)
        self.end_date.pack(side=tk.LEFT, padx=(0, 15))

        ttk.Button(row, text="查询MES数据", style='Primary.TButton',
                   command=self._do_query).pack(side=tk.LEFT, padx=10)

        cards_frame = ttk.Frame(self)
        cards_frame.pack(fill=tk.X, padx=10, pady=5)

        self.card_process = StatCard(cards_frame, "工序数", "0", "道")
        self.card_process.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.card_yield = StatCard(cards_frame, "平均良率", "0.00", "%")
        self.card_yield.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.card_oee = StatCard(cards_frame, "设备OEE", "0", "%")
        self.card_oee.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.card_chemical = StatCard(cards_frame, "药水异常", "0", "次")
        self.card_chemical.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        charts_frame = ttk.Frame(self)
        charts_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        yield_frame = ttk.LabelFrame(charts_frame, text="工序良率对比", padding=5)
        yield_frame.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.chart_yield = ChartCanvas(yield_frame, width=400, height=250)
        self.chart_yield.pack()

        equip_frame = ttk.LabelFrame(charts_frame, text="设备状态分布", padding=5)
        equip_frame.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.chart_equip = ChartCanvas(equip_frame, width=320, height=250)
        self.chart_equip.pack()

        spc_frame = ttk.LabelFrame(charts_frame, text="关键参数SPC控制图", padding=5)
        spc_frame.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.chart_spc = ChartCanvas(spc_frame, width=320, height=250)
        self.chart_spc.pack()

        table_frame = ttk.LabelFrame(self, text="工序生产数据明细", padding=10)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ("工序名称", "工序ID", "总检验", "合格", "不良", "良率%", "时间范围")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)

        widths = {"工序名称": 100, "工序ID": 80, "总检验": 80, "合格": 70, "不良": 70, "良率%": 70, "时间范围": 180}
        for col in columns:
            self.table.heading(col, text=col)
            self.table.column(col, width=widths[col])
        self.table.pack(fill=tk.BOTH, expand=True)

        # 初始化空图表
        self._show_empty_charts()

    def _show_empty_charts(self):
        """显示空图表"""
        self.chart_yield.draw_bar_chart([0], title="工序良率 (%)", labels=["无数据"])
        self.chart_equip.draw_pie_chart([1], title="设备状态", labels=["无数据"])
        self.chart_spc.draw_line_chart([0], title="参数趋势", labels=["无数据"])

    def _do_query(self):
        self.query_part = self.part_entry.get().strip()
        self.gui._set_status("正在查询MES数据...")

        def do_query():
            result = self.system.trace_mes_data(
                self.query_part or None,
                self.start_date.get().strip() or None,
                self.end_date.get().strip() or None
            )
            self.gui.root.after(0, lambda: self._on_query_result(result))

        threading.Thread(target=do_query, daemon=True).start()

    def _on_query_result(self, result):
        self.gui._set_status("已连接")

        for item in self.table.get_children():
            self.table.delete(item)

        if result["success"]:
            data = result.get("data", {})

            # 更新统计卡片 - 使用正确的字段名
            process_yield = data.get("process_yield", [])
            equipment_status = data.get("equipment_status", [])
            chemistry_anomaly = data.get("chemistry_anomaly", [])

            process_count = len(process_yield)
            if process_yield:
                avg_yield = sum(float(p.get("PassRate", 0)) for p in process_yield) / process_count
            else:
                avg_yield = 0
            
            # 设备状态：统计运行中数量
            running_count = sum(1 for e in equipment_status if e.get("Status") == "运行")
            equipment_oee = (running_count / max(len(equipment_status), 1)) * 100 if equipment_status else 0
            chemical_alerts = len(chemistry_anomaly) if chemistry_anomaly else 0

            self.card_process.value_label.config(text=str(process_count))
            self.card_yield.value_label.config(text=f"{avg_yield:.2f}" if avg_yield else "0")
            self.card_oee.value_label.config(text=f"{equipment_oee:.1f}" if equipment_oee else "0")
            self.card_chemical.value_label.config(text=str(chemical_alerts))

            # 填充工序明细表 - 使用 process_yield 字段
            if process_yield:
                for p in process_yield:
                    process_id = p.get("ProcessID", "")
                    inspect_count = p.get("InspectCount", 0)
                    pass_count = p.get("PassCount", 0)
                    pass_rate = float(p.get("PassRate", 0))
                    ng_count = inspect_count - pass_count
                    self.table.insert("", tk.END, values=(
                        f"工序{str(process_id)[-4:]}",  # 显示工序ID后4位作为名称
                        str(process_id),
                        f"{inspect_count:,}",
                        f"{pass_count:,}",
                        f"{ng_count:,}",
                        f"{pass_rate:.2f}%",
                        ""
                    ))
            else:
                self.table.insert("", tk.END, values=("无工序数据", "", "", "", "", "", ""))

            # 绘制图表
            self._show_charts(data)

            msg = f"查询完成: {process_count} 个工序, 平均良率={avg_yield}%"
            if self.query_part:
                msg = f"料号 {self.query_part}: {msg}"
            messagebox.showinfo("查询完成", msg)
        else:
            messagebox.showwarning("查询提示", result.get('error', 'MES查询失败') + "\n\n已清空显示，不做估算。")
            self._show_empty_state()

    def _show_charts(self, data):
        """使用真实数据绘制图表"""
        # 工序良率柱状图 - 使用 process_yield
        process_yield = data.get("process_yield", [])
        if process_yield:
            labels = [f"P{i+1}" for i in range(min(len(process_yield), 10))]
            values = [float(p.get("PassRate", 0)) for p in process_yield[:10]]
            colors = ['#00ff88' if v >= 98 else '#ff9f43' if v >= 95 else '#ff6b6b' for v in values]
            self.chart_yield.draw_bar_chart(values, title="工序良率 TOP10 (%)", labels=labels, colors=colors)
        else:
            self.chart_yield.draw_bar_chart([0], title="工序良率 (%)", labels=["无数据"])

        # 设备状态饼图 - 使用 equipment_status
        equip_status = data.get("equipment_status", [])
        if equip_status:
            status_counts = {"运行": 0, "待机": 0, "维护": 0, "故障": 0}
            for e in equip_status:
                status = e.get("Status", "待机")
                if status in status_counts:
                    status_counts[status] += 1
                else:
                    status_counts["待机"] += 1
            labels = [k for k, v in status_counts.items() if v > 0]
            values = [v for k, v in status_counts.items() if v > 0]
            if labels:
                self.chart_equip.draw_pie_chart(values, title="设备状态", labels=labels)
            else:
                self.chart_equip.draw_pie_chart([1], title="设备状态", labels=["无数据"])
        else:
            self.chart_equip.draw_pie_chart([1], title="设备状态", labels=["无数据"])

        # SPC参数图 - 使用 chemistry_anomaly 或工序良率作为趋势
        spc_data = data.get("spc_data", [])
        if spc_data:
            labels = [str(d.get("RecordTime", ""))[-8:-3] for d in spc_data[-10:]]
            values = [float(d.get("ParamValue") or 0) for d in spc_data[-10:]]
            target = float(spc_data[0].get("TargetValue") or 0) if spc_data else None
            self.chart_spc.draw_line_chart(values, title="参数趋势",
                                          labels=labels, target_line=target)
        else:
            # 使用工序良率作为趋势图
            if process_yield:
                values = [float(p.get("PassRate", 0)) for p in process_yield[:10]]
                labels = [f"P{i+1}" for i in range(len(values))]
                self.chart_spc.draw_line_chart(values, title="良率趋势 (%)",
                                              labels=labels, target_line=98)
            else:
                self.chart_spc.draw_line_chart([0], title="良率趋势", labels=["无数据"])

    def _on_shared_result(self, trace_data):
        """处理共享追溯数据（由主界面统一调用）"""
        mes_result = trace_data.get("mes", {})
        if mes_result.get("success"):
            self._on_query_result(mes_result)
        else:
            self._show_empty_state()

    def _show_empty_state(self):
        """无数据状态：清空卡片与图表，不展示任何估算值"""
        for card in (self.card_process, self.card_yield, self.card_oee, self.card_chemical):
            card.value_label.config(text="—")

        for item in self.table.get_children():
            self.table.delete(item)
        self.table.insert("", tk.END, values=("无数据（未查询或查询失败）", "", "", "", "", "", ""))

        self.chart_yield.draw_bar_chart([0], title="工序良率 (%)", labels=["无数据"])
        self.chart_equip.draw_pie_chart([1], title="设备状态", labels=["无数据"])
        self.chart_spc.draw_line_chart([0], title="参数趋势", labels=["无数据"])


class MaterialTraceModule(ttk.Frame):
    """物料追溯模块"""

    def __init__(self, parent, system, gui):
        super().__init__(parent)
        self.system = system
        self.gui = gui
        self.query_part = ""
        self._create_widgets()

    def _create_widgets(self):
        # 查询条件栏
        query_frame = ttk.LabelFrame(self, text="查询条件", padding=10)
        query_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        row = ttk.Frame(query_frame)
        row.pack(fill=tk.X)

        ttk.Label(row, text="料号:").pack(side=tk.LEFT, padx=(0, 5))
        self.part_entry = ttk.Entry(row, width=20)
        self.part_entry.pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(row, text="日期:").pack(side=tk.LEFT, padx=(0, 5))
        self.start_date = ttk.Entry(row, width=12)
        self.start_date.pack(side=tk.LEFT)
        self.start_date.insert(0, (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))

        ttk.Label(row, text="~").pack(side=tk.LEFT, padx=5)
        self.end_date = ttk.Entry(row, width=12)
        self.end_date.pack(side=tk.LEFT, padx=(0, 15))

        ttk.Button(row, text="物料追溯", style='Primary.TButton',
                   command=self._do_query).pack(side=tk.LEFT, padx=10)

        cards_frame = ttk.Frame(self)
        cards_frame.pack(fill=tk.X, padx=10, pady=5)

        self.card_batch = StatCard(cards_frame, "原料批次", "0", "批")
        self.card_batch.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.card_supplier = StatCard(cards_frame, "供应商", "0", "家")
        self.card_supplier.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.card_iqc = StatCard(cards_frame, "来料合格率", "0.0", "%")
        self.card_iqc.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.card_trace = StatCard(cards_frame, "追溯覆盖率", "0", "%")
        self.card_trace.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        charts_frame = ttk.Frame(self)
        charts_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        supplier_frame = ttk.LabelFrame(charts_frame, text="供应商来料质量对比", padding=5)
        supplier_frame.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.chart_supplier = ChartCanvas(supplier_frame, width=400, height=250)
        self.chart_supplier.pack()

        material_frame = ttk.LabelFrame(charts_frame, text="原料使用分布", padding=5)
        material_frame.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)

        self.chart_material = ChartCanvas(material_frame, width=320, height=250)
        self.chart_material.pack()

        chain_frame = ttk.LabelFrame(self, text="物料追溯链路", padding=10)
        chain_frame.pack(fill=tk.X, padx=10, pady=5)

        self.chain_canvas = tk.Canvas(chain_frame, height=80, bg='#1e1e2e', highlightthickness=0)
        self.chain_canvas.pack(fill=tk.X)
        self._draw_chain([])

        table_frame = ttk.LabelFrame(self, text="供应商质量明细", padding=10)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ("供应商", "编码", "来料总数", "合格数", "合格率", "质量等级", "状态")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)

        widths = {"供应商": 150, "编码": 80, "来料总数": 100, "合格数": 100,
                 "合格率": 80, "质量等级": 80, "状态": 80}
        for col in columns:
            self.table.heading(col, text=col)
            self.table.column(col, width=widths[col])
        self.table.pack(fill=tk.BOTH, expand=True)

        # 初始化空图表
        self._show_empty_charts()

    def _draw_chain(self, has_data):
        """绘制物料追溯链路"""
        self.chain_canvas.delete('all')
        chain = ["铜箔供应商", "来料批次", "IQC检验", "仓库存储", "投料生产", "成品入库", "客户使用"]
        colors = ['#00d4ff', '#00ff88', '#00ff88', '#00d4ff', '#ff9f43', '#00ff88', '#9b59b6']

        x = 30
        for i, (step, color) in enumerate(zip(chain, colors)):
            self.chain_canvas.create_oval(x, 25, x + 80, 55, fill=color, outline='')
            self.chain_canvas.create_text(x + 40, 40, text=step, fill='#ffffff', font=('Microsoft YaHei', 8))
            if i < len(chain) - 1:
                self.chain_canvas.create_line(x + 85, 40, x + 110, 40, fill='#666666', width=2, arrow=tk.LAST)
            x += 120

    def _show_empty_charts(self):
        """显示空图表"""
        self.chart_supplier.draw_bar_chart([0], title="供应商来料合格率 (%)", labels=["无数据"])
        self.chart_material.draw_pie_chart([1], title="原料使用", labels=["无数据"])

    def _do_query(self):
        self.query_part = self.part_entry.get().strip()
        self.gui._set_status("正在物料追溯...")

        def do_query():
            result = self.system.trace_material(
                self.query_part or None,
                self.start_date.get().strip() or None,
                self.end_date.get().strip() or None
            )
            self.gui.root.after(0, lambda: self._on_query_result(result))

        threading.Thread(target=do_query, daemon=True).start()

    def _on_query_result(self, result):
        self.gui._set_status("已连接")

        for item in self.table.get_children():
            self.table.delete(item)

        if result["success"]:
            data = result.get("data", {})

            # 更新统计卡片 - 使用正确的字段名
            supplier_quality = data.get("supplier_quality", [])
            material_lots = data.get("material_lots", [])
            
            batch_count = len(material_lots) if material_lots else len(supplier_quality)
            supplier_count = len(supplier_quality)
            iqc_sample = data.get("iqc_sample_count") or 0
            iqc_pass_rate = data.get("iqc_pass_rate")
            trace_coverage = data.get("trace_coverage")

            self.card_batch.value_label.config(text=str(batch_count))
            self.card_supplier.value_label.config(text=str(supplier_count))
            self.card_iqc.value_label.config(
                text=f"{float(iqc_pass_rate):.1f}" if iqc_sample and iqc_pass_rate is not None else "—"
            )
            self.card_trace.value_label.config(
                text=f"{float(trace_coverage):.0f}" if trace_coverage is not None else "—"
            )

            # 填充供应商质量明细表 - 使用 supplier_quality
            if supplier_quality:
                for s in supplier_quality:
                    self.table.insert("", tk.END, values=(
                        s.get("supplier", ""),
                        s.get("code", ""),
                        f"{s.get('total', 0):,}",
                        f"{s.get('pass', 0):,}",
                        f"{s.get('rate', 0):.1f}%",
                        "A" if s.get('rate', 0) >= 95 else "B",
                        ""
                    ))
            else:
                self.table.insert("", tk.END, values=("无供应商数据", "", "", "", "", "", ""))

            # 绘制图表
            self._show_charts(data)

            # 更新链路图状态
            if batch_count > 0:
                self._draw_chain(True)
            else:
                self._draw_chain(False)

            msg = f"物料追溯完成: {batch_count} 个批次, {supplier_count} 家供应商"
            if self.query_part:
                msg = f"料号 {self.query_part}: {msg}"
            messagebox.showinfo("追溯完成", msg)
        else:
            messagebox.showwarning("追溯提示", result.get('error', '物料追溯失败') + "\n\n已清空显示，不做估算。")
            self._show_empty_state()

    def _show_charts(self, data):
        """使用真实数据绘制图表"""
        # 供应商质量柱状图 - 使用正确的字段名
        supplier_quality = data.get("supplier_quality", [])
        if supplier_quality:
            labels = [s.get("supplier", "")[:8] for s in supplier_quality[:8]]
            values = [float(s.get("rate", s.get("pass_rate", 0))) for s in supplier_quality[:8]]
            colors = ['#00ff88' if v >= 98 else '#ff9f43' if v >= 95 else '#ff6b6b' for v in values]
            self.chart_supplier.draw_bar_chart(values, title="供应商来料合格率 (%)", labels=labels, colors=colors)
        else:
            self.chart_supplier.draw_bar_chart([0], title="供应商来料合格率 (%)", labels=["无数据"])

        # 原料使用分布饼图 - 使用 supplier_quality 作为替代
        if supplier_quality:
            labels = [s.get("supplier", "")[:8] for s in supplier_quality[:6]]
            values = [s.get("total", s.get("count", 1)) for s in supplier_quality[:6]]
            self.chart_material.draw_pie_chart(values, title="供应商来料批次", labels=labels)
        else:
            self.chart_material.draw_pie_chart([1], title="供应商来料批次", labels=["无数据"])

    def _on_shared_result(self, trace_data):
        """处理共享追溯数据（由主界面统一调用）"""
        mat_result = trace_data.get("material", {})
        if mat_result.get("success"):
            self._on_query_result(mat_result)
        else:
            self._show_empty_state()

    def _show_empty_state(self):
        """无数据状态：清空卡片与图表，不展示任何估算值"""
        for card in (self.card_batch, self.card_supplier, self.card_iqc, self.card_trace):
            card.value_label.config(text="—")

        for item in self.table.get_children():
            self.table.delete(item)
        self.table.insert("", tk.END, values=("无供应商数据", "", "", "", "", "", ""))

        self.chart_supplier.draw_bar_chart([0], title="供应商来料合格率 (%)", labels=["无数据"])
        self.chart_material.draw_pie_chart([1], title="供应商来料批次", labels=["无数据"])
        self._draw_chain(True)


# ═══════════════════════════════════════════════════════════════
# GUI主界面
# ═══════════════════════════════════════════════════════════════

class QualityIntelligenceGUI:
    """智能品质分析系统图形界面"""

    def __init__(self, system, root: tk.Tk = None):
        self.system = system
        self.current_trace_data = None

        if root is None:
            self.root = tk.Tk()
            self.root.withdraw()
        else:
            self.root = root

        self.root.title("智能品质分析系统 v2.0 - 产品全流程追溯")
        self.root.geometry("1400x900")
        self.root.minsize(1200, 700)
        self.root.configure(bg='#1e1e2e')

        self._setup_style()
        self._create_widgets()
        self.root.after(100, self._on_load)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        if root is None:
            self.root.deiconify()

    def _setup_style(self):
        style = ttk.Style()
        style.theme_use('clam')

        style.configure('.', background='#1e1e2e', foreground='#ffffff', fieldbackground='#2a2a3e')
        style.configure('TFrame', background='#1e1e2e')
        style.configure('TLabelframe', background='#1e1e2e', foreground='#ffffff')
        style.configure('TLabelframe.Label', background='#1e1e2e', foreground='#00d4ff')
        style.configure('TLabel', background='#1e1e2e', foreground='#ffffff')
        style.configure('TEntry', fieldbackground='#2a2a3e', foreground='#ffffff', insertcolor='#ffffff')
        style.configure('TButton', background='#2a2a3e', foreground='#ffffff')
        style.configure('TNotebook', background='#1e1e2e')
        style.configure('TNotebook.Tab', background='#2a2a3e', foreground='#ffffff', padding=[10, 5])

        style.configure("Title.TLabel", font=("Microsoft YaHei", 14, "bold"))
        style.configure("Header.TLabel", font=("Microsoft YaHei", 11, "bold"))
        style.configure("Normal.TLabel", font=("Microsoft YaHei", 10))
        style.configure("Primary.TButton", font=("Microsoft YaHei", 10), padding=8)
        style.configure("Secondary.TButton", font=("Microsoft YaHei", 9), padding=5)
        style.configure("Critical.TLabel", foreground="#ff6b6b", font=("Microsoft YaHei", 10, "bold"))
        style.configure("Warning.TLabel", foreground="#ff9f43", font=("Microsoft YaHei", 10, "bold"))
        style.configure("Success.TLabel", foreground="#00ff88", font=("Microsoft YaHei", 10))
        style.configure('StatCard.TFrame', background='#2a2a3e', relief=tk.RAISED, borderwidth=1)

    def _create_widgets(self):
        main_container = ttk.Frame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        toolbar = ttk.Frame(main_container)
        toolbar.pack(fill=tk.X, pady=(0, 10))

        self.status_label = ttk.Label(toolbar, text="系统状态: 未连接", style="Normal.TLabel")
        self.status_label.pack(side=tk.LEFT, padx=5)

        self.connect_btn = ttk.Button(toolbar, text="连接数据库",
            command=self._connect_database, style="Primary.TButton")
        self.connect_btn.pack(side=tk.LEFT, padx=5)

        self.refresh_btn = ttk.Button(toolbar, text="刷新",
            command=self._refresh_dashboard, style="Secondary.TButton")
        self.refresh_btn.pack(side=tk.LEFT, padx=5)

        self.learn_btn = ttk.Button(toolbar, text="自我学习",
            command=self._run_self_learning, style="Secondary.TButton")
        self.learn_btn.pack(side=tk.LEFT, padx=5)

        self.notebook = ttk.Notebook(main_container)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self._create_dashboard_tab()
        self._create_trace_tab()
        self._create_alert_tab()
        self._create_8d_tab()
        self._create_knowledge_tab()

    def _create_dashboard_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="仪表板")

        conn_frame = ttk.LabelFrame(tab, text="连接状态", padding=10)
        conn_frame.pack(fill=tk.X, padx=10, pady=10)

        self.erp_status = ttk.Label(conn_frame, text="ERP: 未连接", style="Normal.TLabel")
        self.erp_status.pack(side=tk.LEFT, padx=20)
        self.mes_status = ttk.Label(conn_frame, text="MES: 未连接", style="Normal.TLabel")
        self.mes_status.pack(side=tk.LEFT, padx=20)

        alert_frame = ttk.LabelFrame(tab, text="预警概览", padding=10)
        alert_frame.pack(fill=tk.X, padx=10, pady=10)

        self.critical_label = ttk.Label(alert_frame, text="紧急: 0", style="Normal.TLabel")
        self.critical_label.pack(side=tk.LEFT, padx=20)
        self.warning_label = ttk.Label(alert_frame, text="警告: 0", style="Normal.TLabel")
        self.warning_label.pack(side=tk.LEFT, padx=20)
        self.total_alerts_label = ttk.Label(alert_frame, text="总计: 0", style="Normal.TLabel")
        self.total_alerts_label.pack(side=tk.LEFT, padx=20)

        recent_frame = ttk.LabelFrame(tab, text="最新预警", padding=10)
        recent_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        columns = ("时间", "料号", "类型", "消息")
        self.alert_tree = ttk.Treeview(recent_frame, columns=columns, show="headings", height=10)
        for col in columns:
            self.alert_tree.heading(col, text=col)
            self.alert_tree.column(col, width=150)
        self.alert_tree.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(recent_frame, orient=tk.VERTICAL, command=self.alert_tree.yview)
        self.alert_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        kb_frame = ttk.LabelFrame(tab, text="知识库统计", padding=10)
        kb_frame.pack(fill=tk.X, padx=10, pady=10)

        self.kb_patterns_label = ttk.Label(kb_frame, text="不良模式: 0", style="Normal.TLabel")
        self.kb_patterns_label.pack(side=tk.LEFT, padx=20)
        self.kb_8d_label = ttk.Label(kb_frame, text="8D报告: 0", style="Normal.TLabel")
        self.kb_8d_label.pack(side=tk.LEFT, padx=20)
        self.kb_cases_label = ttk.Label(kb_frame, text="改善案例: 0", style="Normal.TLabel")
        self.kb_cases_label.pack(side=tk.LEFT, padx=20)

    def _create_trace_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="产品追溯")

        # ═══════════════════════════════════════════════════════════
        # 统一查询入口
        # ═══════════════════════════════════════════════════════════
        unified_frame = ttk.LabelFrame(tab, text="统一追溯入口", padding=15)
        unified_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        # 第一行：料号输入
        input_row = ttk.Frame(unified_frame)
        input_row.pack(fill=tk.X, pady=5)

        ttk.Label(input_row, text="料号:", font=("Microsoft YaHei", 10, "bold")).pack(side=tk.LEFT, padx=(0, 5))
        self.trace_part_entry = ttk.Entry(input_row, width=25, font=("Microsoft YaHei", 10))
        self.trace_part_entry.pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(input_row, text="生产日期:").pack(side=tk.LEFT, padx=(0, 5))
        self.trace_start_date = ttk.Entry(input_row, width=12, font=("Microsoft YaHei", 10))
        self.trace_start_date.pack(side=tk.LEFT)
        self.trace_start_date.insert(0, (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))

        ttk.Label(input_row, text="~").pack(side=tk.LEFT, padx=5)
        self.trace_end_date = ttk.Entry(input_row, width=12, font=("Microsoft YaHei", 10))
        self.trace_end_date.pack(side=tk.LEFT, padx=(0, 20))

        # 第二行：按钮行
        btn_row = ttk.Frame(unified_frame)
        btn_row.pack(fill=tk.X, pady=5)

        self.trace_query_btn = ttk.Button(btn_row, text="▶ 一键追溯", 
            command=self._do_full_trace, style="Primary.TButton", width=15)
        self.trace_query_btn.pack(side=tk.LEFT, padx=5)

        self.export_btn = ttk.Button(btn_row, text="📊 导出报告", 
            command=self._export_full_report, style="Secondary.TButton", width=15)
        self.export_btn.pack(side=tk.LEFT, padx=5)

        # 状态提示
        self.trace_status = ttk.Label(btn_row, text="请输入料号并点击'一键追溯'", 
            style="Normal.TLabel", foreground="#888888")
        self.trace_status.pack(side=tk.LEFT, padx=20)

        # ═══════════════════════════════════════════════════════════
        # 概览卡片（查询后显示汇总）
        # ═══════════════════════════════════════════════════════════
        self.overview_frame = ttk.LabelFrame(tab, text="追溯概览", padding=10)
        self.overview_frame.pack(fill=tk.X, padx=10, pady=5)

        self.overview_cards = {}
        overview_items = [
            ("工单数", "0", "个"),
            ("投入PCS", "0", "PCS"),
            ("报废PCS", "0", "PCS"),
            ("报废率", "0.00", "%"),
            ("MRB报废", "0", "PCS"),
            ("检验PCS", "0", "PCS"),
        ]
        for i, (title, val, unit) in enumerate(overview_items):
            card = StatCard(self.overview_frame, title, val, unit)
            card.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)
            self.overview_cards[title] = card

        # ═══════════════════════════════════════════════════════════
        # 详细数据Notebook
        # ═══════════════════════════════════════════════════════════
        module_notebook = ttk.Notebook(tab)
        module_notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.module_query = TraceQueryModule(module_notebook, self.system, self)
        module_notebook.add(self.module_query, text="工单明细")

        self.module_mrb = MRBAnalysisModule(module_notebook, self.system, self)
        module_notebook.add(self.module_mrb, text="MRB分析")

        self.module_mes = MESDataModule(module_notebook, self.system, self)
        module_notebook.add(self.module_mes, text="MES数据")

        self.module_material = MaterialTraceModule(module_notebook, self.system, self)
        module_notebook.add(self.module_material, text="物料追溯")

        # 存储追溯数据供导出使用
        self.trace_data = None

    def _do_full_trace(self):
        """执行完整追溯查询"""
        part = self.trace_part_entry.get().strip()
        if not part:
            messagebox.showwarning("输入错误", "请输入料号")
            return

        self.trace_query_btn.config(state=tk.DISABLED, text="查询中...")
        self.trace_status.config(text="正在查询...", foreground="#ff9f43")
        self.root.update()

        def do_query():
            try:
                # 1. 工单追溯
                wo_result = self.system.trace_work_orders(
                    part, 
                    self.trace_start_date.get().strip() or None,
                    self.trace_end_date.get().strip() or None
                )
                # 2. MRB分析
                mrb_result = self.system.trace_mrb_analysis(
                    part,
                    self.trace_start_date.get().strip() or None,
                    self.trace_end_date.get().strip() or None
                )
                # 3. MES数据
                mes_result = self.system.trace_mes_data(
                    part,
                    self.trace_start_date.get().strip() or None,
                    self.trace_end_date.get().strip() or None
                )
                # 4. 物料追溯
                mat_result = self.system.trace_material(
                    part,
                    self.trace_start_date.get().strip() or None,
                    self.trace_end_date.get().strip() or None
                )

                # 汇总数据
                self.trace_data = {
                    "part": part,
                    "date_range": f"{self.trace_start_date.get()} ~ {self.trace_end_date.get()}",
                    "wo": wo_result,
                    "mrb": mrb_result,
                    "mes": mes_result,
                    "material": mat_result
                }

                self.root.after(0, self._on_trace_complete)
            except Exception as e:
                self.root.after(0, lambda: self._on_trace_error(str(e)))

        threading.Thread(target=do_query, daemon=True).start()

    def _on_trace_complete(self):
        """追溯查询完成，更新界面"""
        self.trace_query_btn.config(state=tk.NORMAL, text="▶ 一键追溯")
        self.trace_status.config(text=f"查询完成 - {self.trace_data['part']}", foreground="#00ff88")

        # 更新概览卡片
        wo_data = self.trace_data.get("wo", {}).get("data", {})
        mrb_data = self.trace_data.get("mrb", {}).get("data", {})

        self.overview_cards["工单数"].value_label.config(text=str(wo_data.get("total_wo", 0)))
        self.overview_cards["投入PCS"].value_label.config(text=f"{wo_data.get('total_input_pcs', 0):,}")
        self.overview_cards["报废PCS"].value_label.config(text=f"{wo_data.get('total_reject_pcs', 0):,}")
        self.overview_cards["报废率"].value_label.config(text=f"{wo_data.get('scrap_rate', 0):.2f}")
        self.overview_cards["MRB报废"].value_label.config(text=f"{mrb_data.get('total_reject_pcs', 0):,}")
        self.overview_cards["检验PCS"].value_label.config(text=f"{mrb_data.get('total_inspect_pcs', 0):,}")

        # 更新各模块
        self.module_query._on_shared_result(self.trace_data)
        self.module_mrb._on_shared_result(self.trace_data)
        self.module_mes._on_shared_result(self.trace_data)
        self.module_material._on_shared_result(self.trace_data)

    def _on_trace_error(self, error):
        self.trace_query_btn.config(state=tk.NORMAL, text="▶ 一键追溯")
        self.trace_status.config(text=f"查询失败: {error}", foreground="#ff6b6b")
        messagebox.showerror("查询错误", f"追溯查询失败:\n{error}")

    def _export_full_report(self):
        """导出完整追溯报告"""
        if not self.trace_data:
            messagebox.showwarning("无数据", "请先执行追溯查询")
            return

        # 询问导出格式
        format_choice = messagebox.askyesnocancel(
            "导出格式选择",
            "是: 导出Excel格式 (.xlsx)\n否: 导出HTML格式 (.html)\n取消: 取消导出",
            parent=self.root
        )
        if format_choice is None:
            return

        if format_choice:  # Excel
            self._export_excel_report()
        else:  # HTML
            self._export_html_report()

    def _export_html_report(self):
        """导出HTML报告（包含真实数据）"""
        if not self.trace_data:
            messagebox.showwarning("无数据", "请先执行追溯查询")
            return

        default_name = f"追溯报告_{self.trace_data['part']}_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
        file_path = filedialog.asksaveasfilename(
            defaultextension=".html",
            filetypes=[("HTML文件", "*.html"), ("所有文件", "*.*")],
            initialfile=default_name
        )
        if not file_path:
            return

        self._set_status("正在生成HTML报告...")
        threading.Thread(target=lambda: self._do_export_html(file_path), daemon=True).start()

    def _do_export_html(self, file_path):
        """执行HTML报告导出"""
        try:
            # 获取追溯数据
            wo_data = self.trace_data.get("wo", {}).get("data", {})
            mrb_data = self.trace_data.get("mrb", {}).get("data", {})
            mes_data = self.trace_data.get("mes", {}).get("data", {})
            material_data = self.trace_data.get("material", {}).get("data", {})

            # 调用系统生成HTML
            result = self.system.generate_html_report(
                part_number=self.trace_data['part'],
                date_range=self.trace_data.get('date_range'),
                wo_data=wo_data,
                mrb_data=mrb_data,
                mes_data=mes_data,
                material_data=material_data
            )

            if result.get("success"):
                html_content = result["data"]["html"]
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
                self.root.after(0, lambda: self._on_html_export_complete(file_path))
            else:
                self.root.after(0, lambda: messagebox.showerror("导出失败", f"生成失败:\n{result.get('error', '未知错误')}"))
                self.root.after(0, lambda: self._set_status("导出失败"))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("导出失败", f"导出失败:\n{str(e)}"))
            self.root.after(0, lambda: self._set_status("导出失败"))

    def _export_excel_report(self):
        """导出Excel报告"""
        default_name = f"追溯报告_{self.trace_data['part']}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        file_path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel文件", "*.xlsx"), ("所有文件", "*.*")],
            initialfile=default_name
        )
        if not file_path:
            return

        self._set_status("正在导出报告...")
        threading.Thread(target=lambda: self._do_export(file_path), daemon=True).start()

    def _do_export(self, file_path):
        """执行报告导出"""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from openpyxl.utils import get_column_letter

            wb = Workbook()
            ws = wb.active
            ws.title = "追溯报告"

            # 样式定义
            header_font = Font(name='微软雅黑', size=14, bold=True, color='FFFFFF')
            header_fill = PatternFill(start_color='2E75B6', end_color='2E75B6', fill_type='solid')
            sub_font = Font(name='微软雅黑', size=11, bold=True)
            sub_fill = PatternFill(start_color='D9E1F2', end_color='D9E1F2', fill_type='solid')
            thin_border = Border(
                left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin')
            )

            row = 1
            # 标题
            ws.merge_cells(f'A{row}:G{row}')
            ws[f'A{row}'] = f"产品追溯报告 - {self.trace_data['part']}"
            ws[f'A{row}'].font = header_font
            ws[f'A{row}'].fill = header_fill
            ws[f'A{row}'].alignment = Alignment(horizontal='center')
            ws.row_dimensions[row].height = 30

            row = 2
            ws[f'A{row}'] = f"追溯日期: {self.trace_data['date_range']}"
            ws[f'A{row}'].font = Font(name='微软雅黑', size=10)
            ws[f'A{row}'].alignment = Alignment(horizontal='left')

            # 概览数据
            row = 4
            ws[f'A{row}'] = "一、追溯概览"
            ws[f'A{row}'].font = sub_font
            ws[f'A{row}'].fill = sub_fill
            ws.merge_cells(f'A{row}:G{row}')

            wo_data = self.trace_data.get("wo", {}).get("data", {})
            mrb_data = self.trace_data.get("mrb", {}).get("data", {})

            overview_data = [
                ("工单数量", wo_data.get('total_wo', 0), "个"),
                ("投入PCS", wo_data.get('total_input_pcs', 0), "PCS"),
                ("报废PCS", wo_data.get('total_reject_pcs', 0), "PCS"),
                ("报废率", wo_data.get('scrap_rate', 0), "%"),
                ("MRB报废", mrb_data.get('total_reject_pcs', 0), "PCS"),
                ("检验PCS", mrb_data.get('total_inspect_pcs', 0), "PCS"),
            ]
            row = 5
            for i, (label, value, unit) in enumerate(overview_data):
                col = i * 2 + 1
                ws.cell(row=row, column=col, value=label).font = Font(name='微软雅黑', bold=True)
                ws.cell(row=row, column=col+1, value=f"{value} {unit}")

            # 工单明细
            row = 8
            ws[f'A{row}'] = "二、工单明细"
            ws[f'A{row}'].font = sub_font
            ws[f'A{row}'].fill = sub_fill
            ws.merge_cells(f'A{row}:G{row}')

            row = 9
            headers = ["工单号", "料号", "投入PCS", "报废PCS", "缺陷PCS", "报废率", "开工日期"]
            for i, h in enumerate(headers):
                cell = ws.cell(row=row, column=i+1, value=h)
                cell.font = Font(name='微软雅黑', bold=True, color='FFFFFF')
                cell.fill = PatternFill(start_color='5B9BD5', end_color='5B9BD5', fill_type='solid')
                cell.border = thin_border
                cell.alignment = Alignment(horizontal='center')

            wo_list = wo_data.get('work_orders', [])
            for idx, wo in enumerate(wo_list):
                row = 10 + idx
                values = [
                    wo.get('wo_number', ''),
                    wo.get('part_code', ''),
                    wo.get('input_pcs', 0),
                    wo.get('reject_pcs', 0),
                    wo.get('defect_pcs', 0),
                    f"{wo.get('scrap_rate', 0):.2f}%",
                    wo.get('start_date', '')[:10] if wo.get('start_date') else ''
                ]
                for i, v in enumerate(values):
                    cell = ws.cell(row=row, column=i+1, value=v)
                    cell.border = thin_border
                    if i > 0:
                        cell.alignment = Alignment(horizontal='right')

            # MRB分析
            mrb_start = row + 3
            ws[f'A{mrb_start}'] = "三、MRB分析"
            ws[f'A{mrb_start}'].font = sub_font
            ws[f'A{mrb_start}'].fill = sub_fill
            ws.merge_cells(f'A{mrb_start}:G{mrb_start}')

            row = mrb_start + 1
            headers = ["料号", "投入PCS", "报废PCS", "缺陷PCS", "报废率"]
            for i, h in enumerate(headers):
                cell = ws.cell(row=row, column=i+1, value=h)
                cell.font = Font(name='微软雅黑', bold=True, color='FFFFFF')
                cell.fill = PatternFill(start_color='5B9BD5', end_color='5B9BD5', fill_type='solid')
                cell.border = thin_border
                cell.alignment = Alignment(horizontal='center')

            mrb_list = mrb_data.get('top_defects', [])[:20]
            for idx, mrb in enumerate(mrb_list):
                row = mrb_start + 2 + idx
                values = [
                    mrb.get('part_code', ''),
                    mrb.get('input_pcs', 0),
                    mrb.get('reject_pcs', 0),
                    mrb.get('defect_pcs', 0),
                    f"{mrb.get('reject_rate', 0):.2f}%"
                ]
                for i, v in enumerate(values):
                    cell = ws.cell(row=row, column=i+1, value=v)
                    cell.border = thin_border

            # 调整列宽
            for i in range(1, 8):
                ws.column_dimensions[get_column_letter(i)].width = 18

            wb.save(file_path)
            self.root.after(0, lambda: self._on_export_complete(file_path))
        except ImportError:
            self.root.after(0, lambda: messagebox.showerror("导出失败", "请安装 openpyxl: pip install openpyxl"))
            self.root.after(0, lambda: self._set_status("导出失败"))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("导出失败", f"导出失败:\n{str(e)}"))
            self.root.after(0, lambda: self._set_status("导出失败"))

    def _on_export_complete(self, file_path):
        self._set_status("已连接")
        messagebox.showinfo("导出成功", f"报告已导出至:\n{file_path}")

    def _on_html_export_complete(self, file_path):
        """HTML报告导出完成"""
        self._set_status("已连接")
        msg = messagebox.askyesno("导出成功", f"HTML报告已生成:\n{file_path}\n\n是否立即打开查看？", parent=self.root)
        if msg:
            import webbrowser
            webbrowser.open('file:///' + file_path.replace('\\', '/'))


    def _create_alert_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="预警管理")

        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, padx=10, pady=10)

        ttk.Button(toolbar, text="执行预警检查",
            command=self._run_alert_check, style="Primary.TButton").pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="刷新列表",
            command=self._refresh_alerts, style="Secondary.TButton").pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="确认选中预警",
            command=self._acknowledge_selected_alert, style="Secondary.TButton").pack(side=tk.LEFT, padx=5)

        list_frame = ttk.Frame(tab)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        columns = ("状态", "料号", "预警类型", "消息", "建议", "时间")
        self.alerts_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=20)

        widths = {"状态": 80, "料号": 150, "预警类型": 100, "消息": 300, "建议": 200, "时间": 150}
        for col in columns:
            self.alerts_tree.heading(col, text=col)
            self.alerts_tree.column(col, width=widths[col])
        self.alerts_tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.alerts_tree.yview)
        self.alerts_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _create_8d_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="8D报告")

        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(toolbar, text="料号:").pack(side=tk.LEFT, padx=5)
        self._8d_part_entry = ttk.Entry(toolbar, width=25)
        self._8d_part_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(toolbar, text="生成8D报告",
            command=self._generate_8d_report, style="Primary.TButton").pack(side=tk.LEFT, padx=20)
        ttk.Button(toolbar, text="刷新列表",
            command=self._refresh_8d_list, style="Secondary.TButton").pack(side=tk.LEFT, padx=5)

        list_frame = ttk.LabelFrame(tab, text="8D报告列表", padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        columns = ("报告编号", "状态", "创建人", "创建时间")
        self._8d_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=15)
        for col in columns:
            self._8d_tree.heading(col, text=col)
            self._8d_tree.column(col, width=200)
        self._8d_tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        detail_frame = ttk.LabelFrame(tab, text="报告详情", padding=10)
        detail_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self._8d_detail_text = scrolledtext.ScrolledText(detail_frame, width=100, height=15, font=("Consolas", 9))
        self._8d_detail_text.pack(fill=tk.BOTH, expand=True)

        export_btn = ttk.Button(detail_frame, text="导出8D报告",
            command=self._export_8d_report, style="Secondary.TButton")
        export_btn.pack(side=tk.RIGHT, pady=5)

    def _create_knowledge_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="知识库")

        pattern_frame = ttk.LabelFrame(tab, text="不良模式库", padding=10)
        pattern_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        columns = ("模式ID", "模式名称", "不良类型", "优先级", "使用次数", "成功率")
        self.pattern_tree = ttk.Treeview(pattern_frame, columns=columns, show="headings", height=15)
        for col in columns:
            self.pattern_tree.heading(col, text=col)
            self.pattern_tree.column(col, width=120)
        self.pattern_tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        btn_frame = ttk.Frame(pattern_frame)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=5)

        ttk.Button(btn_frame, text="刷新",
            command=self._refresh_patterns, style="Secondary.TButton").pack(side=tk.LEFT, padx=5)

        stats_frame = ttk.LabelFrame(tab, text="知识库统计", padding=10)
        stats_frame.pack(fill=tk.X, padx=10, pady=10)

        self.knowledge_stats_text = scrolledtext.ScrolledText(stats_frame, height=8, font=("微软雅黑", 9))
        self.knowledge_stats_text.pack(fill=tk.X)

    def _on_load(self):
        self._refresh_dashboard()

    def _on_closing(self):
        if messagebox.askokcancel("退出", "确定要退出系统吗？"):
            self.system.disconnect()
            self.root.destroy()

    def _connect_database(self):
        self._set_status("正在连接...")
        def do_connect():
            success = self.system.connect()
            self.root.after(0, lambda: self._on_connect_result(success))
        threading.Thread(target=do_connect, daemon=True).start()

    def _on_connect_result(self, success: bool):
        if success:
            self._set_status("已连接")
            self.connect_btn.config(text="断开连接")
            self.connect_btn.config(command=self._disconnect_database)
            messagebox.showinfo("连接成功", "数据库连接成功！")
        else:
            self._set_status("连接失败")
            messagebox.showerror("连接失败", "请检查数据库配置和网络连接。")
        self._refresh_dashboard()

    def _disconnect_database(self):
        self.system.disconnect()
        self._set_status("已断开")
        self.connect_btn.config(text="连接数据库")
        self.connect_btn.config(command=self._connect_database)
        self._refresh_dashboard()

    def _set_status(self, message: str):
        self.status_label.config(text=f"系统状态: {message}")

    def _refresh_dashboard(self):
        if not self.system.connected:
            self.erp_status.config(text="ERP: 未连接", style="Warning.TLabel")
            self.mes_status.config(text="MES: 未连接", style="Warning.TLabel")
            return

        status = self.system.connection_status
        self.erp_status.config(text="ERP: 已连接" if status["erp"] else "ERP: 未连接",
            style="Success.TLabel" if status["erp"] else "Warning.TLabel")
        self.mes_status.config(text="MES: 已连接" if status["mes"] else "MES: 未连接",
            style="Success.TLabel" if status["mes"] else "Warning.TLabel")

        result = self.system.get_dashboard_data()
        if result["success"]:
            data = result["data"]
            alerts = data["alerts"]
            self.critical_label.config(text=f"紧急: {alerts['critical']}")
            self.warning_label.config(text=f"警告: {alerts['warning']}")
            self.total_alerts_label.config(text=f"总计: {alerts['total']}")

            for item in self.alert_tree.get_children():
                self.alert_tree.delete(item)
            for alert in alerts["recent"]:
                icon = "!" if alert["alert_type"] == "critical" else "~"
                self.alert_tree.insert("", tk.END, values=(
                    alert["create_time"][:16], alert["part_number"], icon, alert["alert_message"][:50]))

            kb = data.get("knowledge_base", {})
            self.kb_patterns_label.config(text=f"不良模式: {kb.get('defect_patterns', 0)}")
            self.kb_8d_label.config(text=f"8D报告: {kb.get('eight_d_reports', 0)}")
            self.kb_cases_label.config(text=f"改善案例: {kb.get('improvement_cases', 0)}")

    def _do_trace_analysis(self):
        part_number = self.module_query.part_entry.get().strip()
        if not part_number:
            messagebox.showwarning("输入错误", "请输入料号")
            return
        self.module_query._do_query()

    def _on_trace_result(self, result: dict, part_number: str):
        self._set_status("已连接")
        if result["success"]:
            data = result["data"]
            self.current_trace_data = data
            self.module_query.trace_result_text = None

    def _generate_8d_from_trace(self):
        if not hasattr(self, 'current_trace_data') or not self.current_trace_data:
            messagebox.showwarning("操作错误", "请先执行追溯分析")
            return
        part_number = self.module_query.part_entry.get().strip()
        if part_number:
            self._generate_8d_report(part_number)

    def _run_alert_check(self):
        self._set_status("正在检查预警...")
        def do_check():
            result = self.system.check_alerts()
            self.root.after(0, lambda: self._on_alert_check_result(result))
        threading.Thread(target=do_check, daemon=True).start()

    def _on_alert_check_result(self, result: dict):
        self._set_status("已连接")
        if result["success"]:
            messagebox.showinfo("预警检查", f"发现 {result['count']} 个预警")
            self._refresh_alerts()
            self._refresh_dashboard()
        else:
            messagebox.showerror("预警检查失败", result.get('error'))

    def _refresh_alerts(self):
        for item in self.alerts_tree.get_children():
            self.alerts_tree.delete(item)
        result = self.system.get_active_alerts()
        if result["success"]:
            for alert in result["data"]:
                ack = "已确认" if alert["acknowledged"] else "未确认"
                self.alerts_tree.insert("", tk.END, values=(
                    ack, alert["part_number"], alert["alert_type"],
                    alert["alert_message"][:60], alert["recommendation"][:40], alert["create_time"][:16]))

    def _acknowledge_selected_alert(self):
        selection = self.alerts_tree.selection()
        if not selection:
            messagebox.showwarning("选择错误", "请先选择要确认的预警")
            return
        item = self.alerts_tree.item(selection[0])
        part_number = item["values"][1]
        result = self.system.get_active_alerts()
        if result["success"]:
            for alert in result["data"]:
                if alert["part_number"] == part_number:
                    self.system.acknowledge_alert(alert["alert_id"], "GUI用户")
                    break
        messagebox.showinfo("确认成功", "预警已确认")
        self._refresh_alerts()

    def _generate_8d_report(self, part_number=None):
        if part_number is None:
            part_number = self._8d_part_entry.get().strip()
        if not part_number:
            messagebox.showwarning("输入错误", "请输入料号")
            return
        self._set_status("正在生成8D报告...")
        def do_generate():
            result = self.system.generate_8d_report(part_number)
            self.root.after(0, lambda: self._on_8d_generate_result(result))
        threading.Thread(target=do_generate, daemon=True).start()

    def _on_8d_generate_result(self, result: dict):
        self._set_status("已连接")
        if result["success"]:
            self._8d_detail_text.delete("1.0", tk.END)
            self._8d_detail_text.insert(tk.END, result["markdown"])
            self._refresh_8d_list()
            messagebox.showinfo("生成成功", "8D报告已生成！")
        else:
            messagebox.showerror("生成失败", result.get('error'))

    def _refresh_8d_list(self):
        for item in self._8d_tree.get_children():
            self._8d_tree.delete(item)
        result = self.system.get_8d_reports()
        if result["success"]:
            for report in result["data"]:
                self._8d_tree.insert("", tk.END, values=(
                    report["report_id"], report["status"], report["created_by"], report["created_time"][:16]))

    def _export_trace_report(self):
        if not hasattr(self, 'current_trace_data') or not self.current_trace_data:
            messagebox.showwarning("导出错误", "请先执行追溯分析")
            return
        file_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")])
        if file_path:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(self.current_trace_data, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("导出成功", f"报告已保存到: {file_path}")

    def _export_8d_report(self):
        content = self._8d_detail_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showwarning("导出错误", "请先生成8D报告")
            return
        file_path = filedialog.asksaveasfilename(defaultextension=".md", filetypes=[("Markdown文件", "*.md"), ("所有文件", "*.*")])
        if file_path:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("导出成功", f"报告已保存到: {file_path}")

    def _run_self_learning(self):
        if not self.system.connected:
            messagebox.showwarning("连接错误", "请先连接数据库")
            return
        self._set_status("正在自我学习...")
        def do_learn():
            result = self.system.run_self_learning()
            self.root.after(0, lambda: self._on_learning_result(result))
        threading.Thread(target=do_learn, daemon=True).start()

    def _on_learning_result(self, result: dict):
        self._set_status("已连接")
        if result["success"]:
            data = result["data"]
            msg = f"自我学习完成！\n\n学到的新模式: {data.get('patterns_learned', 0)}\n\n知识库统计:\n"
            for k, v in data.get('knowledge_stats', {}).items():
                msg += f"  {k}: {v}\n"
            messagebox.showinfo("学习完成", msg)
            self._refresh_dashboard()
            self._refresh_patterns()
        else:
            messagebox.showerror("学习失败", result.get('error'))

    def _refresh_patterns(self):
        for item in self.pattern_tree.get_children():
            self.pattern_tree.delete(item)
        patterns = self.system.kb.get_all_patterns()
        for p in patterns:
            self.pattern_tree.insert("", tk.END, values=(
                p.pattern_id, p.pattern_name, ", ".join(p.defect_types[:3]),
                p.priority, p.occurrence_count, f"{p.success_rate:.1f}%"))

        stats = self.system.get_knowledge_stats()
        if stats["success"]:
            self.knowledge_stats_text.delete("1.0", tk.END)
            self.knowledge_stats_text.insert(tk.END, "知识库统计:\n\n")
            for k, v in stats["data"].items():
                self.knowledge_stats_text.insert(tk.END, f"  {k}: {v}\n")

    def run(self):
        self.root.mainloop()


def start_gui(system):
    gui = QualityIntelligenceGUI(system)
    gui.run()


if __name__ == "__main__":
    try:
        from app.quality_intelligence_main import QualityIntelligenceSystem
    except ImportError:
        from quality_intelligence_main import QualityIntelligenceSystem
    system = QualityIntelligenceSystem()
    start_gui(system)
