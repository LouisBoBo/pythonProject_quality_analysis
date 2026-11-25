# -*- coding: utf-8 -*-
import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, '.')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

# 1. 直接查报废 - View_P_Inspection
print("=" * 60)
print("1. View_P_Inspection 报废数据 (FiscalPeriod=202604)")
r = erp.execute("""
    SELECT COUNT(*) AS cnt, SUM(QtyOfUnit) AS total
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped' AND FiscalPeriod = '202604'
""")
print(f"   记录数: {r['data'][0]['cnt']}")
print(f"   报废PCS: {r['data'][0]['total']}")

# 2. 入库数据 - 按日期范围
print("\n" + "=" * 60)
print("2. FGI_StockFormItem 入库数据 (2026-04)")
r = erp.execute("""
    SELECT COUNT(*) AS cnt, SUM(sfiwo.qtyOfPCS) AS total
    FROM FGI_StockFormItemWO sfiwo
    INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
    WHERE sfi.stockDate >= '2026-04-01' AND sfi.stockDate < '2026-05-01'
""")
print(f"   记录数: {r['data'][0]['cnt']}")
print(f"   入库PCS: {r['data'][0]['total']}")

# 3. 检查报废表的日期范围 vs FiscalPeriod
print("\n" + "=" * 60)
print("3. 报废日期分布")
r = erp.execute("""
    SELECT FiscalPeriod, COUNT(*) AS cnt, SUM(QtyOfUnit) AS total
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped'
    GROUP BY FiscalPeriod
    ORDER BY FiscalPeriod DESC
""")
print(f"   {'FiscalPeriod':<15} {'记录数':<10} {'报废PCS':<15}")
for row in r['data'][:6]:
    print(f"   {row['FiscalPeriod']:<15} {row['cnt']:<10} {row['total']:<15}")

# 4. 检查入库日期分布
print("\n" + "=" * 60)
print("4. 入库月份分布")
r = erp.execute("""
    SELECT CONVERT(VARCHAR(7), sfi.stockDate, 120) AS Month,
           COUNT(*) AS cnt, SUM(sfiwo.qtyOfPCS) AS total
    FROM FGI_StockFormItemWO sfiwo
    INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
    GROUP BY CONVERT(VARCHAR(7), sfi.stockDate, 120)
    ORDER BY Month DESC
""")
print(f"   {'月份':<10} {'记录数':<10} {'入库PCS':<15}")
for row in r['data'][:6]:
    print(f"   {row['Month']:<10} {row['cnt']:<10} {row['total']:<15}")

# 5. 检查报废 CreateDate 和 FiscalPeriod 是否一致
print("\n" + "=" * 60)
print("5. FiscalPeriod vs CreateDate 差异分析")
r = erp.execute("""
    SELECT
        COUNT(*) AS total_cnt,
        SUM(CASE WHEN CONVERT(VARCHAR(6), CreateDate, 112) = FiscalPeriod THEN 1 ELSE 0 END) AS match_cnt,
        SUM(CASE WHEN CONVERT(VARCHAR(6), CreateDate, 112) <> FiscalPeriod THEN 1 ELSE 0 END) AS diff_cnt
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped' AND FiscalPeriod = '202604'
""")
row = r['data'][0]
print(f"   总记录: {row['total_cnt']}")
print(f"   日期匹配(YYYYMM= FiscalPeriod): {row['match_cnt']}")
print(f"   日期不匹配: {row['diff_cnt']}")

# 6. 检查日期不匹配的记录
r = erp.execute("""
    SELECT TOP 5
        CreateDate, FiscalPeriod,
        CONVERT(VARCHAR(6), CreateDate, 112) AS CreateYYYYMM,
        QtyOfUnit
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped'
      AND FiscalPeriod = '202604'
      AND CONVERT(VARCHAR(6), CreateDate, 112) <> FiscalPeriod
""")
print(f"\n   日期不匹配样例:")
for row in r['data']:
    print(f"   CreateDate={row['CreateDate']}, FiscalPeriod={row['FiscalPeriod']}, Qty={row['QtyOfUnit']}")
