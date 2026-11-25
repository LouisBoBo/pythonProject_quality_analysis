# -*- coding: utf-8 -*-
"""详细分析报废数据差异"""
import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r"D:\AI助理\PCB副总经理助理系统\品质分析处理系统")

from app.quality_intelligence_main import QualityIntelligenceSystem

def main():
    qi = QualityIntelligenceSystem()
    qi.connect()

    part = "B60-10P06T17615A0"

    print(f"\n{'='*70}")
    print(f"料号: {part} - 报废数据差异分析")
    print(f"{'='*70}")

    if qi.erp and qi.erp.connected:
        # 用户看到的40,420 vs 38,450可能来自不同时间段
        # 让我查询所有可能的时间范围

        # 2025-10按endDate: 35,130 + 某月数据 ≈ 40,420?
        # 38,450 可能是 View_P_Inspection 按月统计的数据

        print("\n【按View_P_Inspection日期（MRB检验日期）统计】")
        r1 = qi.erp.execute(f"""
            SELECT
                CONVERT(VARCHAR(7), CreateDate, 120) AS Month,
                SUM(QtyOfUnit) AS TotalScrap,
                COUNT(*) AS RecordCount
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
            GROUP BY CONVERT(VARCHAR(7), CreateDate, 120)
            ORDER BY Month DESC
        """)
        if r1.get("success") and r1.get("data"):
            total_view = 0
            for row in r1["data"]:
                print(f"  {row['Month']}: {row['TotalScrap']:,} PCS ({row['RecordCount']}条记录)")
                total_view += row['TotalScrap']
            print(f"  TOTAL: {total_view:,} PCS")

        print("\n【按P_WO.createDate（工单创建日期）统计】")
        r2 = qi.erp.execute(f"""
            SELECT
                CONVERT(VARCHAR(7), createDate, 120) AS Month,
                SUM(reject_PCS) AS TotalReject
            FROM P_WO wo
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
            GROUP BY CONVERT(VARCHAR(7), createDate, 120)
            ORDER BY Month DESC
        """)
        if r2.get("success") and r2.get("data"):
            total_wo = 0
            for row in r2["data"]:
                print(f"  {row['Month']}: {row['TotalReject']:,} PCS")
                total_wo += row['TotalReject']
            print(f"  TOTAL: {total_wo:,} PCS")

        print("\n【按P_WO.endDate（工单完工日期）统计】")
        r3 = qi.erp.execute(f"""
            SELECT
                CONVERT(VARCHAR(7), endDate, 120) AS Month,
                SUM(reject_PCS) AS TotalReject
            FROM P_WO wo
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
              AND wo.endDate IS NOT NULL
            GROUP BY CONVERT(VARCHAR(7), endDate, 120)
            ORDER BY Month DESC
        """)
        if r3.get("success") and r3.get("data"):
            total_end = 0
            for row in r3["data"]:
                print(f"  {row['Month']}: {row['TotalReject']:,} PCS")
                total_end += row['TotalReject']
            print(f"  TOTAL: {total_end:,} PCS")

        # 分析差异
        print("\n" + "="*70)
        print("【核心发现】")
        print("="*70)
        print("""
报废数据有两个来源日期：
1. View_P_Inspection.CreateDate = MRB检验日期（物料实际报废的时间）
2. P_WO.reject_PCS + createDate = 工单创建日期（开始生产的时间）
3. P_WO.reject_PCS + endDate = 工单完工日期（生产完成的时间）

三者含义不同：
- 物料在2025-09生产，2025-12才完成MRB检验 → 
  P_WO.createDate = 2025-09
  P_WO.endDate = 2025-09  
  View_P_Inspection.CreateDate = 2025-12
        """)

        # 验证：2025-12的endDate vs View_P_Inspection
        print("\n【关键对比：2025-12月份数据】")
        r4 = qi.erp.execute(f"""
            SELECT SUM(reject_PCS) AS RejectByEndDate
            FROM P_WO wo
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
              AND CONVERT(VARCHAR(7), endDate, 120) = '2025-12'
        """)
        r5 = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS ScrapByCreateDate
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
              AND CONVERT(VARCHAR(7), CreateDate, 120) = '2025-12'
        """)
        if r4.get("success") and r4.get("data"):
            print(f"  P_WO.endDate='2025-12': {r4['data'][0].get('RejectByEndDate', 0) or 0:,} PCS")
        if r5.get("success") and r5.get("data"):
            print(f"  View_P_Inspection.CreateDate='2025-12': {r5['data'][0].get('ScrapByCreateDate', 0) or 0:,} PCS")

    else:
        print("ERP未连接")

if __name__ == "__main__":
    main()
