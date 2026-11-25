# -*- coding: utf-8 -*-
"""通过API对比系统数据与ERP日报数据"""
import _bootstrap  # noqa: F401  # ensure project paths
import urllib.request
import json
import sys

BASE_URL = "http://localhost:8765"

def api(path):
    try:
        req = urllib.request.Request(f"{BASE_URL}{path}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}

output = []

def out(s):
    output.append(str(s))

out("=" * 70)
out("系统数据 vs ERP日报 对比分析")
out("=" * 70)

# 检查API健康状态
health = api("/api/health")
out(f"\nAPI状态: {health}")

out("\n" + "-" * 70)
out("1. Dashboard数据（当前系统首页）")
out("-" * 70)

dashboard = api("/api/dashboard")
if "kpi" in dashboard:
    kpi = dashboard["kpi"]
    out(f"  总工单数: {kpi.get('total_wo', 0)}")
    out(f"  总投入PCS: {kpi.get('total_input', 0):,}")
    out(f"  总报废PCS: {kpi.get('total_reject', 0):,}")
    out(f"  报废率: {kpi.get('reject_rate', 0)}%")
    out(f"  [注意] Dashboard使用近30天数据，而ERP日报使用整月(FiscalPeriod)")
else:
    out(f"  Dashboard获取失败: {dashboard}")

out("\n" + "-" * 70)
out("2. ERP日报数据（2026-04-16）")
out("-" * 70)
out("  日期: 2026-04-16")
out("  月份: 2026年4月（周期码202604）")
out("  月生产面积: 58,086.87 m2")
out("  月总报废面积: 3,685.21 m2")
out("  月总报废率: 6.00%")

out("\n" + "-" * 70)
out("3. 使用周期码查询（FiscalPeriod=202604）")
out("-" * 70)

# 查询周期列表
periods = api("/api/periods")
out(f"  可用周期: {periods.get('fiscal_periods', [])}")

# 查询一个料号的4月份报废数据
part = "B60-10P06T17615A0"
out(f"\n  查询料号: {part} (2026年4月)")

# 使用fiscal_period查询
scrap = api(f"/api/scrap?part={part}&fiscal_period=202604")
if "scrap_rate" in scrap:
    out(f"  报废PCS: {scrap.get('total_scrap_pcs', 0):,}")
    out(f"  入库PCS: {scrap.get('total_in_stock_pcs', 0):,}")
    out(f"  报废率: {scrap.get('scrap_rate', 0)}%")
else:
    out(f"  报废数据: {scrap}")

out("\n" + "-" * 70)
out("4. 关键差异分析")
out("-" * 70)
out("  [问题1] Dashboard使用近30天，而ERP日报使用整月")
out("  [问题2] Dashboard使用工单表PCS统计，与ERP日报的面积统计不同")
out("  [解决方案] 首页也改用FiscalPeriod周期码统计，与ERP日报一致")

# 写入文件
with open("compare_output.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(output))
print("Done! Check compare_output.txt")
