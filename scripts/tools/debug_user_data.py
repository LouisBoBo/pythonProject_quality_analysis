# -*- coding: utf-8 -*-
"""验证用户提到的40,420 vs 38,450数据来源"""
import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r"D:\AI助理\PCB副总经理助理系统\品质分析处理系统")

from app.quality_intelligence_main import QualityIntelligenceSystem

def main():
    qi = QualityIntelligenceSystem()
    qi.connect()

    part = "B60-10P06T17615A0"

    print(f"\n{'='*70}")
    print(f"料号: {part}")
    print(f"{'='*70}")

    if qi.erp and qi.erp.connected:
        # 用户说 ERP查询报废40420PCS，检验报告报废38450PCS
        # 40420+38450 = 78870，约等于一个季度的量
        # 让我查找接近这个数字的数据范围

        # 方案1：按最近90天统计
        print("\n【方案1】最近90天统计:")
        r1 = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS TotalScrap
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
              AND CreateDate >= DATEADD(day, -90, GETDATE())
        """)
        if r1.get("success") and r1.get("data"):
            row = r1["data"][0]
            print(f"  View_P_Inspection (90天): {row.get('TotalScrap', 0) or 0:,} PCS")

        # 方案2：查询所有P_WO.reject_PCS
        r2 = qi.erp.execute(f"""
            SELECT SUM(reject_PCS) AS TotalReject
            FROM P_WO wo
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
        """)
        if r2.get("success") and r2.get("data"):
            row = r2["data"][0]
            print(f"  P_WO.reject_PCS (全部): {row.get('TotalReject', 0) or 0:,} PCS")

        # 方案3：查询当月入库+报废
        print("\n【方案3】当月入库 vs 报废:")
        r3 = qi.erp.execute(f"""
            SELECT SUM(sfiwo.qtyOfPCS) AS TotalInStock
            FROM FGI_StockFormItemWO sfiwo
            INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
            INNER JOIN P_WO wo ON sfiwo.woId = wo.recId
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
              AND sfi.stockDate >= '2026-03-01'
        """)
        if r3.get("success") and r3.get("data"):
            row = r3["data"][0]
            print(f"  2026-03入库PCS: {row.get('TotalInStock', 0) or 0:,}")

        r4 = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS TotalScrap
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
              AND CreateDate >= '2026-03-01'
              AND CreateDate < '2026-04-01'
        """)
        if r4.get("success") and r4.get("data"):
            row = r4["data"][0]
            print(f"  2026-03报废PCS(View): {row.get('TotalScrap', 0) or 0:,}")

        # 方案4：如果用户看到的是2026-03-15~04-15的数据
        print("\n【方案4】2026-03-15 ~ 2026-04-15 统计:")
        r5 = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS TotalScrap
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
              AND CreateDate >= '2026-03-15'
              AND CreateDate < '2026-04-16'
        """)
        if r5.get("success") and r5.get("data"):
            row = r5["data"][0]
            print(f"  View_P_Inspection: {row.get('TotalScrap', 0) or 0:,}")

        r6 = qi.erp.execute(f"""
            SELECT SUM(reject_PCS) AS TotalReject
            FROM P_WO wo
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
              AND wo.createDate >= '2026-03-15'
              AND wo.createDate < '2026-04-16'
        """)
        if r6.get("success") and r6.get("data"):
            row = r6["data"][0]
            print(f"  P_WO.reject_PCS: {row.get('TotalReject', 0) or 0:,}")

        # 方案5：工单完工日期
        print("\n【方案5】按工单完工日期(endDate)统计:")
        r7 = qi.erp.execute(f"""
            SELECT SUM(reject_PCS) AS TotalReject
            FROM P_WO wo
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
              AND wo.endDate >= '2026-03-15'
              AND wo.endDate < '2026-04-16'
        """)
        if r7.get("success") and r7.get("data"):
            row = r7["data"][0]
            print(f"  P_WO.reject_PCS (按endDate): {row.get('TotalReject', 0) or 0:,}")

        # 方案6：40,420 PCS 可能是什么？
        # 133,610 - 21,750(4月) - 10,710(3月View) = 101,150 - 不对
        # 让我看看P_WO按完工日期统计

        print("\n【方案6】按月统计P_WO.reject_PCS (按createDate):")
        r8 = qi.erp.execute(f"""
            SELECT TOP 12
                CONVERT(VARCHAR(7), createDate, 120) AS Month,
                SUM(reject_PCS) AS TotalReject
            FROM P_WO wo
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
            GROUP BY CONVERT(VARCHAR(7), createDate, 120)
            ORDER BY Month DESC
        """)
        if r8.get("success") and r8.get("data"):
            for row in r8["data"]:
                print(f"  {row['Month']}: {row['TotalReject']:,} PCS")

        # 方案7：按endDate(完工日期)统计
        print("\n【方案7】按月统计P_WO.reject_PCS (按endDate):")
        r9 = qi.erp.execute(f"""
            SELECT TOP 12
                CONVERT(VARCHAR(7), endDate, 120) AS Month,
                SUM(reject_PCS) AS TotalReject
            FROM P_WO wo
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
              AND wo.endDate IS NOT NULL
            GROUP BY CONVERT(VARCHAR(7), endDate, 120)
            ORDER BY Month DESC
        """)
        if r9.get("success") and r9.get("data"):
            for row in r9["data"]:
                print(f"  {row['Month']}: {row['TotalReject']:,} PCS")

    else:
        print("ERP未连接")

if __name__ == "__main__":
    main()
