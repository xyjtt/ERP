# Reference: 配置与输出说明

## 环境变量

### 聚水潭访问

- `JST_LOGIN_URL`
  登录地址
- `JST_PRODUCT_URL`
  店铺商品管理页地址
- `JST_USERNAME`
  登录账号
- `JST_PASSWORD`
  登录密码

### Excel

- `EXCEL_PATH`
  Excel 文件路径
- `EXCEL_SHEET_NAME`
  工作表名称，可为空
- `TARGET_DATE`
  目标执行日期，格式 `yyyy-MM-dd`

### 执行范围

- `TARGET_PLATFORMS`
  平台列表，逗号分隔
- `BATCH_SIZE`
  每批商品编码数
- `MAX_PRODUCT_CODES`
  仅调试时使用，限制取前 N 个商品编码

### 执行行为

- `REPLACEMENT_ONLINE_SKU`
  默认固定为 `txcj`
- `HEADLESS`
  是否无头运行
- `BROWSER_CHANNEL`
  `chrome` 或 `msedge`
- `BROWSER_EXECUTABLE_PATH`
  自定义浏览器路径
- `SLOW_MO`
  Playwright 慢动作毫秒数

### 超时与调试

- `LOGIN_WAIT_MS`
  登录等待时长
- `SEARCH_WAIT_MS`
  搜索后等待时长
- `MANUAL_LOGIN_TIMEOUT_MS`
  手动登录等待时长
- `KEEP_BROWSER_OPEN_ON_ERROR`
  报错后是否保留浏览器

### 登录态

- `SAVE_STORAGE_STATE`
  是否保存登录态
- `STORAGE_STATE_PATH`
  登录态文件路径

## 支持平台

当前支持：

- `douyin`
- `kuaishou`
- `pinduoduo`
- `taobao`
- `tmall`
- `xiaohongshu`

## 默认输出目录

- 结果目录：
  `results/<runId>/`
- 调试目录：
  `artifacts/<runId>/`
- 日志目录：
  `logs/`

## 结果文件说明

### `summary.txt`

包含：

- 平台名
- 计划处理商品编码数
- 批量更新提示记录数
- 复查剩余行数

### `update-popups.xlsx`

包含：

- 平台
- 批次序号
- 商品编码
- 弹窗提示
- 记录时间

### `remaining-rows.xlsx`

包含复查后仍能查询到的真实数据。

已过滤：

- `暂无数据`
- `无数据`
- `暂无店铺商品`
- 占位空行

### `<platform>-group-message.txt`

供业务方直接发群使用的简版文本。

## 调试产物说明

### `*-results.html/json`

搜索结果页的原始快照。

### `*-results-500.html/json`

切换到 `500条/页` 后的快照。

### `*-after-select.html/json`

勾选后的快照，用于核对勾选是否真实生效。

### `fatal.png`

任务异常中断时的页面截图。

### `*-no-results.png`

该批次无命中结果时的页面截图。

## 当前已验证样例

- [results/20260319-214252/taobao](/C:/Users/Administrator/Documents/Playground/jushuitan-sku-offline-batch/results/20260319-214252/taobao)
- [artifacts/20260319-213542](/C:/Users/Administrator/Documents/Playground/jushuitan-sku-offline-batch/artifacts/20260319-213542)

