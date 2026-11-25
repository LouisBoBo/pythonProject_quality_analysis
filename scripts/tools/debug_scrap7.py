import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("分析报废差异来源")
print("=" * 70)

# 1. 如果ERP只统计TType=1
print("\n1. TType=1 vs TType=0 差异")
r = erp.execute("""
    SELECT
        ISNULL(v.TType, -1) AS TType,
        SUM(v.[Area(m2)]) AS TotalScrap_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
    GROUP BY v.TType
""")
ttype_data = {}
for row in r['data']:
    ttype_data[row['TType']] = row['TotalScrap_m2'] or 0
t1 = ttype_data.get(1, 0)
t0 = ttype_data.get(0, 0)
print(f"   TType=1: {t1:.2f} m2")
print(f"   TType=0: {t0:.2f} m2")
print(f"   合计: {t1+t0:.2f} m2")

# 2. 检查报废记录中是否有重复（同一工单+同一工序+同一物料）
print("\n2. 检查报废记录的重复情况")
r = erp.execute("""
    SELECT
        v.WONumber,
        v.InspectionProcess,
        v.InspectionItem,
        COUNT(*) AS cnt,
        SUM(v.QtyOfUnit) AS total_pcs,
        SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
    GROUP BY v.WONumber, v.InspectionProcess, v.InspectionItem
    HAVING COUNT(*) > 1
    ORDER BY SUM(v.[Area(m2)]) DESC
""")
if r.get('success') and r['data']:
    print(f"   存在重复的组合数: {len(r['data'])}")
    total_dup_m2 = sum(row['total_m2'] for row in r['data'])
    print(f"   重复记录的报废面积合计: {total_dup_m2:.2f} m2")
    print(f"   TOP5 重复记录:")
    for row in r['data'][:5]:
        print(f"     {row['WONumber']}/{row['InspectionProcess']}: {row['cnt']}次, {row['total_m2']:.2f}m2")
else:
    print(f"   无重复记录")

# 3. 检查报废记录中 InspectionItem 是否有特殊值
print("\n3. 报废项目分布")
r = erp.execute("""
    SELECT TOP 20
        v.InspectionItem,
        COUNT(*) AS cnt,
        SUM(v.QtyOfUnit) AS total_pcs,
        SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
    GROUP BY v.InspectionItem
    ORDER BY SUM(v.[Area(m2)]) DESC
""")
if r.get('success'):
    print(f"   {'检验项目':<20} {'次数':<8} {'PCS':<12} {'面积m2':<12}")
    for row in r['data']:
        item = row['InspectionItem'] or ''
        pcs = row['total_pcs'] or 0
        m2 = row['total_m2'] or 0
        print(f"   {item:<20} {row['cnt']:<8} {pcs:<12,} {m2:<12.2f}")

# 4. 检查日期不匹配的情况
print("\n4. CreateDate vs FiscalPeriod 日期差异")
r = erp.execute("""
    SELECT TOP 10
        v.CreateDate,
        v.FiscalPeriod,
        v.InspectionProcess,
        v.[Area(m2)]
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
      AND v.CreateDate < '2026-04-01'
""")
if r.get('success') and r['data']:
    print(f"   CreateDate < FiscalPeriod起始日期 的记录:")
    for row in r['data']:
        print(f"     CreateDate={row['CreateDate']}, FP={row['FiscalPeriod']}, Area={row['Area(m2)']:.2f}m2")
else:
    print(f"   无CreateDate早于4月1日的记录")

# 5. 检查报废记录的Note字段
print("\n5. 报废记录的Note/备注分布")
r = erp.execute("""
    SELECT
        CASE WHEN v.Note IS NULL OR v.Note = '' THEN '无备注'
             ELSE '有备注' END AS has_note,
        COUNT(*) AS cnt,
        SUM(v.[Area(m2)]) AS total_m2
    FROM View_P_Inspection v
    WHERE v.InspectionResult = 'Scrapped' AND v.FiscalPeriod = '202604'
    GROUP BY CASE WHEN v.Note IS NULL OR v.Note = '' THEN '无备注'
                  ELSE '有备注' END
""")
if r.get('success'):
    for row in r['data']:
        print(f"   {row['has_note']}: {row['cnt']}条, {row['total_m2']:.2f}m2")
