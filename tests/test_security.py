# -*- coding: utf-8 -*-
"""安全相关单元测试：参数校验、周期解析、导出文件名、登录跳转白名单。

运行：
    python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import excel_exporter, pdf_generator
from app.auth_session import safe_internal_next
from app.pcb_quality_api import _CSP, _is_loopback, _period_parts, validate_params


class PeriodPartsTest(unittest.TestCase):
    def test_dash_and_compact(self):
        self.assertEqual(_period_parts("2026-11"), ("202611", "2026", "11"))
        self.assertEqual(_period_parts("202611"), ("202611", "2026", "11"))
        self.assertEqual(_period_parts(" 2026-01 "), ("202601", "2026", "01"))

    def test_invalid_returns_none(self):
        for bad in ("", None, "2026", "2026-1", "2026/11", "abc", "2026-111"):
            self.assertIsNone(_period_parts(bad), bad)


class ValidateParamsTest(unittest.TestCase):
    def test_clean_params_pass(self):
        params = {
            "part": ["B60P04L19865A0"],
            "period": ["2026-11"],
            "fiscal_period": ["202611"],
            "start": ["2026-01-01"],
            "end": ["2026-11-30"],
            "limit": ["50"],
            "days": ["30"],
            "acknowledged": ["true"],
        }
        self.assertIsNone(validate_params(params))

    def test_sql_metacharacters_rejected(self):
        for value in ("x' OR '1'='1", 'a"b', "a;b", "a--b", "a/*b", "a\nb"):
            self.assertIsNotNone(validate_params({"part": [value]}), value)

    def test_date_and_period_shape(self):
        self.assertIsNotNone(validate_params({"start": ["2026/01/01"]}))
        self.assertIsNotNone(validate_params({"end": ["2026-1-1"]}))
        self.assertIsNotNone(validate_params({"period": ["2026-1"]}))
        self.assertIsNotNone(validate_params({"fiscal_period": ["2026-11"]}))
        self.assertIsNotNone(validate_params({"limit": ["abc"]}))

    def test_token_skipped(self):
        self.assertIsNone(validate_params({"token": ["a'b;c"]}))


class ExportNameTest(unittest.TestCase):
    def test_pdf_safe_name_blocks_traversal(self):
        name = pdf_generator._safe_name("../../etc/passwd")
        self.assertNotIn("/", name)
        self.assertNotIn(".", name)

    def test_pdf_safe_name_keeps_chinese(self):
        self.assertEqual(pdf_generator._safe_name("品质体检"), "品质体检")

    def test_excel_safe_name_blocks_traversal_and_dots(self):
        name = excel_exporter._safe_name("../..\\evil.xlsx")
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertFalse(name.startswith("."))

    def test_excel_safe_name_fallback(self):
        self.assertEqual(excel_exporter._safe_name("", "export"), "export")
        self.assertEqual(excel_exporter._safe_name("...", "export"), "export")


class LoginRedirectTest(unittest.TestCase):
    def test_allows_internal_paths(self):
        self.assertEqual(safe_internal_next("/pcb_quality_system.html?x=1"), "/pcb_quality_system.html?x=1")
        self.assertEqual(safe_internal_next("/"), "/")

    def test_blocks_open_redirects(self):
        for bad in ("//evil.com", "https://evil.com", "/\\evil", "/../secret", "evil"):
            self.assertEqual(safe_internal_next(bad), "/pcb_quality_system.html", bad)


class SecurityHeadersTest(unittest.TestCase):
    def test_csp_locks_down_frames_and_external_connect(self):
        self.assertIn("frame-ancestors 'self'", _CSP)
        self.assertIn("object-src 'none'", _CSP)
        self.assertIn("connect-src 'self'", _CSP)

    def test_loopback_detection(self):
        for host in ("127.0.0.1", "localhost", "::1", " LOCALHOST "):
            self.assertTrue(_is_loopback(host), host)
        for host in ("0.0.0.0", "10.0.0.5", "175.178.238.31", ""):
            self.assertFalse(_is_loopback(host), host)


if __name__ == "__main__":
    unittest.main()
