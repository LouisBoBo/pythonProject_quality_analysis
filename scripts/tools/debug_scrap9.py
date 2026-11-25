import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("深入分析重复报废记录")
print("=" * 70)

# 1. 查看一个重复记录的详细情况
print("\n1. 查看重复最多的工单详细情况")
r = erp.execute("""
    SELECT TOP 10
        v.WONumber,
        v.InspectionProcess,
        v.InspectionItem,
        v.CreateDate,
        v.QtyOfUnit,
        v.[Area(m2)]
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.WONumber = 'RPA012603063651-00-005-005'
    ORDER BY v.CreateDate
""")
if r.get('success') and r['data']:
    print(f"   工单 RPA012603063651-00-005-005 的报废记录:")
    for row in r['data']:
        print(f"   {row['CreateDate']} | {row['InspectionProcess']} | {row['InspectionItem']} | PCS={row['QtyOfUnit']} | Area={row['Area(m2)']:.2f}m2")

# 2. 按工单汇总报废（每个工单只计算一次）
print("\n2. 按工单汇总报废 (每个工单报废只计算一次)")
r = erp.execute("""
    SELECT
        COUNT(DISTINCT v.WONumber) AS wo_cnt,
        SUM(v.QtyOfUnit) AS total_pcs,
        SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
""")
if r.get('success') and r['data']:
    row = r['data'][0]
    print(f"   工单数: {row['wo_cnt']}")
    print(f"   报废PCS: {row['total_pcs']:,}")
    print(f"   报废面积: {row['total_m2']:.2f} m2")
    print(f"   ERP: 3,685.21 m2")
    print(f"   差异: {row['total_m2']-3685.21:.2f} m2")

# 3. 如果按工单去重后还是有差异，可能是工序报废(TType=0)的问题
print("\n3. 只统计TType=1的工单报废")
r = erp.execute("""
    SELECT
        COUNT(DISTINCT v.WONumber) AS wo_cnt,
        SUM(v.QtyOfUnit) AS total_pcs,
        SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.TType = 1
""")
if r.get('success') and r['data']:
    row = r['data'][0]
    print(f"   工单数: {row['wo_cnt']}")
    print(f"   报废PCS: {row['total_pcs']:,}")
    print(f"   报废面积: {row['total_m2']:.2f} m2")

# 4. 检查报废记录中的重复行情况 - 同一工单同一日期同一工序
print("\n4. 检查同一工单+日期+工序的多条记录")
r = erp.execute("""
    SELECT TOP 5
        v.WONumber,
        CONVERT(VARCHAR(10), v.CreateDate, 120) AS CreateDate,
        v.InspectionProcess,
        COUNT(*) AS cnt,
        SUM(v.QtyOfUnit) AS total_pcs,
        SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
    GROUP BY v.WONumber, CONVERT(VARCHAR(10), v.CreateDate, 120), v.InspectionProcess
    HAVING COUNT(*) > 3
    ORDER BY SUM(v.[Area(m2)]) DESC
""")
if r.get('success') and r['data']:
    print(f"   存在3次以上同一工单+日期+工序的记录:")
    for row in r['data']:
        print(f"     {row['WONumber'][:30]}.../{row['CreateDate']}/{row['InspectionProcess']}: {row['cnt']}次")
