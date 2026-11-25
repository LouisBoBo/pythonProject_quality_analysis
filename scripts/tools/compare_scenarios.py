import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("场景对比")
print("=" * 70)

scenarios = []

# 场景1: 保留测试工序
print("\n1. 保留测试工序 + 排除其他非生产工序")
r = erp.execute("""
    SELECT SUM(v.[Area(m2)]) AS total_m2, SUM(v.QtyOfUnit) AS total_pcs
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND ISNULL(v.InspectionProcess, '') NOT IN ('样板组', '品质部', '工艺部', '设备部', '销售部')
      AND ISNULL(v.InspectionItem, '') NOT IN ('实验板', '备品', '实验')
""")
row = r['data'][0] if r.get('success') else {}
scrap_m2 = row.get('total_m2') or 0
scrap_pcs = row.get('total_pcs') or 0
print(f"   报废面积: {scrap_m2:.2f} m2 (ERP: 3,685.21)")
print(f"   报废PCS: {scrap_pcs:,}")

# 场景2: 排除测试工序 + 排除其他非生产工序
print("\n2. 排除测试工序 + 排除其他非生产工序")
r = erp.execute("""
    SELECT SUM(v.[Area(m2)]) AS total_m2, SUM(v.QtyOfUnit) AS total_pcs
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.InspectionProcess <> '测试'
      AND ISNULL(v.InspectionProcess, '') NOT IN ('样板组', '品质部', '工艺部', '设备部', '销售部')
      AND ISNULL(v.InspectionItem, '') NOT IN ('实验板', '备品', '实验')
""")
row = r['data'][0] if r.get('success') else {}
scrap_m2_2 = row.get('total_m2') or 0
scrap_pcs_2 = row.get('total_pcs') or 0
print(f"   报废面积: {scrap_m2_2:.2f} m2 (ERP: 3,685.21)")
print(f"   报废PCS: {scrap_pcs_2:,}")

# 场景3: 只排除样板组和实验/备品
print("\n3. 只排除样板组和实验/备品")
r = erp.execute("""
    SELECT SUM(v.[Area(m2)]) AS total_m2, SUM(v.QtyOfUnit) AS total_pcs
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND ISNULL(v.InspectionProcess, '') <> '样板组'
      AND ISNULL(v.InspectionItem, '') NOT IN ('实验板', '备品', '实验')
""")
row = r['data'][0] if r.get('success') else {}
scrap_m2_3 = row.get('total_m2') or 0
scrap_pcs_3 = row.get('total_pcs') or 0
print(f"   报废面积: {scrap_m2_3:.2f} m2 (ERP: 3,685.21)")
print(f"   报废PCS: {scrap_pcs_3:,}")

# 场景4: 只排除样板组
print("\n4. 只排除样板组")
r = erp.execute("""
    SELECT SUM(v.[Area(m2)]) AS total_m2, SUM(v.QtyOfUnit) AS total_pcs
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND ISNULL(v.InspectionProcess, '') <> '样板组'
""")
row = r['data'][0] if r.get('success') else {}
scrap_m2_4 = row.get('total_m2') or 0
scrap_pcs_4 = row.get('total_pcs') or 0
print(f"   报废面积: {scrap_m2_4:.2f} m2 (ERP: 3,685.21)")
print(f"   报废PCS: {scrap_pcs_4:,}")

# 总结
print("\n" + "=" * 70)
print("总结")
print("=" * 70)
erp_scrap = 3685.21
print(f"ERP日报报废: {erp_scrap:.2f} m2")
print(f"\n各场景与ERP差异:")
print(f"  场景1(保留测试): {scrap_m2 - erp_scrap:+.2f} m2 ({(scrap_m2/erp_scrap-1)*100:+.1f}%)")
print(f"  场景2(排除测试): {scrap_m2_2 - erp_scrap:+.2f} m2 ({(scrap_m2_2/erp_scrap-1)*100:+.1f}%)")
print(f"  场景3(只排除样板): {scrap_m2_3 - erp_scrap:+.2f} m2 ({(scrap_m2_3/erp_scrap-1)*100:+.1f}%)")
print(f"  场景4(只排除样板组): {scrap_m2_4 - erp_scrap:+.2f} m2 ({(scrap_m2_4/erp_scrap-1)*100:+.1f}%)")
