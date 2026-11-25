#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查ERP系统 - 简化版"""

import _bootstrap  # noqa: F401  # ensure project paths
import sys
import os

# 添加连接器路径
sys.path.insert(0, r"D:\AI助理\数据对接系统")
from erp_connector import get_erp

erp = get_erp()
erp.connect()  # 显式连接

print("ERP连接状态:", erp.connected if erp else "未连接")

print()
print("=" * 70)
print("【1. 检查View_P_Inspection表字段】")
print("=" * 70)
# 检查字段
r = erp.execute("SELECT TOP 3 CreateDate, PeriodCode, MonthCode, MfgPartCode, InspectionResult, QtyOfUnit FROM View_P_Inspection")
print("SQL执行结果:", r.get('success'))
if r.get('success') and r['data']:
    print("数据样例:")
    for row in r['data']:
        print("  CreateDate=%s, PeriodCode=%s, MonthCode=%s, QtyOfUnit=%s" % (
            row.get('CreateDate'), row.get('PeriodCode'), row.get('MonthCode'), row.get('QtyOfUnit')))
else:
    print("查询失败或无数据")
    print("错误:", r.get('error'))

print()
print("=" * 70)
print("【2. 检查具体料号数据】")
print("=" * 70)
part = "B60-10P06T17615A0"
r = erp.execute("""
    SELECT TOP 5 CreateDate, PeriodCode, MonthCode, QtyOfUnit
    FROM View_P_Inspection 
    WHERE MfgPartCode = '%s'
      AND InspectionResult = 'Scrapped'
""" % part)
print("SQL执行结果:", r.get('success'))
if r.get('success') and r['data']:
    print("料号 %s 报废数据:" % part)
    for row in r['data']:
        print("  CreateDate=%s, PeriodCode=%s, MonthCode=%s, QtyOfUnit=%s" % (
            row.get('CreateDate'), row.get('PeriodCode'), row.get('MonthCode'), row.get('QtyOfUnit')))
else:
    print("查询失败或无数据")
    print("错误:", r.get('error'))

print()
print("=" * 70)
print("【3. 用LIKE模糊匹配查询】")
print("=" * 70)
r = erp.execute("""
    SELECT TOP 5 CreateDate, PeriodCode, MonthCode, QtyOfUnit, MfgPartCode
    FROM View_P_Inspection 
    WHERE MfgPartCode LIKE '%%%s%%'
      AND InspectionResult = 'Scrapped'
""" % part)
print("SQL执行结果:", r.get('success'))
if r.get('success') and r['data']:
    print("料号 %s 报废数据(模糊):" % part)
    for row in r['data']:
        print("  MfgPartCode=%s, CreateDate=%s, PeriodCode=%s, MonthCode=%s, QtyOfUnit=%s" % (
            row.get('MfgPartCode'), row.get('CreateDate'), row.get('PeriodCode'), row.get('MonthCode'), row.get('QtyOfUnit')))
else:
    print("查询失败或无数据")
    print("错误:", r.get('error'))
