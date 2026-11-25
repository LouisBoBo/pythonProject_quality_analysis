# -*- coding: utf-8 -*-
"""详细分析TType差异"""
import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r"D:\AI助理\PCB副总经理助理系统\品质分析处理系统")

from app.quality_intelligence_main import QualityIntelligenceSystem

def main():
    qi = QualityIntelligenceSystem()
    qi.connect()

    part = "B60-10P06T17615A0"

    print(f"\n{'='*70}")
    print(f"料号: {part} - TType差异分析")
    print(f"{'='*70}")

    if qi.erp and qi.erp.connected:
        # TType的含义：0可能是采购来料检验，1是生产过程检验
        # 让我验证这个假设

        print("\n【按TType分析报废数据】")

        # TType=0的详细信息
        print("\nTType=0（可能是来料检验）:")
        r1 = qi.erp.execute(f"""
            SELECT TOP 10
                InspectionProcess,
                InspectionItem,
                SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 0
              AND InspectionResult = 'Scrapped'
            GROUP BY InspectionProcess, InspectionItem
            ORDER BY Total DESC
        """)
        if r1.get("success") and r1.get("data"):
            for row in r1["data"]:
                print(f"  {row['InspectionProcess']}/{row['InspectionItem']}: {row['Total']:,} PCS")

        # TType=1的详细信息
        print("\nTType=1（可能是生产过程检验）:")
        r2 = qi.erp.execute(f"""
            SELECT TOP 10
                InspectionProcess,
                InspectionItem,
                SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
            GROUP BY InspectionProcess, InspectionItem
            ORDER BY Total DESC
        """)
        if r2.get("success") and r2.get("data"):
            for row in r2["data"]:
                print(f"  {row['InspectionProcess']}/{row['InspectionItem']}: {row['Total']:,} PCS")

        # 确认差异
        print("\n【差异确认】")
        r3 = qi.erp.execute(f"""
            SELECT
                SUM(CASE WHEN TType = 0 THEN QtyOfUnit ELSE 0 END) AS TType0,
                SUM(CASE WHEN TType = 1 THEN QtyOfUnit ELSE 0 END) AS TType1
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND InspectionResult = 'Scrapped'
        """)
        if r3.get("success") and r3.get("data"):
            row = r3["data"][0]
            print(f"  TType=0: {row['TType0'] or 0:,} PCS")
            print(f"  TType=1: {row['TType1'] or 0:,} PCS")
            print(f"  合计: {(row['TType0'] or 0) + (row['TType1'] or 0):,} PCS")

        # 检查当前系统的默认日期范围
        print("\n【当前系统默认日期范围】")
        from datetime import datetime
        today = datetime.now()
        first_day = today.replace(day=1).strftime('%Y-%m-%d')
        print(f"  开始日期: {first_day}")
        print(f"  结束日期: {today.strftime('%Y-%m-%d')}")
        print(f"  范围: {first_day} ~ {today.strftime('%Y-%m-%d')}")

        # 按这个范围查询
        r4 = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
              AND CreateDate >= '{first_day}'
        """)
        if r4.get("success") and r4.get("data"):
            print(f"  当月TType=1报废: {r4['data'][0].get('Total') or 0:,} PCS")

    else:
        print("ERP未连接")

if __name__ == "__main__":
    main()
