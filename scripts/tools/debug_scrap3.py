import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("分析: 报废数据差异来源")
print("=" * 70)

# 1. 检查P_WO表字段
print("\n1. P_WO 表主要字段")
r = erp.execute("""
    SELECT TOP 1 recId, woNumber, mfgPartId, qty_PCS_PRODUCED, reject_PCS, createDate
    FROM P_WO
""")
if r.get('success') and r['data']:
    print(f"   字段: {list(r['data'][0].keys())}")

# 2. 检查FGI_StockFormItemWO字段
print("\n2. FGI_StockFormItemWO 表字段")
r = erp.execute("""
    SELECT TOP 1 woId, qtyOfPCS, stockFormItemId
    FROM FGI_StockFormItemWO
""")
if r.get('success') and r['data']:
    print(f"   字段: {list(r['data'][0].keys())}")

# 3. 关键问题：入库数据和报废数据的关联方式
# 报废用的是 View_P_Inspection.FiscalPeriod
# 入库用的是 FGI_StockFormItem.stockDate
# 两者可能不一致！

print("\n3. 报废数据 vs 入库数据 日期范围对比")
# 报废
r = erp.execute("""
    SELECT MIN(CreateDate) AS min_dt, MAX(CreateDate) AS max_dt, SUM(QtyOfUnit) AS total
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped' AND FiscalPeriod = '202604'
""")
row = r['data'][0]
print(f"   报废(FiscalPeriod=202604):")
print(f"     日期范围: {row['min_dt']} ~ {row['max_dt']}")
print(f"     报废PCS: {row['total']}")

# 入库
r = erp.execute("""
    SELECT MIN(sfi.stockDate) AS min_dt, MAX(sfi.stockDate) AS max_dt, SUM(sfiwo.qtyOfPCS) AS total
    FROM FGI_StockFormItemWO sfiwo
    INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
    WHERE sfi.stockDate >= '2026-04-01' AND sfi.stockDate < '2026-05-01'
""")
row = r['data'][0]
print(f"   入库(stockDate在4月):")
print(f"     日期范围: {row['min_dt']} ~ {row['max_dt']}")
print(f"     入库PCS: {row['total']}")

# 4. 报废CreateDate和FiscalPeriod对比
print("\n4. 报废CreateDate分布")
r = erp.execute("""
    SELECT CONVERT(VARCHAR(7), CreateDate, 120) AS Month, SUM(QtyOfUnit) AS total
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped' AND FiscalPeriod = '202604'
    GROUP BY CONVERT(VARCHAR(7), CreateDate, 120)
    ORDER BY Month
""")
print(f"   {'月份':<10} {'报废PCS':<15}")
for row in r['data']:
    print(f"   {row['Month']:<10} {row['total']:<15}")

# 5. 如果按CreateDate在4月范围内，报废是多少？
print("\n5. 按CreateDate在4月的报废数据")
r = erp.execute("""
    SELECT SUM(QtyOfUnit) AS total
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped'
      AND CreateDate >= '2026-04-01' AND CreateDate < '2026-05-01'
""")
scrap_by_date = r['data'][0]['total'] or 0
print(f"   报废PCS (CreateDate在4月): {scrap_by_date}")

# 6. 如果按CreateDate在4月，且关联到入库时，入库是多少？
print("\n6. 数据匹配验证")
print(f"   报废PCS (FiscalPeriod=202604): 676,427")
print(f"   报废PCS (CreateDate在4月): {scrap_by_date}")
print(f"   入库PCS (stockDate在4月): 11,862,338")
print(f"   差异: 676,427 - {scrap_by_date} = {676427 - scrap_by_date}")
