import pyodbc

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
print("jst_sku 字段列表:")
print("=" * 60)
cursor.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'jst_sku' ORDER BY ORDINAL_POSITION")
for row in cursor.fetchall():
    print(f"  {row[0]}")

print("\n" + "=" * 60)
print("jst_skumap 字段列表:")
print("=" * 60)
cursor.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'jst_skumap' ORDER BY ORDINAL_POSITION")
for row in cursor.fetchall():
    print(f"  {row[0]}")

print("\n" + "=" * 60)
print("jst_sku 样例数据:")
print("=" * 60)
cursor.execute("SELECT TOP 1 * FROM jst_sku WHERE name IS NOT NULL")
columns = [desc[0] for desc in cursor.description]
row = cursor.fetchone()
if row:
    for i, col in enumerate(columns):
        val = row[i]
        if val is not None and str(val).strip():
            print(f"  {col}: {val}")

print("\n" + "=" * 60)
print("jst_skumap 1688已上架样例:")
print("=" * 60)
cursor.execute("SELECT TOP 1 * FROM jst_skumap WHERE channel LIKE '%1688%'")
columns = [desc[0] for desc in cursor.description]
row = cursor.fetchone()
if row:
    for i, col in enumerate(columns):
        val = row[i]
        if val is not None and str(val).strip():
            print(f"  {col}: {val}")

conn.close()