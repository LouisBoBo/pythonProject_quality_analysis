import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("入库数据检查")
print("=" * 70)

# 1. 检查FGI_StockFormItem表的字段
print("\n1. FGI_StockFormItem 表字段")
r = erp.execute("""
    SELECT TOP 1 *
    FROM FGI_StockFormItem
    WHERE stockDate >= '2026-04-01' AND stockDate < '2026-05-01'
""")
if r.get('success') and r['data']:
    cols = [c for c in r['data'][0].keys()]
    area_cols = [c for c in cols if 'area' in c.lower() or 'Area' in c or 'sqm' in c.lower() or 'm2' in c.lower()]
    print(f"   总字段数: {len(cols)}")
    print(f"   面积相关字段: {area_cols}")
    print(f"   关键字段: {[c for c in cols if 'qty' in c.lower() or 'Qty' in c or 'qtyof' in c.lower()]}")

# 2. 检查FGI_StockFormItem是否有面积字段
print("\n2. FGI_StockFormItem 的面积数据")
r = erp.execute("""
    SELECT TOP 5 *
    FROM FGI_StockFormItem
    WHERE stockDate >= '2026-04-01' AND stockDate < '2026-05-01'
""")
if r.get('success') and r['data']:
    for col in area_cols:
        vals = [row.get(col) for row in r['data'] if row.get(col)]
        if vals:
            print(f"   {col}: {vals}")

# 3. 计算入库面积（如果字段存在）
print("\n3. 入库面积汇总")
area_fields = ['Area_Sqm', 'Area(m2)', 'NetArea_Sqm', 'GrossArea_Sqm']
for af in area_fields:
    r = erp.execute(f"""
        SELECT SUM(sfiwo.qtyOfPCS * ISNULL(sfi.{af}, 0)) AS total_area
        FROM FGI_StockFormItemWO sfiwo
        INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
        WHERE sfi.stockDate >= '2026-04-01' AND sfi.stockDate < '2026-05-01'
          AND ISNULL(sfi.{af}, 0) > 0
    """)
    total = r['data'][0]['total_area'] if r.get('success') and r['data'] else 0
    if total:
        print(f"   {af}: {total:.2f}")
