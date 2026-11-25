import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("检查过滤条件")
print("=" * 70)

# 1. 测试工序的报废PCS
print("\n1. 测试工序报废PCS")
r = erp.execute("""
    SELECT SUM(v.QtyOfUnit) AS total_pcs
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.InspectionProcess = '测试'
""")
test_pcs = r['data'][0]['total_pcs'] if r.get('success') else 0
print(f"   测试工序报废PCS: {test_pcs:,}")

# 2. 检查总报废PCS
print("\n2. 总报废PCS")
r = erp.execute("""
    SELECT SUM(v.QtyOfUnit) AS total_pcs
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
""")
total_pcs = r['data'][0]['total_pcs'] if r.get('success') else 0
print(f"   总报废PCS: {total_pcs:,}")

# 3. 排除测试后的报废PCS
print("\n3. 排除测试工序后的报废PCS")
r = erp.execute("""
    SELECT SUM(v.QtyOfUnit) AS total_pcs
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.InspectionProcess <> '测试'
      AND ISNULL(v.InspectionProcess, '') NOT IN ('样板组', '品质部', '工艺部', '设备部', '销售部')
      AND ISNULL(v.InspectionItem, '') NOT IN ('实验板', '备品', '实验')
""")
no_test_pcs = r['data'][0]['total_pcs'] if r.get('success') else 0
print(f"   排除测试工序后PCS: {no_test_pcs:,}")
print(f"   减少PCS: {total_pcs - no_test_pcs:,}")

# 4. 检查FQC/FQC2等工序
print("\n4. FQC相关工序报废")
r = erp.execute("""
    SELECT InspectionProcess, SUM(v.QtyOfUnit) AS total_pcs, SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND (v.InspectionProcess LIKE '%FQC%' OR v.InspectionProcess LIKE '%划线%')
    GROUP BY InspectionProcess
""")
if r.get('success'):
    for row in r['data']:
        print(f"   {row['InspectionProcess']}: {row['total_pcs']:,} PCS, {row['total_m2']:.2f} m2")

# 5. 按当前API条件检查
print("\n5. 当前API条件下的报废")
non_production_processes = ['样板组', '品质部', '工艺部', '设备部', '销售部', '测试划线', 'FQC', 'FQC(内部客检)', 'FQC2']
non_production_items = ['实验板', '备品', '实验']
np_proc_list = "', '".join(non_production_processes)
np_item_list = "', '".join(non_production_items)

r = erp.execute(f"""
    SELECT SUM(v.QtyOfUnit) AS total_pcs
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND ISNULL(v.InspectionProcess, '') NOT IN ('{np_proc_list}')
      AND ISNULL(v.InspectionItem, '') NOT IN ('{np_item_list}')
      AND ISNULL(v.InspectionProcess, '') <> '测试'
""")
api_pcs = r['data'][0]['total_pcs'] if r.get('success') else 0
print(f"   API条件下PCS: {api_pcs:,}")
print(f"   预期(排除测试工序): {no_test_pcs:,}")
