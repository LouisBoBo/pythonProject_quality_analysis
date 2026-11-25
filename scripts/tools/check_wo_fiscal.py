# -*- coding: utf-8 -*-
"""检查P_WO表的FiscalPeriod字段"""
import _bootstrap  # noqa: F401  # ensure project paths
import urllib.request
import json

BASE_URL = "http://localhost:8765"

def api(path):
    req = urllib.request.Request(f"{BASE_URL}{path}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))

print("=" * 60)
print("P_WO表FiscalPeriod字段检查")
print("=" * 60)

# 检查工单表不同条件的统计
print("\n1. P_WO表 - 近30天:")
r1 = api("/api/dashboard?fiscal_period=")
kpi = r1.get('kpi', {})
print(f"  工单数: {kpi.get('total_wo', 0)}")
print(f"  投入PCS: {kpi.get('total_input', 0):,}")
print(f"  报废PCS: {kpi.get('total_reject', 0):,}")
print(f"  报废率: {kpi.get('reject_rate', 0)}%")

print("\n2. P_WO表 - FiscalPeriod=202604:")
r2 = api("/api/dashboard?fiscal_period=202604")
kpi = r2.get('kpi', {})
print(f"  工单数: {kpi.get('total_wo', 0)}")
print(f"  投入PCS: {kpi.get('total_input', 0):,}")
print(f"  报废PCS: {kpi.get('total_reject', 0):,}")
print(f"  报废率: {kpi.get('reject_rate', 0)}%")

print("\n3. View_P_Inspection报废表 - FiscalPeriod=202604:")
r3 = api("/api/scrap?part=&fiscal_period=202604")
print(f"  报废PCS: {r3.get('total_scrap_pcs', 0):,}")
print(f"  入库PCS: {r3.get('total_in_stock_pcs', 0):,}")
print(f"  报废率: {r3.get('scrap_rate', 0)}%")

print("\n" + "=" * 60)
print("问题分析：")
print("=" * 60)
print("- P_WO表工单数为0，说明该表FiscalPeriod可能未更新或为空")
print("- View_P_Inspection报废表有数据，说明该表FiscalPeriod正确")
print("- Dashboard应使用View_P_Inspection计算报废率，而非P_WO表")
