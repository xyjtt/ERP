# ERP

`ERP` 是用于集中管理 ERP / 电商运营自动化项目的私有仓库。

当前收录项目：

- `furniture-uploader`
- `jushuitan-sku-offline-batch`
- `jushuitan-link-ops`

## 项目说明

### `furniture-uploader`

定位：

- 家具铺货自动化项目
- 当前以聚水潭 Web + 1688 渠道为第一阶段主线
- 主要使用 Python + Selenium

主目录：

- `furniture-uploader/`

### `jushuitan-link-ops`

定位：

- 聚水潭链接运维项目
- 当前核心任务是批量修改线上商品编码
- 主要使用 Playwright

主目录：

- `jushuitan-link-ops/`

### `jushuitan-sku-offline-batch`

定位：

- 当前聚水潭“批量更新商品编码”主线项目
- 专注处理“停产下架商品编码批量改为 `txcj`”这一条业务链路

主目录：

- `jushuitan-sku-offline-batch/`

## 主线与扩展线定义

- 当前主线：
  - `jushuitan-sku-offline-batch`
- 历史 / 扩展线：
  - `jushuitan-link-ops`

解释：

- `jushuitan-sku-offline-batch` 更聚焦、目标更明确，适合直接推进
- `jushuitan-link-ops` 保留为泛化版 / 扩展版参考，不作为当前第一主线

## 当前仓库规则

- 不提交本地私有配置
- 不提交日志、缓存、打包产物
- 不提交 `node_modules`、`dist`、`build`、`__pycache__`

## 建议接手顺序

1. 看各项目根目录 `README.md`
2. 看各项目 `docs/交接文档`
3. 看各项目 `docs/operations/`
4. 看各项目 `docs/roadmap/`
