# Tutorial: 运行一次淘宝 50 条真实回归

本教程用于帮助第一次接手该项目的人，完成一次最小可验证的真实执行。

目标结果：

- 成功读取 Excel
- 成功进入聚水潭 `店铺商品管理 -> 按SKU`
- 只对 `taobao` 平台运行
- 只取前 `50` 个商品编码
- 在 `results` 和 `artifacts` 中看到本次执行产物

## 开始前准备

请确认：

- 已安装 Node.js
- 本机可正常打开聚水潭
- `.env` 已配置账号密码
- Excel 文件路径有效
- Chrome 或 Edge 可用

## 步骤 1：进入项目目录

```powershell
cd C:\Users\Administrator\Documents\Playground\jushuitan-sku-offline-batch
```

## 步骤 2：设置本次回归参数

```powershell
$env:TARGET_PLATFORMS='taobao'
$env:MAX_PRODUCT_CODES='50'
$env:KEEP_BROWSER_OPEN_ON_ERROR='false'
```

如果想显式指定 Excel：

```powershell
$env:EXCEL_PATH='D:\工作平台2.0\AI项目需求文档\需下架商品20260319.xlsx'
```

## 步骤 3：启动任务

```powershell
npm run start
```

## 步骤 4：检查执行结果

执行成功后，终端会输出：

```text
执行完成，结果目录: C:\...\results\<runId>
```

重点检查：

- `results/<runId>/taobao/summary.txt`
- `results/<runId>/taobao/update-popups.xlsx`
- `results/<runId>/taobao/remaining-rows.xlsx`
- `results/<runId>/taobao/taobao-group-message.txt`
- `artifacts/<runId>/`

## 预期现象

如果本批 50 条里存在可处理数据：

- 会生成 `results.html/json`
- 会生成 `after-select.html/json`
- 批量更新后会有批次截图

如果本批 50 条没有命中：

- 会出现 `taobao-batch-1-no-results.png`
- `update-popups.xlsx` 中会记录 `No matched rows found for this batch`

## 本项目已验证的真实样例

可参考：

- [results/20260319-213958/taobao/summary.txt](/C:/Users/Administrator/Documents/Playground/jushuitan-sku-offline-batch/results/20260319-213958/taobao/summary.txt)
- [results/20260319-214252/taobao/summary.txt](/C:/Users/Administrator/Documents/Playground/jushuitan-sku-offline-batch/results/20260319-214252/taobao/summary.txt)

## 如果执行失败

优先看：

- `artifacts/<runId>/fatal.png`
- `artifacts/<runId>/*.json`

常见原因：

- 平台无命中数据
- 页面 DOM 变化
- 平台选择弹窗异常
- 登录态失效

