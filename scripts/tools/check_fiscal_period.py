#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证FiscalPeriod周期码字段"""

import _bootstrap  # noqa: F401  # ensure project paths
import sys
import json
sys.path.insert(0, r"D:\AI助理\数据对接系统")
from erp_connector import get_erp

erp = get_erp()
erp.connect()

output = []

output.append("=" * 70)
output.append("【1. 按FiscalPeriod(周期码)统计报废数据】")
output.append("=" * 70)

r = erp.execute("""
    SELECT 
           FiscalPeriod,
           COUNT(*) AS record_count,
           SUM(QtyOfUnit) AS total_scrap
    FROM View_P_Inspection 
    WHERE MfgPartCode LIKE '%B60-10P06T17615A0%'
      AND InspectionResult = 'Scrapped'
    GROUP BY FiscalPeriod
    ORDER BY FiscalPeriod DESC
""")
if r.get('success') and r['data']:
    output.append("按 FiscalPeriod 统计:")
    for row in r['data']:
        output.append("  Period: %s, Records: %s, Scrap: %s" % (
            row.get('FiscalPeriod'), row.get('record_count'), row.get('total_scrap')))
else:
    output.append("查询失败: " + str(r.get('error')))

output.append("")
output.append("=" * 70)
output.append("【2. 对比CreateDate月份 vs FiscalPeriod】")
output.append("=" * 70)

r = erp.execute("""
    SELECT TOP 20
           CreateDate,
           FiscalPeriod,
           CONVERT(VARCHAR(6), CreateDate, 112) AS CreateDate6,
           QtyOfUnit
    FROM View_P_Inspection 
    WHERE MfgPartCode LIKE '%B60-10P06T17615A0%'
      AND InspectionResult = 'Scrapped'
    ORDER BY CreateDate DESC
""")
if r.get('success') and r['data']:
    output.append("CreateDate vs FiscalPeriod 对比:")
    for row in r['data']:
        output.append("  CreateDate: %s, FiscalPeriod: %s, CreateDate6: %s, Qty: %s" % (
            row.get('CreateDate'), row.get('FiscalPeriod'), 
            row.get('CreateDate6'), row.get('QtyOfUnit')))
else:
    output.append("查询失败: " + str(r.get('error')))

output.append("")
output.append("=" * 70)
output.append("【3. FiscalPeriod=202603的报废汇总】")
output.append("=" * 70)

r = erp.execute("""
    SELECT 
           FiscalPeriod,
           COUNT(*) AS record_count,
           SUM(QtyOfUnit) AS total_scrap
    FROM View_P_Inspection 
    WHERE MfgPartCode LIKE '%B60-10P06T17615A0%'
      AND InspectionResult = 'Scrapped'
      AND FiscalPeriod = '202603'
    GROUP BY FiscalPeriod
""")
if r.get('success') and r['data']:
    for row in r['data']:
        output.append("  Period: %s, Records: %s, Scrap: %s" % (
            row.get('FiscalPeriod'), row.get('record_count'), row.get('total_scrap')))
else:
    output.append("无数据或查询失败: " + str(r.get('error')))

output.append("")
output.append("=" * 70)
output.append("【4. 全厂可用周期码列表】")
output.append("=" * 70)

r = erp.execute("""
    SELECT DISTINCT TOP 24 FiscalPeriod
    FROM View_P_Inspection 
    WHERE InspectionResult = 'Scrapped'
    ORDER BY FiscalPeriod DESC
""")
if r.get('success') and r['data']:
    output.append("全厂可用周期码:")
    for row in r['data']:
        output.append("  - %s" % row.get('FiscalPeriod'))

# 写入文件
with open("fiscal_period_output.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(output))
print("Done. Output written to fiscal_period_output.txt")
