# -*- coding: utf-8 -*-
"""对比料号当月报废数据"""
import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r"D:\AI助理\PCB副总经理助理系统\品质分析处理系统")

from app.quality_intelligence_main import QualityIntelligenceSystem
from datetime import datetime, timedelta

def main():
    qi = QualityIntelligenceSystem()
    qi.connect()

    part = "B60-10P06T17615A0"

    # 获取当月1号
    today = datetime.now()
    first_day = today.replace(day=1).strftime('%Y-%m-%d')

    print(f"\n{'='*60}")
    print(f"料号: {part}")
    print(f"当月日期范围: {first_day} ~ {today.strftime('%Y-%m-%d')}")
    print(f"{'='*60}")

    if qi.erp and qi.erp.connected:
        # 当月View_P_Inspection MRB报废
        print(f"\n【当月 View_P_Inspection】({first_day} ~ ):")
        r1 = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS TotalScrap,
                   COUNT(*) AS RecordCount
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
              AND CreateDate >= '{first_day}'
        """)
        if r1.get("success") and r1.get("data"):
            row = r1["data"][0]
            print(f"  总报废PCS: {row.get('TotalScrap', 0) or 0:,}")

        # 当月P_WO.reject_PCS
        print(f"\n【当月 P_WO.reject_PCS】({first_day} ~ ):")
        r2 = qi.erp.execute(f"""
            SELECT SUM(reject_PCS) AS TotalReject
            FROM P_WO wo
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
              AND wo.createDate >= '{first_day}'
        """)
        if r2.get("success") and r2.get("data"):
            row = r2["data"][0]
            print(f"  总报废PCS: {row.get('TotalReject', 0) or 0:,}")

        # 近30天
        print(f"\n{'='*60}")
        print("【近30天数据】")
        r3 = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS TotalScrap
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
              AND CreateDate >= DATEADD(day, -30, GETDATE())
        """)
        if r3.get("success") and r3.get("data"):
            row = r3["data"][0]
            print(f"  View_P_Inspection: {row.get('TotalScrap', 0) or 0:,} PCS")

        r4 = qi.erp.execute(f"""
            SELECT SUM(reject_PCS) AS TotalReject
            FROM P_WO wo
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
              AND wo.createDate >= DATEADD(day, -30, GETDATE())
        """)
        if r4.get("success") and r4.get("data"):
            row = r4["data"][0]
            print(f"  P_WO.reject_PCS: {row.get('TotalReject', 0) or 0:,} PCS")

        # 按月统计
        print(f"\n{'='*60}")
        print("【按月报废趋势】")
        r5 = qi.erp.execute(f"""
            SELECT TOP 12
                CONVERT(VARCHAR(7), CreateDate, 120) AS Month,
                SUM(QtyOfUnit) AS TotalScrap
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
            GROUP BY CONVERT(VARCHAR(7), CreateDate, 120)
            ORDER BY Month DESC
        """)
        if r5.get("success") and r5.get("data"):
            for row in r5["data"]:
                print(f"  {row['Month']}: {row['TotalScrap']:,} PCS")

        # 最近3个月的详细对比
        print(f"\n{'='*60}")
        print("【对比：View_P_Inspection vs P_WO.reject_PCS（最近3个月）】")
        for i in range(3):
            if i == 0:
                month_start = first_day
            else:
                prev = today.replace(day=1)
                for _ in range(i):
                    prev = prev - timedelta(days=1)
                prev = prev.replace(day=1)
                month_start = prev.strftime('%Y-%m-%d')

            year, month = month_start.split('-')[:2]
            next_m = int(month) + 1
            next_y = int(year)
            if next_m > 12:
                next_m = 1
                next_y += 1
            month_end = f"{next_y}-{next_m:02d}-01"

            # View_P_Inspection
            r_v = qi.erp.execute(f"""
                SELECT SUM(QtyOfUnit) AS Total
                FROM View_P_Inspection
                WHERE MfgPartCode = '{part}'
                  AND TType = 1
                  AND InspectionResult = 'Scrapped'
                  AND CreateDate >= '{month_start}'
                  AND CreateDate < '{month_end}'
            """)
            v_total = (r_v["data"][0].get("Total") or 0) if r_v.get("success") and r_v.get("data") else 0

            # P_WO.reject_PCS (按工单创建日期)
            r_p = qi.erp.execute(f"""
                SELECT SUM(reject_PCS) AS Total
                FROM P_WO wo
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE mp.mfgPartCode = '{part}'
                  AND wo.createDate >= '{month_start}'
                  AND wo.createDate < '{month_end}'
            """)
            p_total = (r_p["data"][0].get("Total") or 0) if r_p.get("success") and r_p.get("data") else 0

            print(f"  {month_start[:7]}: View={v_total:,} | P_WO={p_total:,} | Diff={abs(v_total-p_total):,}")

    else:
        print("ERP未连接")

if __name__ == "__main__":
    main()
