import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("最终分析: ERP日报报废口径")
print("=" * 70)

# 1. 目标: 3,685.21 m2
# 让我们测试各种可能的过滤条件组合

# 测试1: 排除样板组
print("\n测试1: 排除样板组")
r = erp.execute("""
    SELECT SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.InspectionProcess <> '样板组'
""")
t1 = r['data'][0]['total_m2'] if r.get('success') else 0
print(f"   排除样板组: {t1:.2f} m2 (目标: 3,685.21)")

# 测试2: 排除样板组+实验板+备品
print("\n测试2: 排除样板组 + 实验板/备品/实验")
r = erp.execute("""
    SELECT SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.InspectionProcess NOT IN ('样板组')
      AND v.InspectionItem NOT IN ('实验板', '备品', '实验')
""")
t2 = r['data'][0]['total_m2'] if r.get('success') else 0
print(f"   排除样板组+实验/备品: {t2:.2f} m2")

# 测试3: 只保留量产工序
print("\n测试3: 只保留量产工序 (压合/阻焊/钻孔/外层/测试/成型等)")
r = erp.execute("""
    SELECT SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.InspectionProcess IN ('压合', '阻焊', '钻孔', '外层线路', '测试', '成型',
                                   '字符', '沉铜', '图形电镀', '内层线路', '无铅喷锡', '沉金')
""")
t3 = r['data'][0]['total_m2'] if r.get('success') else 0
print(f"   量产工序合计: {t3:.2f} m2")

# 测试4: 按WONumber前缀区分(RPA=样板 vs WOA=量产)
print("\n测试4: 按工单号前缀区分 (RPA=样板, WOA=量产)")
r = erp.execute("""
    SELECT
        CASE
            WHEN v.WONumber LIKE 'RPA%' THEN '样板'
            WHEN v.WONumber LIKE 'WOA%' THEN '量产'
            ELSE '其他'
        END AS wo_type,
        COUNT(*) AS cnt,
        SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
    GROUP BY CASE
        WHEN v.WONumber LIKE 'RPA%' THEN '样板'
        WHEN v.WONumber LIKE 'WOA%' THEN '量产'
        ELSE '其他'
    END
""")
if r.get('success'):
    for row in r['data']:
        print(f"   {row['wo_type']}: {row['cnt']}条, {row['total_m2']:.2f}m2")

# 测试5: 量产+排除一些非生产工序
print("\n测试5: WOA量产 + 排除品质部/工艺部/设备部/销售部")
r = erp.execute("""
    SELECT SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.WONumber LIKE 'WOA%'
      AND v.InspectionProcess NOT IN ('品质部', '工艺部', '设备部', '销售部', '样板组')
""")
t5 = r['data'][0]['total_m2'] if r.get('success') else 0
print(f"   WOA量产(排除非生产工序): {t5:.2f} m2")

# 测试6: 最终结果对比
print("\n" + "=" * 70)
print("对比")
print("=" * 70)
print(f"ERP日报: 3,685.21 m2")
print(f"测试5: {t5:.2f} m2 (差异: {t5-3685.21:.2f})")
