import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("分析测试工序报废")
print("=" * 70)

# 检查测试工序但检验项目为空的记录
print("\n1. 测试工序的报废项目分布")
r = erp.execute("""
    SELECT
        ISNULL(v.InspectionItem, '(空)') AS item,
        COUNT(*) AS cnt,
        SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.InspectionProcess = '测试'
    GROUP BY ISNULL(v.InspectionItem, '(空)')
    ORDER BY SUM(v.[Area(m2)]) DESC
""")
if r.get('success'):
    total = sum(row['total_m2'] for row in r['data'])
    print(f"测试工序总计: {total:.2f} m2")
    for row in r['data']:
        print(f"  {row['item']}: {row['cnt']}条, {row['total_m2']:.2f}m2")

# 检查测试工序报废对应的入库状态
print("\n2. 排除测试工序后的报废")
r = erp.execute("""
    SELECT SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.InspectionProcess <> '测试'
      AND ISNULL(v.InspectionProcess, '') NOT IN ('样板组', '品质部', '工艺部', '设备部', '销售部')
      AND ISNULL(v.InspectionItem, '') NOT IN ('实验板', '备品', '实验')
""")
no_test = r['data'][0]['total_m2'] if r.get('success') else 0
print(f"排除测试工序后: {no_test:.2f} m2 (目标: 3,685.21)")

# 继续排除更多
print("\n3. 最终ERP口径模拟")
r = erp.execute("""
    SELECT SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      -- 排除非生产工序
      AND ISNULL(v.InspectionProcess, '') NOT IN ('样板组', '品质部', '工艺部', '设备部', '销售部', '测试划线', 'FQC', 'FQC(内部客检)', 'FQC2')
      -- 排除非生产项目
      AND ISNULL(v.InspectionItem, '') NOT IN ('实验板', '备品', '实验')
""")
final = r['data'][0]['total_m2'] if r.get('success') else 0
print(f"最终口径: {final:.2f} m2 (目标: 3,685.21)")
print(f"差异: {final - 3685.21:.2f} m2")
