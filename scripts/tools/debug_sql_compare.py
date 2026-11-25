# -*- coding: utf-8 -*-
"""对比ERP查询和系统查询的差异"""
import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r"D:\AI助理\PCB副总经理助理系统\品质分析处理系统")

from app.quality_intelligence_main import QualityIntelligenceSystem

def main():
    qi = QualityIntelligenceSystem()
    qi.connect()

    part = "B60-10P06T17615A0"

    print(f"\n{'='*70}")
    print(f"料号: {part} - SQL对比分析")
    print(f"{'='*70}")

    if qi.erp and qi.erp.connected:
        # 用户说日期一样，但40,420 vs 38,450
        # 让我检查View_P_Inspection的不同条件

        # 基础查询 - 不带任何过滤
        print("\n【1. 基础查询 - 无过滤条件】")
        r1 = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
        """)
        if r1.get("success") and r1.get("data"):
            print(f"  总报废: {r1['data'][0].get('Total') or 0:,} PCS")

        # 检查TType的不同值
        print("\n【2. 按TType分组查看报废数量】")
        r2 = qi.erp.execute(f"""
            SELECT TType, InspectionResult, COUNT(*) AS CNT, SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND InspectionResult = 'Scrapped'
            GROUP BY TType, InspectionResult
            ORDER BY Total DESC
        """)
        if r2.get("success") and r2.get("data"):
            for row in r2["data"]:
                print(f"  TType={row['TType']}, Result={row['InspectionResult']}: {row['Total']:,} PCS ({row['CNT']}条)")

        # 检查InspectionResult的不同值
        print("\n【3. 按InspectionResult分组（包含TType=1）】")
        r3 = qi.erp.execute(f"""
            SELECT InspectionResult, COUNT(*) AS CNT, SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
            GROUP BY InspectionResult
        """)
        if r3.get("success") and r3.get("data"):
            for row in r3["data"]:
                print(f"  {row['InspectionResult']}: {row['Total'] or 0:,} PCS ({row['CNT']}条)")

        # 检查是否有其他状态（如ReturnToSupplier等）
        print("\n【4. 所有InspectionResult值】")
        r4 = qi.erp.execute(f"""
            SELECT DISTINCT InspectionResult
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
        """)
        if r4.get("success") and r4.get("data"):
            print(f"  值列表: {[row['InspectionResult'] for row in r4['data']]}")

        # 检查CreateDate范围
        print("\n【5. 按日期范围查看数据分布】")
        r5 = qi.erp.execute(f"""
            SELECT MIN(CreateDate) AS MinDate, MAX(CreateDate) AS MaxDate
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
        """)
        if r5.get("success") and r5.get("data"):
            row = r5["data"][0]
            print(f"  日期范围: {row.get('MinDate')} ~ {row.get('MaxDate')}")

        # 可能40,420是ERP用了不同的过滤条件
        # 让我检查是否有TType=2或其他类型
        print("\n【6. TType=2的数据】")
        r6 = qi.erp.execute(f"""
            SELECT InspectionResult, COUNT(*) AS CNT, SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 2
            GROUP BY InspectionResult
        """)
        if r6.get("success") and r6.get("data"):
            for row in r6["data"]:
                print(f"  TType=2, {row['InspectionResult']}: {row['Total'] or 0:,} PCS ({row['CNT']}条)")

        # 差异分析：40,420 - 38,450 = 1,970
        # 让我看看是否能找到1,970的来源
        print("\n【7. 差异分析：查找1,970 PCS的来源】")
        print("  差异 = 40,420 - 38,450 = 1,970 PCS")

        # 可能是不包含TType=1的报废？
        r7 = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS TotalScrap
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND InspectionResult = 'Scrapped'
              AND TType != 1
        """)
        if r7.get("success") and r7.get("data"):
            val = r7["data"][0].get("TotalScrap") or 0
            print(f"  非TType=1的报废: {val:,} PCS")

        # 可能是不同的日期条件
        print("\n【8. 尝试不同的日期条件】")

        # 最近30天
        r8a = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
              AND CreateDate >= DATEADD(day, -30, GETDATE())
        """)
        if r8a.get("success") and r8a.get("data"):
            print(f"  最近30天: {r8a['data'][0].get('Total') or 0:,} PCS")

        # 当月
        r8b = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
              AND CreateDate >= '2026-04-01'
        """)
        if r8b.get("success") and r8b.get("data"):
            print(f"  2026-04当月: {r8b['data'][0].get('Total') or 0:,} PCS")

        # 2026年
        r8c = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
              AND CreateDate >= '2026-01-01'
        """)
        if r8c.get("success") and r8c.get("data"):
            print(f"  2026年至今: {r8c['data'][0].get('Total') or 0:,} PCS")

        # 也许ERP查询没有TType=1过滤？
        print("\n【9. 测试：不用TType过滤】")
        r9 = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND InspectionResult = 'Scrapped'
        """)
        if r9.get("success") and r9.get("data"):
            print(f"  无TType过滤: {r9['data'][0].get('Total') or 0:,} PCS")

        # 也许ERP用了endDate？
        print("\n【10. 按endDate过滤测试】")
        r10 = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection v
            INNER JOIN P_WO wo ON v.MO_NUMBER = wo.woNumber
            WHERE v.MfgPartCode = '{part}'
              AND v.TType = 1
              AND v.InspectionResult = 'Scrapped'
              AND wo.endDate >= '2026-01-01'
        """)
        if r10.get("success") and r10.get("data"):
            print(f"  按工单endDate>=2026-01-01: {r10['data'][0].get('Total') or 0:,} PCS")

        # 可能用户用的日期范围不同
        print("\n【11. 详细日期分布】")
        r11 = qi.erp.execute(f"""
            SELECT CONVERT(VARCHAR(10), CreateDate, 120) AS Day,
                   SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
            GROUP BY CONVERT(VARCHAR(10), CreateDate, 120)
            ORDER BY Day DESC
            LIMIT 30
        """)
        if r11.get("success") and r11.get("data"):
            for row in r11["data"]:
                print(f"  {row['Day']}: {row['Total']:,} PCS")

    else:
        print("ERP未连接")

if __name__ == "__main__":
    main()
