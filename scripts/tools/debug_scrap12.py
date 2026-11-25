import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("最终匹配: ERP日报报废口径")
print("=" * 70)

# 继续排除更多非生产相关项目
print("\n测试: 排除更多非生产相关项目")
r = erp.execute("""
    SELECT
        v.InspectionItem,
        SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.InspectionProcess NOT IN ('样板组')
      AND v.InspectionItem NOT IN ('实验板', '备品', '实验')
    GROUP BY v.InspectionItem
    ORDER BY SUM(v.[Area(m2)]) DESC
""")
if r.get('success'):
    # 显示所有项目，看哪些可能是ERP不统计的
    total = sum(row['total_m2'] for row in r['data'])
    print(f"   剩余项目总计: {total:.2f} m2")
    print(f"\n   {'检验项目':<20} {'面积m2':<12} {'占比':<8}")
    for row in r['data'][:20]:
        pct = row['total_m2']/total*100
        print(f"   {row['InspectionItem'] or '':<20} {row['total_m2']:<12.2f} {pct:.1f}%")

# 找出差异来源
print("\n" + "=" * 70)
print("差异分析")
print("=" * 70)
target = 3685.21
current = 3752.70
diff = current - target
print(f"目标值: {target:.2f} m2")
print(f"当前值: {current:.2f} m2")
print(f"差异: {diff:.2f} m2")
print(f"\n差异可能来源: 67 m2左右的报废项目被ERP排除")
