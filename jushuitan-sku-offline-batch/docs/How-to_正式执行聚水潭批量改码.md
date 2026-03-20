# How-to: 正式执行聚水潭批量改码

本指南用于在已经理解项目基本结构的前提下，执行一次正式任务。

## 场景 1：按默认配置正式执行

如果 `.env` 已经配置完成，直接运行：

```powershell
cd C:\Users\Administrator\Documents\Playground\jushuitan-sku-offline-batch
.\run.ps1
```

或双击：

- `run.bat`

## 场景 2：临时指定 Excel 再执行

```powershell
cd C:\Users\Administrator\Documents\Playground\jushuitan-sku-offline-batch
$env:EXCEL_PATH='D:\工作平台2.0\AI项目需求文档\需下架商品20260319.xlsx'
npm run start
```

## 场景 3：先做单平台验证

```powershell
cd C:\Users\Administrator\Documents\Playground\jushuitan-sku-offline-batch
$env:TARGET_PLATFORMS='taobao'
$env:MAX_PRODUCT_CODES='50'
npm run start
```

## 场景 4：保留失败现场做排障

```powershell
cd C:\Users\Administrator\Documents\Playground\jushuitan-sku-offline-batch
$env:KEEP_BROWSER_OPEN_ON_ERROR='true'
npm run start
```

说明：

- 失败时浏览器会保留现场
- 适合人工查看真实页面状态
- 不适合后台长时间无人值守执行

## 正式执行前检查项

- `.env` 中账号密码正确
- `EXCEL_PATH` 指向有效文件
- `TARGET_DATE` 符合业务日期
- `TARGET_PLATFORMS` 符合本次执行范围
- `BROWSER_CHANNEL` 对应浏览器已安装
- `KEEP_BROWSER_OPEN_ON_ERROR=false`

## 正式执行后检查项

检查以下文件：

- `results/<runId>/<platform>/summary.txt`
- `results/<runId>/<platform>/update-popups.xlsx`
- `results/<runId>/<platform>/remaining-rows.xlsx`
- `results/<runId>/<platform>/<platform>-group-message.txt`

## 如何判断是否执行成功

成功判断标准：

- 程序正常退出
- `summary.txt` 已生成
- `update-popups.xlsx` 有记录
- 若复查无剩余数据，则 `remaining-rows.xlsx` 应为空数据结果

## 如何处理常见异常

### 平台弹窗打不开

处理方式：

- 检查当前页面是否还在正确查询区域
- 检查登录态是否过期
- 检查 `storage/jushuitan.json`
- 必要时开启 `KEEP_BROWSER_OPEN_ON_ERROR=true` 重跑

### 结果表未勾选

处理方式：

- 查看 `after-select.json`
- 确认是否出现：
  - `.ant-checkbox-checked`
  - `已勾选X条`

### 全部显示无结果

处理方式：

- 先确认 Excel 中筛选出的编码是否真实存在于当前平台
- 再确认平台选择是否正确
- 最后检查页面是否切换到了 `按SKU`

