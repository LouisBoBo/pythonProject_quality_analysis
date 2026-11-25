# -*- coding: utf-8 -*-
"""精确匹配用户数据"""
import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r"D:\AI助理\PCB副总经理助理系统\品质分析处理系统")

from app.quality_intelligence_main import QualityIntelligenceSystem

def main():
    qi = QualityIntelligenceSystem()
    qi.connect()

    part = "B60-10P06T17615A0"

    print(f"\n{'='*70}")
    print(f"料号: {part} - 精确匹配分析")
    print(f"{'='*70}")

    if qi.erp and qi.erp.connected:
        # 用户数据：40,420 vs 38,450
        # 差异1,970

        # 方案：测试不同的日期范围
        print("\n【测试不同的日期范围组合】")

        # 按月累计测试
        months = [
            ('2026-01-01', '2026-01-31'),
            ('2026-02-01', '2026-02-28'),
            ('2026-03-01', '2026-03-31'),
            ('2026-01-01', '2026-02-28'),
            ('2026-01-01', '2026-03-31'),
            ('2026-02-01', '2026-03-31'),
        ]

        print("\n按TType=1:")
        for start, end in months:
            r = qi.erp.execute(f"""
                SELECT SUM(QtyOfUnit) AS Total
                FROM View_P_Inspection
                WHERE MfgPartCode = '{part}'
                  AND TType = 1
                  AND InspectionResult = 'Scrapped'
                  AND CreateDate >= '{start}'
                  AND CreateDate < '{end}'
            """)
            if r.get("success") and r.get("data"):
                val = r["data"][0].get("Total") or 0
                if 30000 < val < 50000:
                    print(f"  {start} ~ {end}: {val:,} PCS ***接近***")

        print("\n按TType=0:")
        for start, end in months:
            r = qi.erp.execute(f"""
                SELECT SUM(QtyOfUnit) AS Total
                FROM View_P_Inspection
                WHERE MfgPartCode = '{part}'
                  AND TType = 0
                  AND InspectionResult = 'Scrapped'
                  AND CreateDate >= '{start}'
                  AND CreateDate < '{end}'
            """)
            if r.get("success") and r.get("data"):
                val = r["data"][0].get("Total") or 0
                if 30000 < val < 50000:
                    print(f"  {start} ~ {end}: {val:,} PCS ***接近***")

        print("\n按TType=1+0:")
        for start, end in months:
            r = qi.erp.execute(f"""
                SELECT SUM(QtyOfUnit) AS Total
                FROM View_P_Inspection
                WHERE MfgPartCode = '{part}'
                  AND InspectionResult = 'Scrapped'
                  AND CreateDate >= '{start}'
                  AND CreateDate < '{end}'
            """)
            if r.get("success") and r.get("data"):
                val = r["data"][0].get("Total") or 0
                if 30000 < val < 50000:
                    print(f"  {start} ~ {end}: {val:,} PCS ***接近***")

        # 可能40,420和38,450是包含和不包含TType=0的区别
        print("\n【核心差异分析】")
        print(f"  TType=1 全部: 133,610 PCS")
        print(f"  TType=0 全部: 25,010 PCS")
        print(f"  TType=1+0: 158,620 PCS")

        # 让我检查最近的报废趋势
        print("\n【最近5天报废数据】")
        r = qi.erp.execute(f"""
            SELECT TOP 20
                CONVERT(VARCHAR(16), CreateDate, 120) AS CreateTime,
                QtyOfUnit AS Qty,
                InspectionItem AS Item,
                InspectionProcess AS Process
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
            ORDER BY CreateDate DESC
        """)
        if r.get("success") and r.get("data"):
            total = 0
            for row in r["data"]:
                total += row.get("Qty") or 0
            print(f"  TOP20记录总计: {total:,} PCS")

        # 也许ERP查询没有TType过滤？
        print("\n【检查View_P_Inspection表结构】")
        r = qi.erp.execute("""
            SELECT TOP 5 * FROM View_P_Inspection
            WHERE MfgPartCode = 'B60-10P06T17615A0'
              AND InspectionResult = 'Scrapped'
            ORDER BY CreateDate DESC
        """)
        if r.get("success") and r.get("data"):
            print("  字段列表:")
            if r["data"]:
                for key in r["data"][0].keys():
                    print(f"    - {key}")

    else:
        print("ERP未连接")

if __name__ == "__main__":
    main()
