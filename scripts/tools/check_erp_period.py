#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查ERP系统中的周期码字段"""

import _bootstrap  # noqa: F401  # ensure project paths
import sys
import os

# 添加连接器路径
sys.path.insert(0, r"D:\AI助理\数据对接系统")
from erp_connector import get_erp

erp = get_erp()

print("=" * 70)
print("【1. View_P_Inspection 表 TType=0 的所有字段】")
print("=" * 70)
r = erp.execute("SELECT TOP 1 * FROM View_P_Inspection WHERE TType = 0")
if r.get('success') and r['data']:
    print('字段列表:')
    for col in r['data'][0].keys():
        val = r['data'][0][col]
        print(f'  - {col}: {val}')

print()
print("=" * 70)
print("【2. View_P_Inspection 表 TType=1 的所有字段】")
print("=" * 70)
r = erp.execute("SELECT TOP 1 * FROM View_P_Inspection WHERE TType = 1")
if r.get('success') and r['data']:
    print('字段列表:')
    for col in r['data'][0].keys():
        val = r['data'][0][col]
        print(f'  - {col}: {val}')

print()
print("=" * 70)
print("【3. 检查B60-10P06T17615A0的Period相关字段】")
print("=" * 70)
r = erp.execute("""
    SELECT TOP 10 
           CreateDate, 
           PeriodCode, 
           MonthCode,
           MfgPartCode, 
           InspectionResult, 
           TType,
           QtyOfUnit
    FROM View_P_Inspection 
    WHERE MfgPartCode LIKE '%B60-10P06T17615A0%'
      AND InspectionResult = 'Scrapped'
    ORDER BY CreateDate DESC
""")
if r.get('success') and r['data']:
    for row in r['data']:
        print(f'  CreateDate: {row.get("CreateDate")}')
        print(f'  PeriodCode: {row.get("PeriodCode")}')
        print(f'  MonthCode: {row.get("MonthCode")}')
        print(f'  TType: {row.get("TType")}')
        print(f'  QtyOfUnit: {row.get("QtyOfUnit")}')
        print('  ---')

print()
print("=" * 70)
print("【4. 按PeriodCode统计报废数据】")
print("=" * 70)
r = erp.execute("""
    SELECT 
           PeriodCode,
           MonthCode,
           COUNT(*) AS record_count,
           SUM(QtyOfUnit) AS total_scrap
    FROM View_P_Inspection 
    WHERE MfgPartCode LIKE '%B60-10P06T17615A0%'
      AND InspectionResult = 'Scrapped'
    GROUP BY PeriodCode, MonthCode
    ORDER BY PeriodCode DESC
""")
if r.get('success') and r['data']:
    print('按周期码统计:')
    for row in r['data']:
        print(f'  PeriodCode: {row.get("PeriodCode")}, MonthCode: {row.get("MonthCode")}, 记录数: {row.get("record_count")}, 总报废: {row.get("total_scrap")}')

print()
print("=" * 70)
print("【5. 按CreateDate(YYYY-MM格式)统计报废数据】")
print("=" * 70)
r = erp.execute("""
    SELECT 
           CONVERT(VARCHAR(7), CreateDate, 120) AS Period,
           COUNT(*) AS record_count,
           SUM(QtyOfUnit) AS total_scrap
    FROM View_P_Inspection 
    WHERE MfgPartCode LIKE '%B60-10P06T17615A0%'
      AND InspectionResult = 'Scrapped'
    GROUP BY CONVERT(VARCHAR(7), CreateDate, 120)
    ORDER BY Period DESC
""")
if r.get('success') and r['data']:
    print('按CreateDate月份统计:')
    for row in r['data']:
        print(f'  月份: {row.get("Period")}, 记录数: {row.get("record_count")}, 总报废: {row.get("total_scrap")}')

print()
print("=" * 70)
print("【6. 对比两种周期口径的数据】")
print("=" * 70)
# 检查PeriodCode和CreateDate是否一致
r = erp.execute("""
    SELECT TOP 20
           CreateDate,
           PeriodCode,
           MonthCode,
           CONVERT(VARCHAR(7), CreateDate, 120) AS CreateDateMonth,
           QtyOfUnit
    FROM View_P_Inspection 
    WHERE MfgPartCode LIKE '%B60-10P06T17615A0%'
      AND InspectionResult = 'Scrapped'
    ORDER BY CreateDate DESC
""")
if r.get('success') and r['data']:
    print('CreateDate vs PeriodCode vs MonthCode:')
    for row in r['data']:
        create_date = row.get("CreateDate")
        period_code = row.get("PeriodCode")
        month_code = row.get("MonthCode")
        create_month = row.get("CreateDateMonth")
        print(f'  CreateDate: {create_date}, PeriodCode: {period_code}, MonthCode: {month_code}, CreateMonth: {create_month}')
