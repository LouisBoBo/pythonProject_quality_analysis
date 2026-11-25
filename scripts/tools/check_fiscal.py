# -*- coding: utf-8 -*-
import _bootstrap  # noqa: F401  # ensure project paths
import urllib.request
import json

BASE_URL = "http://localhost:8765"

def api(path):
    req = urllib.request.Request(f"{BASE_URL}{path}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))

# 测试近30天 vs FiscalPeriod
print("=" * 60)
print("测试不同时间范围的统计结果")
print("=" * 60)

# 近30天
print("\n1. 近30天数据（工单表）:")
r = api("/api/dashboard?fiscal_period=")  # 空参数使用近30天
# 实际上现在不支持空参数回退，需要直接检查

# 查看health_check接口测试FiscalPeriod
print("\n测试报废分析（FiscalPeriod=202604）:")
r2 = api("/api/scrap?part=&fiscal_period=202604")
print(f"  报废PCS: {r2.get('total_scrap_pcs', 0):,}")
print(f"  入库PCS: {r2.get('total_in_stock_pcs', 0):,}")
print(f"  报废率: {r2.get('scrap_rate', 0)}%")

# 查看周期列表
print("\n可用周期:")
r3 = api("/api/periods")
print(f"  {r3.get('fiscal_periods', [])}")
