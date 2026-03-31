# ERP Project Positioning

更新日期：2026-03-20

## 当前项目分工

### 1. `jushuitan-sku-offline-batch`

定位：

- 当前聚水潭批量更新商品编码主线项目

原因：

- 业务目标单一明确
- 当前问题和阻塞点已知
- 更适合继续直接推进

### 2. `jushuitan-link-ops`

定位：

- 历史 / 扩展线项目

原因：

- 结构更泛化
- 目标更宽
- 适合保留为后续重构参考

### 3. `furniture-uploader`

定位：

- 家具铺货自动化项目

原因：

- 与聚水潭编码修改不是同一条业务线
- 但同属于 ERP / 电商运营自动化范围

## 当前建议

- 推进聚水潭编码修改需求时，以 `jushuitan-sku-offline-batch` 为主
- 需要抽象平台化能力时，再参考 `jushuitan-link-ops`
- `furniture-uploader` 独立维护，不和聚水潭编码修改线混在一起

## 路径约定

- ERP 正式仓库路径：`D:\script_files\ERP`
- `jushuitan-sku-offline-batch` 正式路径：`D:\script_files\ERP\jushuitan-sku-offline-batch`
- 历史上若存在 `C:\Users\Administrator\Documents\Playground\...` 副本，统一视为旧工作副本，不再作为后续迭代真源
