# 聚水潭批量更新在线商品编码为停产下架编码（新项目）

## 目标
自动化完成聚水潭店铺商品管理页面：按 SKU 查询 -> 批量更新在线商品编码 -> 选择批量填充 -> 填写停产下架编码 -> 保存提示 -> 再次查询数据并保存。

## 目录
- `rpa/jushuitan_batch_update.py`：自动化脚本
- `config/systems/jushuitan_batch_update.json`：执行步骤 selector
- 输入 Excel：根据字段 `统计日期新`、`处理说明`、`下架备注` 过滤
- 输出 CSV：查询结果保存

## 1. 配置步骤
1. 编辑 `config/systems/jushuitan_batch_update.json`，把页面的实际 selector 填写到对应 field。以下为示例：
   - `sku_tab`
   - `sku_search_input`
   - `platform_filter_dropdown`
   - `search_button`
   - `batch_update_button`
   - `batch_fill_mode`
   - `batch_fill_value`
   - `batch_fill_confirm`
   - `batch_fill_message`
   - `result_row` 和 `result_columns`
2. 确认 `manage_page_url` 为店铺商品管理页面 URL。

## 2. 数据源格式
Excel 需要包含列：
- `统计日期新`
- `商品编码`
- `处理说明`
- `下架备注`

过滤规则：
- `统计日期新` 等于执行日期
- `处理说明` 等于 `下架`
- `下架备注` 不包含 `京喜需下架`

## 3. 运行命令
```bash
python rpa/jushuitan_batch_update.py \
  --template templates/your_source.xlsx \
  --output logs/jushuitan_batch_update_result.csv \
  --config config/systems/jushuitan_batch_update.json \
  --date 2026-03-18 \
  --platform 抖音 \
  --new-code txcj
```

## 4. 结果
- 脚本会在 `logs/jushuitan_batch_update_result.csv` 保存查询结果
- 若配置了 `batch_fill_message`，会保存 `logs/jushuitan_batch_update_result.message.txt`

## 5. 注意
- 请先登录聚水潭并手动保持已登录状态。
- 若页面结构变化，及时使用 `docs/SELECTOR_PROBE_GUIDE.md` 重新抓取 selectors。
- 查询结果保存后，发送给对应群组。
