import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("分析: ERP日报的报废统计口径")
print("=" * 70)

# 1. 检查报废记录中，测试工序的报废占比
print("\n1. 报废工序分析")
r = erp.execute("""
    SELECT
        v.InspectionProcess,
        COUNT(*) AS cnt,
        SUM(v.QtyOfUnit) AS total_pcs,
        SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
    GROUP BY v.InspectionProcess
    ORDER BY SUM(v.[Area(m2)]) DESC
""")
total_area = sum(row['total_m2'] for row in r['data'])
print(f"   {'工序':<15} {'次数':<8} {'PCS':<12} {'面积m2':<12} {'占比':<8}")
print(f"   {'总计':<15} {'':<8} {'':<12} {total_area:<12.2f} {'100%':<8}")
for row in r['data']:
    pct = row['total_m2']/total_area*100
    print(f"   {row['InspectionProcess']:<15} {row['cnt']:<8} {row['total_pcs']:<12,} {row['total_m2']:<12.2f} {pct:.1f}%")

# 2. 如果ERP日报只统计"测试"工序的报废（最终检验）
test_area = 0
for row in r['data']:
    if row['InspectionProcess'] == '测试':
        test_area = row['total_m2']
        break
print(f"\n   如果只统计'测试'(最终检验): {test_area:.2f} m2")
print(f"   ERP日报: 3,685.21 m2")

# 3. 检查是否有"样板"相关的报废（ERP可能不统计样板）
print("\n2. 样板相关报废")
r = erp.execute("""
    SELECT
        v.InspectionProcess,
        v.InspectionItem,
        SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND (v.InspectionItem LIKE '%样板%' OR v.InspectionItem LIKE '%实验%')
    GROUP BY v.InspectionProcess, v.InspectionItem
""")
sample_area = 0
if r.get('success') and r['data']:
    for row in r['data']:
        print(f"   {row['InspectionProcess']}/{row['InspectionItem']}: {row['total_m2']:.2f}m2")
        sample_area += row['total_m2']
print(f"   样板+实验合计: {sample_area:.2f} m2")

# 4. 检查不同工序组合
print("\n3. 工序组合分析")
r = erp.execute("""
    SELECT
        CASE
            WHEN v.InspectionProcess IN ('测试', '成型', '字符') THEN '最终检验'
            WHEN v.InspectionProcess = '样板组' THEN '样板组'
            ELSE '工序检验'
        END AS proc_type,
        COUNT(*) AS cnt,
        SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
    GROUP BY CASE
        WHEN v.InspectionProcess IN ('测试', '成型', '字符') THEN '最终检验'
        WHEN v.InspectionProcess = '样板组' THEN '样板组'
        ELSE '工序检验'
    END
""")
if r.get('success'):
    total = sum(row['total_m2'] for row in r['data'])
    for row in r['data']:
        pct = row['total_m2']/total*100
        print(f"   {row['proc_type']:<15} {row['cnt']:<8} {row['total_m2']:.2f}m2 ({pct:.1f}%)")

# 5. 模拟ERP日报口径：只统计测试+成型+字符
print("\n4. 模拟ERP日报口径")
r = erp.execute("""
    SELECT SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped'
      AND v.FiscalPeriod = '202604'
      AND v.InspectionProcess IN ('测试', '成型', '字符')
""")
sim_erp = r['data'][0]['total_m2'] if r.get('success') else 0
print(f"   模拟ERP口径(测试+成型+字符): {sim_erp:.2f} m2")
print(f"   实际ERP日报: 3,685.21 m2")
print(f"   差异: {sim_erp - 3685.21:.2f} m2")
