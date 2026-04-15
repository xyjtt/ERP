import pyodbc
import json
from collections import defaultdict

conn = pyodbc.connect(
    'DRIVER={SQL Server Native Client 10.0};'
    'SERVER=218.93.191.21;'
    'DATABASE=JianSun;'
    'UID=itread;'
    'PWD=eZR3DJd2;'
    'Connection Timeout=10'
)
cursor = conn.cursor()

print("=" * 60)
print("查询jst_skumap所有渠道分布")
print("=" * 60)
cursor.execute("SELECT DISTINCT channel FROM jst_skumap WHERE channel IS NOT NULL")
for row in cursor.fetchall():
    print(f"  channel: {row[0]}")

print("\n" + "=" * 60)
print("查询jst_skumap样例数据（不限渠道）")
print("=" * 60)
cursor.execute("SELECT TOP 5 sku_id, name, properties_value, c_id, channel FROM jst_skumap WHERE properties_value IS NOT NULL AND LEN(properties_value) > 0")
for row in cursor.fetchall():
    print(f"\nSKU: {row[0]}")
    print(f"Name: {row[1]}")
    print(f"Properties: {row[2]}")
    print(f"Category ID: {row[3]}")
    print(f"Channel: {row[4]}")

conn.close()