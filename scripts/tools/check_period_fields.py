#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查ERP系统中的周期码字段"""

import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, '.')

from erp_connector import ERPConnector

erp = ERPConnector()

print("=" * 60)
print("【1. 查看View_P_Inspection表TType=0字段】")
print("=" * 60)
r = erp.execute("SELECT TOP 1 * FROM View_P_Inspection WHERE TType = 0")
if r.get('success') and r['data']:
    print('TType=0 字段列表:')
    for col in r['data'][0].keys():
        print(f'  - {col}')

print()
print("=" * 60)
print("【2. 查看View_P_Inspection表TType=1字段】")
print("=" * 60)
r = erp.execute("SELECT TOP 1 * FROM View_P_Inspection WHERE TType = 1")
if r.get('success') and r['data']:
    print('TType=1 字段列表:')
    for col in r['data'][0].keys():
        print(f'  - {col}')

print()
print("=" * 60)
print("【3. 查看具体数据的周期字段】")
print("=" * 60)
r = erp.execute("""
    SELECT TOP 5 CreateDate, PeriodCode, MonthCode, 
           MfgPartCode, InspectionResult, TType
    FROM View_P_Inspection 
    WHERE TType = 0 AND MfgPartCode LIKE '%B60-10P06T17615A0%'
    ORDER BY CreateDate DESC
""")
if r.get('success') and r['data']:
    for row in r['data']:
        print(f'  CreateDate: {row.get("CreateDate")}')
        print(f'  PeriodCode: {row.get("PeriodCode")}')
        print(f'  MonthCode: {row.get("MonthCode")}')
        print(f'  MfgPartCode: {row.get("MfgPartCode")}')
        print(f'  TType: {row.get("TType")}')
        print('  ---')

print()
print("=" * 60)
print("【4. 检查是否有专门的周期表】")
print("=" * 60)
# 检查是否有Period相关的表
r = erp.execute("""
    SELECT name FROM sys.tables 
    WHERE name LIKE '%Period%' OR name LIKE '%Month%'
""")
if r.get('success') and r.get('data'):
    print('周期相关表:')
    for row in r['data']:
        print(f'  - {row["name"]}')
else:
    print('未找到周期相关表')

print()
print("=" * 60)
print("【5. 检查P_WO工单表的周期字段】")
print("=" * 60)
r = erp.execute("SELECT TOP 1 * FROM P_WO")
if r.get('success') and r['data']:
    print('P_WO 字段列表(筛选Period相关):')
    for col in r['data'][0].keys():
        if 'period' in col.lower() or 'month' in col.lower() or 'date' in col.lower():
            print(f'  - {col}')

print()
print("=" * 60)
print("【6. 查看CreateDate和PeriodCode的关系】")
print("=" * 60)
r = erp.execute("""
    SELECT TOP 20 
           CreateDate,
           PeriodCode,
           MonthCode,
           COUNT(*) AS cnt
    FROM View_P_Inspection 
    WHERE MfgPartCode LIKE '%B60-10P06T17615A0%'
      AND InspectionResult = 'Scrapped'
    GROUP BY CreateDate, PeriodCode, MonthCode
    ORDER BY CreateDate DESC
""")
if r.get('success') and r['data']:
    for row in r['data']:
        print(f'  CreateDate: {row.get("CreateDate")}, PeriodCode: {row.get("PeriodCode")}, MonthCode: {row.get("MonthCode")}, 数量: {row.get("cnt")}')
