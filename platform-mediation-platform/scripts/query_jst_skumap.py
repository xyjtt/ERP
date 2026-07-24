import json

from jst_readonly_db import connect_jst_readonly


conn = connect_jst_readonly()
cursor = conn.cursor()

cursor.execute("SELECT TOP 5 shop_id, shop_i_id, shop_sku_id, sku_id, name, properties_value, pic, shop_price, channel FROM jst_skumap WHERE channel LIKE '%1688%'")
print("Sample 1688 listed products:")
for row in cursor.fetchall():
    print("-" * 60)
    print(f"  shop_id: {row[0]}")
    print(f"  shop_i_id (平台商品ID): {row[1]}")
    print(f"  shop_sku_id (平台SKU ID): {row[2]}")
    print(f"  sku_id (聚水潭SKU): {row[3]}")
    print(f"  name: {row[4]}")
    print(f"  properties_value: {row[5]}")
    print(f"  pic: {row[6]}")
    print(f"  shop_price: {row[7]}")
    print(f"  channel: {row[8]}")

conn.close()
