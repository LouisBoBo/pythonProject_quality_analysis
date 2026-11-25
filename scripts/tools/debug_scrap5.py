import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("检查: View_P_Inspection 结构和重复数据")
print("=" * 70)

# 1. 检查字段名
print("\n1. View_P_Inspection 字段")
r = erp.execute("""
    SELECT TOP 1 *
    FROM View_P_Inspection
    WHERE InspectionResult = 'Scrapped' AND FiscalPeriod = '202604'
""")
if r.get('success') and r['data']:
    cols = list(r['data'][0].keys())
    print(f"   字段数: {len(cols)}")
    print(f"   字段: {cols}")

# 2. 用正确的字段检查重复
print("\n2. 检查是否有重复报废记录")
r = erp.execute("""
    SELECT TOP 20
        v.MfgPartCode,
        v.InspectionProcess,
        v.InspectionItem,
        v.CreateDate,
        v.QtyOfUnit,
        v.InspectionResult
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
    ORDER BY v.MfgPartCode, v.CreateDate DESC
""")
if r.get('success'):
    print(f"   样例: {len(r['data'])}")
    if r['data']:
        print(f"   第一条: {r['data'][0]}")

# 3. 检查TType字段（之前注释提到TType=1是样板组/测试）
print("\n3. 按TType分类的报废")
r = erp.execute("""
    SELECT
        ISNULL(v.TType, -1) AS TType,
        COUNT(*) AS cnt,
        SUM(v.QtyOfUnit) AS total
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
    GROUP BY v.TType
    ORDER BY total DESC
""")
if r.get('success'):
    print(f"   {'TType':<10} {'记录数':<10} {'报废PCS':<15}")
    for row in r['data']:
        print(f"   {row['TType']:<10} {row['cnt']:<10} {row['total']:<15}")

# 4. 按工序分类的报废
print("\n4. 按工序分类的报废 TOP10")
r = erp.execute("""
    SELECT TOP 10
        ISNULL(v.InspectionProcess, '未知') AS process,
        COUNT(*) AS cnt,
        SUM(v.QtyOfUnit) AS total
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
    GROUP BY v.InspectionProcess
    ORDER BY total DESC
""")
if r.get('success'):
    print(f"   {'工序':<15} {'记录数':<10} {'报废PCS':<15}")
    for row in r['data']:
        print(f"   {row['process']:<15} {row['cnt']:<10} {row['total']:<15}")

# 5. 关键对比：ERP日报的报废工序 vs 系统的工序
print("\n5. 对比ERP报废工序")
print("   ERP日报显示的报废工序可能是不同的分组方式")
print("   需要知道ERP日报的具体统计口径")

# 6. 检查报废记录的时间跨度
print("\n6. 报废记录时间跨度")
r = erp.execute("""
    SELECT
        MIN(v.CreateDate) AS min_dt,
        MAX(v.CreateDate) AS max_dt,
        DATEDIFF(day, MIN(v.CreateDate), MAX(v.CreateDate)) AS days_diff
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
""")
if r.get('success'):
    row = r['data'][0]
    print(f"   最早: {row['min_dt']}")
    print(f"   最晚: {row['max_dt']}")
    print(f"   天数跨度: {row['days_diff']}天")

# 7. 计算如果ERP报废率是6.00%，对应的PCS应该是多少
print("\n7. ERP报废率反推")
erp_scrap_area = 3685.21  # m²
erp_stock_area = 58086.87  # m²
# 假设平均每平方英尺=xxx PCS
# PCB行业一般用m²或ft²
# 如果每m² = 10 PCS (假设)
per_sqm_pcs = erp_stock_area / 11862338  # 从系统数据反推
print(f"   ERP报废面积: {erp_scrap_area} m2")
print(f"   ERP入库面积: {erp_stock_area} m2")
print(f"   ERP报废率: {erp_scrap_area/erp_stock_area*100:.2f}%")
print(f"   系统入库PCS: {11862338}")
print(f"   反推每m2对应PCS: {per_sqm_pcs:.2f}")
