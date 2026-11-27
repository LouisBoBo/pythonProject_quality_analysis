# -*- coding: utf-8 -*-
"""Excel 导出契约测试：输入一律用接口真实返回结构（ERP/MES 字段名）。

运行：
    python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import load_workbook

from app.excel_exporter import export_module_excel


def _rows(path, sheet):
    wb = load_workbook(path)
    ws = wb[sheet]
    return [[c.value for c in row] for row in ws.iter_rows()]


def _flat(rows):
    return [str(v) for row in rows for v in row if v not in (None, "")]


class ScrapExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = {
            "total_scrap_pcs": 1000,
            "total_in_stock_pcs": 9000,
            "scrap_rate": None,
            "defect_top10": [
                {"defect_type": "开路", "process": "蚀刻", "scrap_pcs": 321, "ratio": 32.1},
            ],
            "process_scrap": [{"process": "蚀刻", "scrap_pcs": 200, "ratio": 20.0}],
            "monthly_trend": [
                {"Day": "2026-11-01", "Month": "2026-11-01", "TotalScrap": 100,
                 "TotalInStock": 900, "ScrapRate": 10.0},
                {"Day": "2026-11-02", "Month": "2026-11-02", "TotalScrap": 50,
                 "TotalInStock": 0, "ScrapRate": None},
            ],
        }
        self.path = export_module_excel("scrap", self.data, self.tmp.name, extra={"part": "P1"})

    def test_defect_and_process_sheets(self):
        defects = _rows(self.path, "缺陷TOP")
        self.assertIn("开路", _flat(defects))
        self.assertIn("321", _flat(defects))
        self.assertIn("32.1", _flat(defects))
        proc = _flat(_rows(self.path, "工序报废"))
        self.assertIn("蚀刻", proc)

    def test_monthly_trend_capital_keys(self):
        trend = _rows(self.path, "月度趋势")
        flat = _flat(trend)
        self.assertIn("2026-11-01", flat)
        self.assertIn("100", flat)
        self.assertIn("900", flat)
        self.assertIn("10", flat)

    def test_overview_none_rate_not_printed_as_none(self):
        flat = _flat(_rows(self.path, "概览"))
        self.assertIn("报废率%: —", flat)


class IpqcExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = {
            "kpi": {"inspect_count": 50, "pass_rate": 80.0},
            "process_rates": [
                {"process_name": "钻孔", "total": 50, "pass_count": 40,
                 "ng_count": 10, "pass_rate": 80.0, "ng_rate": 20.0},
            ],
            "daily_trend": [
                {"day": "2026-11-01", "inspect_count": 50, "ng_count": 10, "ng_rate": 20.0},
            ],
            "customer_ng_top": [
                {"customer_code": "C01", "customer_name": "客户A", "total": 50,
                 "ng_count": 10, "ng_rate": 20.0},
            ],
        }
        self.path = export_module_excel("ipqc", self.data, self.tmp.name)

    def test_process_pass_and_ng_counts(self):
        flat = _flat(_rows(self.path, "工序合格率"))
        self.assertIn("钻孔", flat)
        self.assertIn("40", flat)
        self.assertIn("10", flat)
        self.assertIn("80", flat)

    def test_daily_trend_inspect_count(self):
        flat = _flat(_rows(self.path, "日趋势"))
        self.assertIn("2026-11-01", flat)
        self.assertIn("50", flat)
        self.assertIn("20", flat)


class ProcessExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = {
            "avg_yield": 88.0,
            "min_yield": 72.5,
            "process_yield": [
                {"ProcessID": 1, "ProcessName": "钻孔", "InspectCount": 20,
                 "PassCount": 15, "NgCount": 5, "PassRate": 75.0, "yield_rate": 75.0},
            ],
            "process_defects": [
                {"ProcessID": 1, "ProcessName": "钻孔", "DefectItem": "孔偏",
                 "DefectValue": "", "DefectCount": 5},
            ],
        }
        self.path = export_module_excel("process", self.data, self.tmp.name, extra={"part": "P1"})

    def test_process_yield_uses_capital_keys(self):
        flat = _flat(_rows(self.path, "工序良率"))
        self.assertIn("钻孔", flat)
        self.assertIn("75", flat)
        self.assertIn("20", flat)

    def test_process_defect_sheet(self):
        flat = _flat(_rows(self.path, "不良明细"))
        self.assertIn("孔偏", flat)
        self.assertIn("5", flat)


class CopqExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = {
            "kpi": {"copq_total": 1000.0, "open_count": 1},
            "by_customer": [{"customer_code": "C01", "customer_name": "客户A",
                             "count": 2, "amount": 500.0}],
            "by_product": [{"product_code": "P1", "product_name": "板A",
                            "count": 2, "amount": 500.0, "qty": 100}],
            "monthly": [{"month": "2026-11", "count": 2, "amount": 500.0}],
            "recent": [{
                "complaint_no": "CC-1", "customer_name": "客户A", "product_code": "P1",
                "product_name": "板A", "complaint_date": "2026-11-02",
                "complaint_type": "外观", "severity": 2, "status": "open",
                "cost_category": "客诉", "affected_qty": 100, "unit_cost": 5.0,
                "copq_amount": 500.0, "problem_description": "划伤", "assigned_to": "张三",
            }],
        }
        self.path = export_module_excel("copq", self.data, self.tmp.name)

    def test_recent_uses_real_api_keys(self):
        rows = _rows(self.path, "客诉明细")
        header = [c for c in rows[0] if c]
        self.assertIn("complaint_type", header)
        self.assertIn("problem_description", header)
        flat = _flat(rows)
        self.assertIn("划伤", flat)
        self.assertIn("CC-1", flat)


class HealthExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = {
            "part_number": "P1", "report_no": "HC-1",
            "score": {"total": 78, "grade": "B", "grade_name": "良好",
                      "scores": {"scrap": 26, "inspection": None},
                      "dim_max": {"scrap": 30, "inspection": 25}},
            "findings": [{"level": "warning", "dim": "报废", "title": "报废偏高", "detail": "x"}],
            "recommendations": [{"priority": "P1", "title": "降低报废", "action": "排查蚀刻"}],
        }
        self.path = export_module_excel("health", self.data, self.tmp.name, extra={"part": "P1"})

    def test_dimension_none_and_max(self):
        flat = _flat(_rows(self.path, "维度得分"))
        self.assertIn("—", flat)
        self.assertIn("30", flat)

    def test_recommendation_title_and_action(self):
        flat = _flat(_rows(self.path, "改善建议"))
        self.assertIn("降低报废", flat)
        self.assertIn("排查蚀刻", flat)


if __name__ == "__main__":
    unittest.main()
