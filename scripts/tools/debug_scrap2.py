import _bootstrap  # noqa: F401  # ensure project paths
import sys
sys.path.insert(0, r'd:\AI助理\数据对接系统')
from erp_connector import ERPConnector

erp = ERPConnector()
erp.connect()

print("=" * 70)
print("分析: 报废数据差异来源")
print("=" * 70)

# 1. 检查入库数据是否关联到工单的FiscalPeriod
print("\n1. 入库数据关联工单FiscalPeriod")
r = erp.execute("""
    SELECT TOP 5
        wo.FiscalPeriod AS wo_fp,
        sfi.stockDate,
        sfiwo.qtyOfPCS
    FROM FGI_StockFormItemWO sfiwo
    INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
    INNER JOIN P_WO wo ON sfiwo.woId = wo.recId
    WHERE sfi.stockDate >= '2026-04-01' AND sfi.stockDate < '2026-05-01'
""")
for row in r['data']:
    print(f"   工单FP={row['wo_fp']}, 入库日期={row['stockDate']}, PCS={row['qtyOfPCS']}")

# 2. 检查工单表的FiscalPeriod分布
print("\n2. P_WO工单的FiscalPeriod分布")
r = erp.execute("""
    SELECT FiscalPeriod, COUNT(*) AS cnt, SUM(qty_PCS_PRODUCED) AS produced
    FROM P_WO
    WHERE FiscalPeriod IS NOT NULL
    GROUP BY FiscalPeriod
    ORDER BY FiscalPeriod DESC
""")
print(f"   {'FiscalPeriod':<15} {'工单数':<10} {'产出PCS':<15}")
for row in r['data'][:6]:
    print(f"   {row['FiscalPeriod']:<15} {row['cnt']:<10} {row['produced']:<15}")

# 3. 检查P_WO工单FiscalPeriod是否为NULL
print("\n3. P_WO工单FiscalPeriod为NULL的比例")
r = erp.execute("""
    SELECT
        COUNT(*) AS total,
        SUM(CASE WHEN FiscalPeriod IS NULL THEN 1 ELSE 0 END) AS null_cnt,
        SUM(CASE WHEN FiscalPeriod = '202604' THEN 1 ELSE 0 END) AS fp_202604
    FROM P_WO
    WHERE qty_PCS_PRODUCED > 0 OR reject_PCS > 0
""")
row = r['data'][0]
print(f"   总工单: {row['total']}, NULL: {row['null_cnt']}, FP=202604: {row['fp_202604']}")
print(f"   NULL占比: {row['null_cnt']/row['total']*100:.1f}%")

# 4. 检查入库数据中，有多少工单的FiscalPeriod是202604
print("\n4. 入库数据中工单FiscalPeriod分布")
r = erp.execute("""
    SELECT
        wo.FiscalPeriod,
        COUNT(*) AS cnt,
        SUM(sfiwo.qtyOfPCS) AS total_pcs
    FROM FGI_StockFormItemWO sfiwo
    INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
    INNER JOIN P_WO wo ON sfiwo.woId = wo.recId
    WHERE sfi.stockDate >= '2026-04-01' AND sfi.stockDate < '2026-05-01'
    GROUP BY wo.FiscalPeriod
    ORDER BY total_pcs DESC
""")
print(f"   {'FiscalPeriod':<15} {'记录数':<10} {'入库PCS':<15}")
for row in r['data'][:6]:
    print(f"   {row['FiscalPeriod'] or 'NULL':<15} {row['cnt']:<10} {row['total_pcs']:<15}")

# 5. 关键对比：只统计入库PCS中工单FiscalPeriod=202604的
print("\n5. 按工单FiscalPeriod=202604的入库数据")
r = erp.execute("""
    SELECT SUM(sfiwo.qtyOfPCS) AS total_pcs
    FROM FGI_StockFormItemWO sfiwo
    INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
    INNER JOIN P_WO wo ON sfiwo.woId = wo.recId
    WHERE sfi.stockDate >= '2026-04-01' AND sfi.stockDate < '2026-05-01'
      AND wo.FiscalPeriod = '202604'
""")
fp_stock = r['data'][0]['total_pcs'] or 0
print(f"   入库PCS (工单FP=202604): {fp_stock}")

# 6. 对比报废数据
print("\n6. 数据对比")
scrap = 676427
print(f"   报废PCS (FP=202604): {scrap}")
print(f"   入库PCS (日期范围4月): {11862338}")
print(f"   入库PCS (工单FP=202604): {fp_stock}")
if fp_stock > 0:
    rate = scrap * 100 / (scrap + fp_stock)
    print(f"   报废率 (基于工单FP): {rate:.2f}%")
