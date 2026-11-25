import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("检查: View_P_Inspection 是否有重复报废记录")
print("=" * 70)

# 1. 检查是否有重复记录（同一工单、同一料号、同一工序、同一检验项目）
print("\n1. 检查潜在重复记录")
r = erp.execute("""
    SELECT TOP 20
        v.MOBarCode,
        v.MfgPartCode,
        v.InspectionProcess,
        v.InspectionItem,
        v.CreateDate,
        v.QtyOfUnit,
        v.InspectionResult
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
    ORDER BY v.MOBarCode, v.CreateDate DESC
""")
print(f"   检查字段: MOBarCode, MfgPartCode, InspectionProcess, InspectionItem, CreateDate")
print(f"   样例数量: {len(r['data'])}")

# 2. 检查同一MOBarCode的报废次数
print("\n2. 同一MOBarCode的报废次数分布")
r = erp.execute("""
    SELECT MOBarCode, COUNT(*) AS cnt, SUM(QtyOfUnit) AS total
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped' AND FiscalPeriod = '202604'
    GROUP BY MOBarCode
    HAVING COUNT(*) > 1
    ORDER BY total DESC
""")
print(f"   有重复报废的MOBarCode数量: {len(r['data'])}")
if r['data']:
    print(f"   TOP5 重复报废:")
    for row in r['data'][:5]:
        print(f"     {row['MOBarCode']}: {row['cnt']}次, 合计PCS={row['total']}")

# 3. 检查同一物料、同一工序的报废次数
print("\n3. 同一料号+工序的报废次数分布")
r = erp.execute("""
    SELECT MfgPartCode, InspectionProcess, COUNT(*) AS cnt, SUM(QtyOfUnit) AS total
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped' AND FiscalPeriod = '202604'
    GROUP BY MfgPartCode, InspectionProcess
    HAVING COUNT(*) > 10
    ORDER BY total DESC
""")
print(f"   有大量重复的组合数: {len(r['data'])}")
if r['data']:
    print(f"   TOP5:")
    for row in r['data'][:5]:
        print(f"     {row['MfgPartCode']}/{row['InspectionProcess']}: {row['cnt']}次, 合计={row['total']}PCS")

# 4. 检查ERP日报的数据口径（用area而非PCS）
print("\n4. 检查是否有面积(area)字段")
r = erp.execute("""
    SELECT TOP 1 *
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped' AND FiscalPeriod = '202604'
""")
if r['data']:
    cols = [k for k in r['data'][0].keys() if 'area' in k.lower() or 'sqm' in k.lower() or 'size' in k.lower()]
    print(f"   面积相关字段: {cols if cols else '无'}")
    print(f"   所有字段: {list(r['data'][0].keys())}")

# 5. 直接对比：如果ERP报废面积=3,685.21 m²，推算平均每PCS多少m²
print("\n5. 单位换算分析")
scrap_pcs = 676427
scrap_area = 3685.21  # ERP m²
stock_area = 58086.87  # ERP m²
ratio = scrap_area / scrap_pcs if scrap_pcs > 0 else 0
print(f"   系统报废PCS: {scrap_pcs}")
print(f"   ERP报废面积: {scrap_area} m²")
print(f"   平均每PCS: {ratio:.6f} m²")
if stock_area:
    erp_rate = scrap_area / stock_area * 100
    print(f"   ERP报废率: {erp_rate:.2f}%")
sys_rate = scrap_pcs * 100 / (scrap_pcs + 11862338)
print(f"   系统报废率: {sys_rate:.2f}%")
print(f"\n   问题: ERP按面积算，系统按PCS算，无法直接对比")
