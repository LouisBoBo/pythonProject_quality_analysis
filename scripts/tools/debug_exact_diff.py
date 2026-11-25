# -*- coding: utf-8 -*-
"""精确分析40,420 vs 38,450差异"""
import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r"D:\AI助理\PCB副总经理助理系统\品质分析处理系统")

from app.quality_intelligence_main import QualityIntelligenceSystem

def main():
    qi = QualityIntelligenceSystem()
    qi.connect()

    part = "B60-10P06T17615A0"

    print(f"\n{'='*70}")
    print(f"料号: {part} - 精确差异分析")
    print(f"{'='*70}")

    if qi.erp and qi.erp.connected:
        # 差异 = 40,420 - 38,450 = 1,970
        # 让我检查不同的日期范围

        print("\n【1. 测试2026年Q1数据】")
        for end_day in ['2026-01-15', '2026-01-31', '2026-02-15', '2026-02-28', '2026-03-15', '2026-03-31']:
            r = qi.erp.execute(f"""
                SELECT SUM(QtyOfUnit) AS Total
                FROM View_P_Inspection
                WHERE MfgPartCode = '{part}'
                  AND TType = 1
                  AND InspectionResult = 'Scrapped'
                  AND CreateDate >= '2026-01-01'
                  AND CreateDate < '{end_day}'
            """)
            if r.get("success") and r.get("data"):
                val = r["data"][0].get("Total") or 0
                if 37000 < val < 42000:
                    print(f"  ~2026-01-01~{end_day}: {val:,} PCS ***接近***")

        print("\n【2. 连续日期测试 - 找到40,420和38,450的日期边界】")
        # 40,420 - 38,450 = 1,970
        # 让我找找这1,970在哪个日期范围内

        for day in range(1, 32):
            end = f"2026-03-{day:02d}"
            r = qi.erp.execute(f"""
                SELECT SUM(QtyOfUnit) AS Total
                FROM View_P_Inspection
                WHERE MfgPartCode = '{part}'
                  AND TType = 1
                  AND InspectionResult = 'Scrapped'
                  AND CreateDate >= '2026-01-01'
                  AND CreateDate < '{end}'
            """)
            if r.get("success") and r.get("data"):
                val = r["data"][0].get("Total") or 0
                if 37000 < val < 42000:
                    print(f"  2026-01-01~{end}: {val:,} PCS")

        print("\n【3. 检查日期分布 - 3月份】")
        r = qi.erp.execute(f"""
            SELECT CONVERT(VARCHAR(10), CreateDate, 120) AS Day,
                   SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
              AND CreateDate >= '2026-03-01'
              AND CreateDate < '2026-04-01'
            GROUP BY CONVERT(VARCHAR(10), CreateDate, 120)
            ORDER BY Day
        """)
        if r.get("success") and r.get("data"):
            total = 0
            for row in r["data"]:
                total += row['Total']
                print(f"  {row['Day']}: {row['Total']:,} PCS")
            print(f"  3月合计: {total:,} PCS")

        print("\n【4. 如果ERP查了TType=0会怎样？】")
        r = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 0
              AND InspectionResult = 'Scrapped'
              AND CreateDate >= '2026-01-01'
              AND CreateDate < '2026-04-01'
        """)
        if r.get("success") and r.get("data"):
            val = r["data"][0].get("Total") or 0
            print(f"  TType=0, 2026-Q1: {val:,} PCS")

        print("\n【5. TType=0按工序分布 - 2026-Q1】")
        r = qi.erp.execute(f"""
            SELECT InspectionProcess, SUM(QtyOfUnit) AS Total
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 0
              AND InspectionResult = 'Scrapped'
              AND CreateDate >= '2026-01-01'
              AND CreateDate < '2026-04-01'
            GROUP BY InspectionProcess
            ORDER BY Total DESC
        """)
        if r.get("success") and r.get("data"):
            for row in r["data"]:
                print(f"  {row['InspectionProcess']}: {row['Total']:,} PCS")

        # 总结
        print("\n" + "="*70)
        print("【总结】")
        print("="*70)
        print("""
差异可能来源：
1. 日期截止点不同（用户查到40,420可能是到某一天的）
2. ERP可能包含TType=0的数据
3. 38,450是系统查询结果，日期范围可能不完全匹配
        """)

    else:
        print("ERP未连接")

if __name__ == "__main__":
    main()
