import _bootstrap  # noqa: F401  # ensure project paths
import os
import sys

import pyodbc

# 禁止硬编码账号；从环境变量读取
server = os.environ.get("QA_DEBUG_MSSQL_SERVER", "").strip()
database = os.environ.get("QA_DEBUG_MSSQL_DATABASE", "atomLive").strip()
uid = os.environ.get("QA_DEBUG_MSSQL_UID", "").strip()
pwd = os.environ.get("QA_DEBUG_MSSQL_PWD", "").strip()
if not (server and uid and pwd):
    print(
        "请设置环境变量 QA_DEBUG_MSSQL_SERVER / QA_DEBUG_MSSQL_UID / QA_DEBUG_MSSQL_PWD "
        "后再运行（禁止把密码写入脚本）。",
        file=sys.stderr,
    )
    sys.exit(1)

conn = pyodbc.connect(
    f"DRIVER={{SQL Server}};SERVER={server};DATABASE={database};UID={uid};PWD={pwd}"
)
cursor = conn.cursor()

# 1. 检查料号表中的格式
print("=== 料号表中类似料号的格式 ===")
cursor.execute(
    """
SELECT TOP 20 mfgPartCode
FROM E_JobMfgParts
WHERE mfgPartCode LIKE '%19865%'
ORDER BY mfgPartCode
"""
)
for row in cursor.fetchall():
    print(row[0])

print()
print("=== 检查完整料号是否存在 ===")
cursor.execute(
    """
SELECT mfgPartCode, mfgPartName
FROM E_JobMfgParts
WHERE mfgPartCode = 'B60P04L19865A0'
"""
)
result = cursor.fetchone()
if result:
    print(f"料号存在: {result[0]} - {result[1]}")
else:
    print("料号不存在")

# 2. 检查工单表中这个料号的工单
print()
print("=== 工单表中的数据 ===")
cursor.execute(
    """
SELECT TOP 10
    P_WO.woNumber,
    P_WO.qty_PCS_PRODUCED,
    P_WO.reject_PCS,
    P_WO.createDate,
    E_JobMfgParts.mfgPartCode
FROM P_WO
INNER JOIN E_JobMfgParts ON P_WO.mfgPartId = E_JobMfgParts.recId
WHERE E_JobMfgParts.mfgPartCode LIKE '%19865%'
ORDER BY P_WO.createDate DESC
"""
)
for row in cursor.fetchall():
    print(f"工单: {row[0]}, 投入: {row[1]}, 报废: {row[2]}, 日期: {row[3]}, 料号: {row[4]}")

# 3. 检查这个料号是否有成品入库记录
print()
print("=== 成品库入库记录 ===")
cursor.execute(
    """
SELECT COUNT(*) as cnt
FROM P_WO
INNER JOIN E_JobMfgParts ON P_WO.mfgPartId = E_JobMfgParts.recId
WHERE E_JobMfgParts.mfgPartCode = 'B60P04L19865A0'
"""
)
result = cursor.fetchone()
print(f"工单数量: {result[0]}")

conn.close()
