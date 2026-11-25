import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("关键发现: 报废数据应该用Area(m2)而非QtyOfUnit")
print("=" * 70)

scrap_m2 = 0
scrap_pcs = 0

# 1. 按面积统计报废
print("\n1. 按Area(m2)统计报废 (FiscalPeriod=202604)")
r = erp.execute("""
    SELECT
        SUM(v.QtyOfUnit) AS TotalScrap_PCS,
        SUM(v.[Area(m2)]) AS TotalScrap_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
""")
if r.get('success') and r['data']:
    row = r['data'][0]
    scrap_m2 = row['TotalScrap_m2'] or 0
    scrap_pcs = row['TotalScrap_PCS'] or 0
    print(f"   报废PCS: {scrap_pcs:,}")
    print(f"   报废面积: {scrap_m2:.2f} m2")
    erp_scrap_m2 = 3685.21
    print(f"   ERP日报报废: {erp_scrap_m2:.2f} m2")
    if erp_scrap_m2 > 0:
        print(f"   差异: {scrap_m2 - erp_scrap_m2:.2f} m2 ({(scrap_m2/erp_scrap_m2-1)*100:.1f}%)")

# 2. 按TType分类统计面积
print("\n2. 按TType分类的报废面积")
r = erp.execute("""
    SELECT
        ISNULL(v.TType, -1) AS TType,
        SUM(v.QtyOfUnit) AS TotalScrap_PCS,
        SUM(v.[Area(m2)]) AS TotalScrap_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
    GROUP BY v.TType
    ORDER BY TotalScrap_m2 DESC
""")
if r.get('success'):
    print(f"   {'TType':<10} {'报废PCS':<15} {'报废面积m2':<15}")
    for row in r['data']:
        print(f"   {row['TType']:<10} {row['TotalScrap_PCS']:<15,} {row['TotalScrap_m2']:<15.2f}")

# 3. 按工序分类统计面积
print("\n3. 按工序分类的报废面积 TOP10")
r = erp.execute("""
    SELECT TOP 10
        v.InspectionProcess,
        SUM(v.QtyOfUnit) AS TotalScrap_PCS,
        SUM(v.[Area(m2)]) AS TotalScrap_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
    GROUP BY v.InspectionProcess
    ORDER BY SUM(v.[Area(m2)]) DESC
""")
if r.get('success'):
    print(f"   {'工序':<15} {'报废PCS':<15} {'报废面积m2':<15}")
    for row in r['data']:
        print(f"   {row['InspectionProcess']:<15} {row['TotalScrap_PCS']:<15,} {row['TotalScrap_m2']:<15.2f}")

# 4. 检查入库数据的面积
print("\n4. FGI入库数据")
r = erp.execute("""
    SELECT SUM(sfiwo.qtyOfPCS) AS TotalStock_PCS
    FROM FGI_StockFormItemWO sfiwo
    INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
    WHERE sfi.stockDate >= '2026-04-01' AND sfi.stockDate < '2026-05-01'
""")
stock_pcs = r['data'][0]['TotalStock_PCS'] or 0 if r.get('success') else 0
print(f"   入库PCS: {stock_pcs:,}")
print(f"   ERP日报入库: 58,086.87 m2")

# 5. 检查FGI_StockFormItem是否有面积字段
print("\n5. FGI_StockFormItem 表字段")
r = erp.execute("""
    SELECT TOP 1 *
    FROM FGI_StockFormItem
""")
if r.get('success') and r['data']:
    cols = [c for c in r['data'][0].keys() if 'area' in c.lower() or 'Area' in c or 'sqm' in c.lower()]
    print(f"   面积相关字段: {cols if cols else '无'}")
