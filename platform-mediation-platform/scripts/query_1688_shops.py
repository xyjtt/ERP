from jst_readonly_db import connect_jst_readonly


conn = connect_jst_readonly()
cursor = conn.cursor()

print("=" * 60)
print("1688店铺统计")
print("=" * 60)

cursor.execute("SELECT COUNT(*) FROM jst_shopInfo WHERE shop_site = '阿里巴巴'")
total = cursor.fetchone()[0]
print(f"1688店铺总数: {total}")

print("\n" + "=" * 60)
print("1688店铺列表:")
print("=" * 60)
cursor.execute("SELECT shop_id, shop_name, group_name FROM jst_shopInfo WHERE shop_site = '阿里巴巴' ORDER BY shop_id")
for row in cursor.fetchall():
    print(f"  shop_id: {row[0]}, shop_name: {row[1]}, group: {row[2]}")

conn.close()
