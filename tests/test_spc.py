# -*- coding: utf-8 -*-
"""SPC 重算口径单元测试：d2/1.128 常数、控制限、Cp/Cpk、状态判定。

运行：
    python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.pcb_quality_api import _spc_compute


class IMRTest(unittest.TestCase):
    def test_i_mr_sigma_and_limits(self):
        r = _spc_compute([10, 12, 11, 13, 12])
        # 移动极差 2,1,2,1，均值 1.5；σ = 1.5 / 1.128
        self.assertAlmostEqual(r["sigma"], 1.5 / 1.128, places=6)
        self.assertAlmostEqual(r["mean"], 11.6, places=6)
        self.assertAlmostEqual(r["ucl"], 11.6 + 3 * (1.5 / 1.128), places=6)
        self.assertAlmostEqual(r["lcl"], 11.6 - 3 * (1.5 / 1.128), places=6)
        self.assertEqual(r["limits_source"], "recomputed")
        self.assertEqual(r["sample_count"], 5)
        self.assertEqual(r["measurement_count"], 5)
        self.assertEqual(r["out_of_control"], 0)
        self.assertEqual(r["status"], "normal")

    def test_cp_cpk(self):
        r = _spc_compute([10, 12, 11, 13, 12], usl=20, lsl=2)
        sigma = 1.5 / 1.128
        self.assertAlmostEqual(r["cp"], 18 / (6 * sigma), places=6)
        self.assertAlmostEqual(
            r["cpk"], min((20 - 11.6) / (3 * sigma), (11.6 - 2) / (3 * sigma)), places=6
        )

    def test_zero_sigma_gives_no_capability(self):
        r = _spc_compute([5, 5, 5, 5, 5], usl=6, lsl=4)
        self.assertEqual(r["sigma"], 0.0)
        self.assertIsNone(r["ucl"])
        self.assertIsNone(r["cp"])
        self.assertIsNone(r["cpk"])

    def test_invalid_values_skipped(self):
        r = _spc_compute(["bad", 1, 2, 3, 4, 5])
        self.assertEqual(r["measurement_count"], 5)
        self.assertEqual(r["sample_count"], 5)
        self.assertEqual(r["status"], "normal")

    def test_empty(self):
        r = _spc_compute([])
        self.assertEqual(r["points"], [])
        self.assertIsNone(r["mean"])
        self.assertIsNone(r["status"])


class StatusTest(unittest.TestCase):
    def test_insufficient_below_five_points(self):
        r = _spc_compute([1, 2, 3, 4])
        self.assertEqual(r["status"], "insufficient")

    def test_out_of_spec_triggers_alert(self):
        r = _spc_compute([10, 10, 10, 25, 10], usl=20)
        self.assertEqual(r["out_of_spec"], 1)
        self.assertEqual(r["out_of_control"], 0)
        self.assertEqual(r["status"], "alert")


class StoredLimitsTest(unittest.TestCase):
    def test_stored_limits_used_verbatim(self):
        r = _spc_compute([1, 1, 1, 1, 2], ucl=1.9, cl=1.0, lcl=0.1)
        self.assertEqual(r["limits_source"], "stored")
        self.assertAlmostEqual(r["ucl"], 1.9)
        self.assertAlmostEqual(r["cl"], 1.0)
        self.assertAlmostEqual(r["lcl"], 0.1)
        self.assertEqual(r["out_of_control"], 1)
        self.assertEqual(r["status"], "alert")

    def test_stored_limits_no_false_positive(self):
        r = _spc_compute([1, 1, 1, 1, 2], ucl=2.1, cl=1.0, lcl=0.1)
        self.assertEqual(r["out_of_control"], 0)
        self.assertEqual(r["status"], "normal")


class XbarRTest(unittest.TestCase):
    def test_subgroup_means_and_d2(self):
        r = _spc_compute([1, 2, 3, 4, 2, 3, 1, 2, 3, 4], subgroup_size=2, usl=6, lsl=0)
        # 子组均值 1.5,3.5,2.5,1.5,3.5；极差均为 1；σ = R̄ / d2(2) = 1 / 1.128
        sigma = 1 / 1.128
        self.assertEqual(r["points"], [1.5, 3.5, 2.5, 1.5, 3.5])
        self.assertEqual(r["point_ranges"], [1.0] * 5)
        self.assertEqual(r["sample_count"], 5)
        self.assertEqual(r["measurement_count"], 10)
        self.assertAlmostEqual(r["sigma"], sigma, places=6)
        se = sigma / (2 ** 0.5)
        self.assertAlmostEqual(r["ucl"], 2.5 + 3 * se, places=6)
        self.assertAlmostEqual(r["lcl"], 2.5 - 3 * se, places=6)
        self.assertAlmostEqual(r["cp"], 6 / (6 * sigma), places=6)
        self.assertAlmostEqual(
            r["cpk"], min((6 - 2.5) / (3 * sigma), (2.5 - 0) / (3 * sigma)), places=6
        )
        self.assertEqual(r["status"], "normal")

    def test_trend_out_of_control_on_subgroup_means(self):
        # 均值 1.5..9.5，限值约 [3.62, 7.38]：1.5、3.5、7.5、9.5 四点越限
        r = _spc_compute(range(1, 11), subgroup_size=2)
        self.assertEqual(r["out_of_control"], 4)
        self.assertEqual(r["status"], "alert")

    def test_grouping_by_subgroup_ids(self):
        r = _spc_compute([1, 2, 1, 2], subgroup_size=2, subgroup_ids=["A", "A", "B", "B"])
        self.assertEqual(r["points"], [1.5, 1.5])
        self.assertEqual(r["sample_count"], 2)
        self.assertEqual(r["status"], "insufficient")


if __name__ == "__main__":
    unittest.main()
