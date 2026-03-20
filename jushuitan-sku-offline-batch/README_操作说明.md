# 聚水潭停产下架商品编码批量修改 操作说明

## 1. 程序用途

本程序用于：

- 读取 Excel 数据源
- 筛选出当天需要执行“下架”的商品编码
- 登录聚水潭
- 进入 `店铺商品管理`
- 切换到 `按SKU`
- 按平台批量查询 `线上商品编码`
- 将命中的 `线上商品编码` 批量修改为 `txcj`
- 记录每批次提示信息
- 再次复查仍能查询到的数据
- 生成结果文件，方便发到对应群

## 2. 使用前准备

请先准备好以下内容：

- 聚水潭账号和密码
- 聚水潭登录地址
- 聚水潭店铺商品管理页面地址
- Excel 源文件
- 本机已安装 Node.js
- 本机已安装 Microsoft Edge 或 Google Chrome

当前建议优先使用 `Edge`。

原因：

- 这台机器上 `Playwright + Chrome` 重启后出现过浏览器进程自动退出
- `Edge` 当前执行更稳定

## 3. 文件位置

项目目录：

`C:\Users\Administrator\Documents\Playground\jushuitan-sku-offline-batch`

重点文件：

- `run.bat`
  作用：双击启动程序，兼容性最好
- `run.ps1`
  作用：PowerShell 启动脚本；如果 `run.bat` 被系统编码影响，可改用它
- `.env`
  作用：保存账号、密码、页面地址、Excel 路径等配置
- `.env.example`
  作用：配置模板
- `README_操作说明.md`
  作用：本说明文件

## 4. Excel 放置要求

默认 Excel 路径：

`.\data\source.xlsx`

你也可以在 `.env` 里改成自己的实际路径。

Excel 筛选规则如下：

- `商品编码`：有值
- `统计日期新`：等于执行当天日期
- `处理说明`：等于 `下架`
- `下架备注`：不包含 `京喜需下架`

程序会自动读取符合条件的 `商品编码` 作为待处理编码。

## 5. .env 配置说明

请先把 `.env.example` 复制成 `.env`，然后填写真实信息。

示例：

```ini
JST_LOGIN_URL=https://www.erp321.com/epaas
JST_PRODUCT_URL=https://ww.erp321.com
JST_USERNAME=你的账号
JST_PASSWORD=你的密码
EXCEL_PATH=./data/source.xlsx
EXCEL_SHEET_NAME=
TARGET_DATE=2026-03-18
TARGET_PLATFORMS=douyin,kuaishou,pinduoduo,taobao,tmall,xiaohongshu
REPLACEMENT_ONLINE_SKU=txcj
BATCH_SIZE=50
HEADLESS=false
BROWSER_CHANNEL=msedge
BROWSER_EXECUTABLE_PATH=
SLOW_MO=200
LOGIN_WAIT_MS=60000
SEARCH_WAIT_MS=8000
MANUAL_LOGIN_TIMEOUT_MS=180000
KEEP_BROWSER_OPEN_ON_ERROR=false
SAVE_STORAGE_STATE=true
STORAGE_STATE_PATH=./storage/jushuitan.json
```

重点说明：

- `JST_LOGIN_URL`
  登录地址，通常是 `https://www.erp321.com/epaas`
- `JST_PRODUCT_URL`
  店铺商品管理页地址
  注意：聚水潭登录后有时会变成 `https://ww.erp321.com`
- `TARGET_DATE`
  执行日期，格式必须是 `yyyy-MM-dd`
- `TARGET_PLATFORMS`
  支持平台：
  `douyin,kuaishou,pinduoduo,taobao,tmall,xiaohongshu`
- `REPLACEMENT_ONLINE_SKU`
  固定改成 `txcj`
- `BROWSER_CHANNEL`
  当前建议填 `msedge`
- `KEEP_BROWSER_OPEN_ON_ERROR`
  如果填 `true`，失败时浏览器会保留现场，方便排查

## 6. 如何运行

### 方式一：双击运行

直接双击：

`run.bat`

程序会自动：

- 检查 `.env`
- 检查依赖
- 执行批量处理

如果 `run.bat` 无法正常启动：

1. 右键 `run.ps1`
2. 选择“使用 PowerShell 运行”

### 方式二：终端运行

在项目目录打开终端后执行：

```powershell
npm run start
```

## 7. 运行过程中会做什么

程序会按以下顺序执行：

1. 读取 Excel
2. 筛选符合条件的商品编码
3. 登录聚水潭
4. 进入 `店铺商品管理`
5. 切换到 `按SKU`
6. 选择平台
7. 按批次输入 `多个线上商品编码`
8. 点击 `搜索`
9. 如果有结果，则进入 `批量更新线上商品编码`
10. 选择 `批量填充`
11. 输入 `txcj`
12. 点击 `确定`
13. 记录弹窗提示信息
14. 再次复查剩余可查询数据
15. 生成结果文件

## 8. 结果文件在哪里

程序执行完成后，会在 `results` 目录下生成一个时间戳文件夹。

例如：

`results\20260319-132438`

每个平台下通常会生成：

- `update-popups.xlsx`
  含义：每批修改后的提示信息
- `remaining-rows.xlsx`
  含义：复查后仍能查询到的数据
- `summary.txt`
  含义：本平台执行摘要
- `*-group-message.txt`
  含义：可直接发群的文字内容

运行截图会保存在：

`artifacts\时间戳目录`

## 9. 需要发到群里的文件

通常发以下内容：

- `*-group-message.txt` 里的文字
- 如有需要，附带 `remaining-rows.xlsx`

如果 `remaining-rows.xlsx` 是空或只剩“暂无店铺商品”等提示，可按实际情况决定是否发。

## 10. 常见问题

### 10.1 登录后地址变化

这是正常情况。

聚水潭登录后可能从：

`https://www.erp321.com/epaas`

变成：

`https://ww.erp321.com`

程序已按这个情况兼容处理。

### 10.2 页面有弹窗、引导、订购提醒

程序会尽量自动关闭。

如果仍卡住：

- 把 `.env` 中 `KEEP_BROWSER_OPEN_ON_ERROR=true`
- 重新运行
- 失败时浏览器会保留现场，方便继续修正

### 10.3 Chrome 跑不起来

如果 `Chrome` 无法稳定启动，请改用：

```ini
BROWSER_CHANNEL=msedge
```

当前这台机器建议使用 `Edge`。

### 10.4 Excel 没有数据

如果日期不对，或者当天没有符合筛选条件的数据，程序会提示没有满足条件的数据。

这时请检查：

- `TARGET_DATE`
- Excel 中 `统计日期新`
- `处理说明`
- `下架备注`

### 10.5 想只测试少量编码

可以临时加一个环境变量：

```powershell
$env:MAX_PRODUCT_CODES='5'
npm run start
```

这样只会拿前 5 个商品编码做调试。

## 11. 建议操作方式

正式执行建议：

- 先小批量测试 5 个编码
- 确认页面结构无变化
- 再执行全量

如果要保留失败现场：

```ini
KEEP_BROWSER_OPEN_ON_ERROR=true
```

如果要正式后台长跑：

- 建议使用 `msedge`
- 不要手动关闭自动化打开的浏览器

## 12. 当前状态说明

当前项目已经完成：

- Excel 筛选
- 自动登录
- 进入店铺商品管理
- 切换 `按SKU`
- 填写 `线上商品编码`
- 选择平台
- 批次查询
- 结果文件落盘

目前仍建议在正式全量前，先跑一小批确认目标平台确实存在可修改数据。
