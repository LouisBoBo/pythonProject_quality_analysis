import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("验证: 去重后的报废面积是否与ERP匹配")
print("=" * 70)

# 1. 按 WONumber + InspectionProcess + InspectionItem 去重统计
print("\n1. 去重后报废统计 (按 WONumber + 工序 + 检验项目)")
r = erp.execute("""
    SELECT
        SUM(v.QtyOfUnit) AS TotalScrap_PCS,
        SUM(v.[Area(m2)]) AS TotalScrap_m2
    FROM (
        SELECT DISTINCT
            v.WONumber,
            v.InspectionProcess,
            v.InspectionItem,
            v.QtyOfUnit,
            v.[Area(m2)]
        FROM View_P_Inspection v
        WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
    ) v
""")
if r.get('success') and r['data']:
    row = r['data'][0]
    print(f"   去重后报废PCS: {row['TotalScrap_PCS']:,}")
    print(f"   去重后报废面积: {row['TotalScrap_m2']:.2f} m2")
    erp_scrap = 3685.21
    diff = row['TotalScrap_m2'] - erp_scrap
    print(f"   ERP日报: {erp_scrap:.2f} m2")
    print(f"   差异: {diff:.2f} m2 ({diff/erp_scrap*100:.1f}%)")

# 2. 按 TType=1 去重统计 (ERP可能只统计最终检验)
print("\n2. TType=1 去重后报废统计")
r = erp.execute("""
    SELECT
        SUM(v.QtyOfUnit) AS TotalScrap_PCS,
        SUM(v.[Area(m2)]) AS TotalScrap_m2
    FROM (
        SELECT DISTINCT
            v.WONumber,
            v.InspectionProcess,
            v.InspectionItem,
            v.TType,
            v.QtyOfUnit,
            v.[Area(m2)]
        FROM View_P_Inspection v
        WHERE v.InspectionResult = 'Scrapped'
          AND v.FiscalPeriod = '202604'
          AND v.TType = 1
    ) v
""")
if r.get('success') and r['data']:
    row = r['data'][0]
    print(f"   去重后报废PCS: {row['TotalScrap_PCS']:,}")
    print(f"   去重后报废面积: {row['TotalScrap_m2']:.2f} m2")

# 3. 对比总结
print("\n" + "=" * 70)
print("总结")
print("=" * 70)
print(f"   ERP日报报废: 3,685.21 m2")
print(f"   系统当前报废: 5,729.92 m2 (未去重)")
print(f"   差异原因: View_P_Inspection 存在重复报废记录")
print(f"   ")
print(f"   建议: 报废统计应使用 DISTINCT 去重查询")
