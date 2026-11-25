import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

# 1. View_P_Inspection报废数据
r = erp.execute("""
    SELECT COUNT(*) AS cnt, SUM(QtyOfUnit) AS total
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped' AND FiscalPeriod = '202604'
""")
print("=== View_P_Inspection 报废 ===")
print(f"记录数: {r['data'][0]['cnt']}, 报废PCS: {r['data'][0]['total']}")

# 2. 入库数据
r = erp.execute("""
    SELECT COUNT(*) AS cnt, SUM(sfiwo.qtyOfPCS) AS total
    FROM FGI_StockFormItemWO sfiwo
    INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
    WHERE sfi.stockDate >= '2026-04-01' AND sfi.stockDate < '2026-05-01'
""")
print("\n=== FGI 入库(按日期) ===")
print(f"记录数: {r['data'][0]['cnt']}, 入库PCS: {r['data'][0]['total']}")

# 3. 报废日期分布
r = erp.execute("""
    SELECT FiscalPeriod, COUNT(*) AS cnt, SUM(QtyOfUnit) AS total
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped'
    GROUP BY FiscalPeriod
    ORDER BY FiscalPeriod DESC
""")
print("\n=== 报废 FiscalPeriod 分布 ===")
for row in r['data'][:6]:
    print(f"FP={row['FiscalPeriod']}, 记录={row['cnt']}, PCS={row['total']}")

# 4. 入库月份分布
r = erp.execute("""
    SELECT CONVERT(VARCHAR(7), sfi.stockDate, 120) AS Month,
           COUNT(*) AS cnt, SUM(sfiwo.qtyOfPCS) AS total
    FROM FGI_StockFormItemWO sfiwo
    INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
    GROUP BY CONVERT(VARCHAR(7), sfi.stockDate, 120)
    ORDER BY Month DESC
""")
print("\n=== 入库月份分布 ===")
for row in r['data'][:6]:
    print(f"Month={row['Month']}, 记录={row['cnt']}, PCS={row['total']}")

# 5. 日期不匹配检查
r = erp.execute("""
    SELECT
        COUNT(*) AS total_cnt,
        SUM(CASE WHEN CONVERT(VARCHAR(6), CreateDate, 112) = FiscalPeriod THEN 1 ELSE 0 END) AS match_cnt,
        SUM(CASE WHEN CONVERT(VARCHAR(6), CreateDate, 112) <> FiscalPeriod THEN 1 ELSE 0 END) AS diff_cnt
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped' AND FiscalPeriod = '202604'
""")
row = r['data'][0]
print(f"\n=== 日期一致性 ===")
print(f"总记录: {row['total_cnt']}, 匹配: {row['match_cnt']}, 不匹配: {row['diff_cnt']}")
