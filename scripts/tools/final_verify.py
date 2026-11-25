import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("最终验证")
print("=" * 70)

# 1. 排除测试工序后的报废面积
print("\n1. 排除测试工序后的报废面积")
r = erp.execute("""
    SELECT SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.InspectionProcess <> '测试'
      AND ISNULL(v.InspectionProcess, '') NOT IN ('样板组', '品质部', '工艺部', '设备部', '销售部')
      AND ISNULL(v.InspectionItem, '') NOT IN ('实验板', '备品', '实验')
""")
scrap_m2 = r['data'][0]['total_m2'] if r.get('success') else 0
print(f"   报废面积: {scrap_m2:.2f} m2")
print(f"   ERP日报: 3,685.21 m2")
print(f"   差异: {scrap_m2 - 3685.21:.2f} m2 ({(scrap_m2/3685.21-1)*100:.1f}%)")

# 2. 按当前API条件计算报废面积
print("\n2. 按API条件计算报废面积")
non_production_processes = ['样板组', '品质部', '工艺部', '设备部', '销售部', '测试划线', 'FQC', 'FQC(内部客检)', 'FQC2']
non_production_items = ['实验板', '备品', '实验']
np_proc_list = "', '".join(non_production_processes)
np_item_list = "', '".join(non_production_items)

r = erp.execute(f"""
    SELECT SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND ISNULL(v.InspectionProcess, '') NOT IN ('{np_proc_list}')
      AND ISNULL(v.InspectionItem, '') NOT IN ('{np_item_list}')
      AND ISNULL(v.InspectionProcess, '') <> '测试'
""")
api_scrap_m2 = r['data'][0]['total_m2'] if r.get('success') else 0
print(f"   API报废面积: {api_scrap_m2:.2f} m2")

# 3. 入库面积
print("\n3. 入库数据")
print(f"   入库PCS: 11,862,338")
print(f"   ERP入库面积: 58,086.87 m2")
print(f"   换算: 1 m2 = {11862338/58086.87:.0f} PCS")

# 4. 计算报废率
print("\n4. 报废率对比")
erp_scrap_m2 = 3685.21
erp_stock_m2 = 58086.87
erp_rate = erp_scrap_m2 / erp_stock_m2 * 100

api_scrap_pcs = 400459  # API返回
api_scrap_m2 = api_scrap_m2
api_stock_pcs = 11862338

api_rate_pcs = api_scrap_pcs / (api_scrap_pcs + api_stock_pcs) * 100
api_rate_m2 = api_scrap_m2 / (api_scrap_m2 + erp_stock_m2) * 100

print(f"   ERP日报报废率: {erp_rate:.2f}%")
print(f"   系统(PCS): {api_rate_pcs:.2f}%")
print(f"   系统(面积): {api_rate_m2:.2f}%")
print(f"\n   结论: PCS和面积计算的报废率存在差异，原因是小板多PCS少面积，大板少PCS多面积")
