# -*- coding: utf-8 -*-
"""对比料号B60-10P06T17615A0的报废数据来源差异"""
import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r"D:\AI助理\PCB副总经理助理系统\品质分析处理系统")

from app.quality_intelligence_main import QualityIntelligenceSystem

def main():
    qi = QualityIntelligenceSystem()
    qi.connect()

    part = "B60-10P06T17615A0"

    print(f"\n{'='*60}")
    print(f"料号: {part}")
    print(f"{'='*60}")

    if qi.erp and qi.erp.connected:
        # 方法1: View_P_Inspection MRB报废视图
        print("\n【方法1】View_P_Inspection MRB报废记录:")
        r1 = qi.erp.execute(f"""
            SELECT SUM(QtyOfUnit) AS TotalScrap,
                   COUNT(*) AS RecordCount,
                   MIN(CreateDate) AS FirstDate,
                   MAX(CreateDate) AS LastDate
            FROM View_P_Inspection
            WHERE MfgPartCode = '{part}'
              AND TType = 1
              AND InspectionResult = 'Scrapped'
        """)
        if r1.get("success") and r1.get("data"):
            row = r1["data"][0]
            print(f"  总报废PCS: {row.get('TotalScrap', 0):,}")
            print(f"  记录数: {row.get('RecordCount', 0)}")
            print(f"  日期范围: {row.get('FirstDate', 'N/A')} ~ {row.get('LastDate', 'N/A')}")

        # 方法2: P_WO工单表reject_PCS
        print("\n【方法2】P_WO工单表 reject_PCS:")
        r2 = qi.erp.execute(f"""
            SELECT SUM(reject_PCS) AS TotalReject,
                   COUNT(*) AS WoCount
            FROM P_WO wo
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
        """)
        if r2.get("success") and r2.get("data"):
            row = r2["data"][0]
            print(f"  总报废PCS: {row.get('TotalReject', 0):,}")
            print(f"  工单数: {row.get('WoCount', 0)}")

        # 方法3: P_WO工单表的qty_PCS_PRODUCED + reject_PCS
        print("\n【方法3】P_WO工单表 投入PCS vs 报废PCS:")
        r3 = qi.erp.execute(f"""
            SELECT SUM(qty_PCS_PRODUCED) AS TotalProduced,
                   SUM(reject_PCS) AS TotalReject,
                   COUNT(*) AS WoCount
            FROM P_WO wo
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
              AND (qty_PCS_PRODUCED > 0 OR reject_PCS > 0)
        """)
        if r3.get("success") and r3.get("data"):
            row = r3["data"][0]
            produced = row.get('TotalProduced', 0) or 0
            reject = row.get('TotalReject', 0) or 0
            rate = (reject * 100.0 / (produced + reject)) if (produced + reject) > 0 else 0
            print(f"  投入PCS: {produced:,}")
            print(f"  报废PCS: {reject:,}")
            print(f"  报废率: {rate:.2f}%")

        # 方法4: FGI_StockFormItemWO 真实入库PCS
        print("\n【方法4】FGI_StockFormItemWO 真实入库PCS:")
        r4 = qi.erp.execute(f"""
            SELECT SUM(sfiwo.qtyOfPCS) AS TotalInStock
            FROM FGI_StockFormItemWO sfiwo
            INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
            INNER JOIN P_WO wo ON sfiwo.woId = wo.recId
            INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
            WHERE mp.mfgPartCode = '{part}'
        """)
        if r4.get("success") and r4.get("data"):
            row = r4["data"][0]
            print(f"  真实入库PCS: {row.get('TotalInStock', 0):,}")

        # 对比差异
        if r1.get("success") and r2.get("success"):
            v1 = (r1["data"][0].get("TotalScrap") or 0) if r1.get("data") else 0
            v2 = (r2["data"][0].get("TotalReject") or 0) if r2.get("data") else 0
            diff = abs(v1 - v2)
            print(f"\n{'='*60}")
            print(f"【数据对比】")
            print(f"  View_P_Inspection: {v1:,} PCS")
            print(f"  P_WO.reject_PCS:   {v2:,} PCS")
            print(f"  差异: {diff:,} PCS ({diff/v2*100:.1f}%)" if v2 else "")

    else:
        print("ERP未连接")

if __name__ == "__main__":
    main()
