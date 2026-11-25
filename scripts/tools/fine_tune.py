import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("微调ERP口径")
print("=" * 70)

# 目标: 3,685.21 m2
# 当前: 5,255.81 m2
# 需要排除: 1,570.60 m2

# 检查是否有更多需要排除的项目
print("\n检查待排除的项目")
r = erp.execute("""
    SELECT
        v.InspectionProcess,
        v.InspectionItem,
        SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND ISNULL(v.InspectionProcess, '') NOT IN ('样板组', '品质部', '工艺部', '设备部', '销售部')
      AND ISNULL(v.InspectionItem, '') NOT IN ('实验板', '备品', '实验')
    GROUP BY v.InspectionProcess, v.InspectionItem
    ORDER BY SUM(v.[Area(m2)]) DESC
""")
if r.get('success'):
    total = sum(row['total_m2'] for row in r['data'])
    print(f"排除品质/工艺/设备/销售部后: {total:.2f} m2 (目标: 3,685.21)")
    print(f"\nTOP项目:")
    for row in r['data'][:15]:
        print(f"  {row['InspectionProcess']}/{row['InspectionItem']}: {row['total_m2']:.2f}m2")
