#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查View_P_Inspection表的实际字段"""

import _bootstrap  # noqa: F401  # ensure project paths
import sys
import json
sys.path.insert(0, r"D:\AI助理\数据对接系统")
from erp_connector import get_erp

erp = get_erp()
erp.connect()

output = []

output.append("ERP连接状态: " + str(erp.connected))

output.append("")
output.append("=" * 70)
output.append("【查看View_P_Inspection表的所有字段】")
output.append("=" * 70)

r = erp.execute("SELECT TOP 1 * FROM View_P_Inspection")
if r.get('success') and r['data']:
    output.append("字段列表:")
    for col in sorted(r['data'][0].keys()):
        val = r['data'][0][col]
        output.append("  - %s: %s" % (col, val))
else:
    output.append("查询失败: " + str(r.get('error')))

output.append("")
output.append("=" * 70)
output.append("【查看样号B60-10P06T17615A0的实际数据】")
output.append("=" * 70)

r = erp.execute("""
    SELECT TOP 3 CreateDate, MfgPartCode, InspectionResult, TType, QtyOfUnit
    FROM View_P_Inspection 
    WHERE MfgPartCode LIKE '%B60-10P06T17615A0%'
      AND InspectionResult = 'Scrapped'
    ORDER BY CreateDate DESC
""")
if r.get('success') and r['data']:
    output.append("数据样例:")
    for row in r['data']:
        output.append("  CreateDate: %s, MfgPartCode: %s, TType: %s, QtyOfUnit: %s" % (
            row.get('CreateDate'), row.get('MfgPartCode'), row.get('TType'), row.get('QtyOfUnit')))
else:
    output.append("查询失败: " + str(r.get('error')))

# 写入文件
with open("erp_fields_output.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(output))
print("Done. Output written to erp_fields_output.txt")
