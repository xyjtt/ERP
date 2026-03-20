# Project Memory

Updated: 2026-03-18

## Project Positioning

- Project: `jushuitan-link-ops`
- Goal: 聚水潭批量修改线上商品编码
- Strategy: Web RPA only

## Current Judgment

- 不再尝试用开放平台接口修改线上商品编码
- 官方已确认 `/open/webapi/itemapi/itemsku/itemskubatchupload` 不支持该页面编码修改
- 项目正式改为页面脚本方案

## Current External Facts

- 目标页面为“店铺商品管理 > 批量修改线上商品编码”
- 数据源要求支持 Excel、API、手动输入
- 操作后必须重新查询旧编码数量并返回结果

## Next Step

1. 固化脚本项目骨架
2. 获取真实页面 selector
3. 联调“修改后重新查询旧编码数量”
