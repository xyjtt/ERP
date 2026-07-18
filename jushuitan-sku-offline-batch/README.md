# 聚水潭停产下架商品编码批量修改

本项目包含两条彼此隔离的聚水潭链路：

- 历史批量改码：按平台把线上商品编码改为 `txcj`。
- 1688 清除链接：1688 SKU 已下架后，精确执行“更新/清除链接 -> 清除链接”。

两条链路不能混用。1688 清除链接说明见 [docs/1688_LINK_CLEANUP.md](./docs/1688_LINK_CLEANUP.md)。

## 功能范围

- 读取 Excel 模板并按以下条件筛选数据：
  - `商品编码` 有值
  - `统计日期新` 等于执行日期
  - `处理说明` 等于 `下架`
  - `下架备注` 不包含 `京喜需下架`
- 登录聚水潭
- 进入店铺商品管理页面
- 切换到 `按sku`
- 把筛选到的商品编码按批次查询
- 按平台执行批量更新线上商品编码
- 把线上商品编码统一改成 `txcj`
- 记录弹窗提示信息
- 复查仍可查询到的数据
- 导出每个平台的 Excel 结果和群消息文本

## 项目结构

```text
src/
  config.ts        配置读取
  excel.ts         Excel 筛选逻辑
  jushuitan.ts     Playwright 自动化流程
  report.ts        结果导出
  selectors.ts     页面文本与选择器集中配置
  utils.ts         通用工具
```

## 安装

```bash
npm install
npx playwright install chromium
```

## 配置

1. 复制 `.env.example` 为 `.env`
2. 填写非敏感配置；账号密码使用进程或 Windows 用户环境变量 `JST_USERNAME`、`JST_PASSWORD`：

```ini
JST_LOGIN_URL=聚水潭登录页地址
JST_PRODUCT_URL=店铺商品管理页地址
EXCEL_PATH=./data/source.xlsx
EXCEL_SHEET_NAME=
TARGET_DATE=2026-03-18
TARGET_PLATFORMS=douyin,kuaishou,pinduoduo,taobao,tmall,xiaohongshu
REPLACEMENT_ONLINE_SKU=txcj
BATCH_SIZE=50
HEADLESS=false
BROWSER_CHANNEL=chrome
BROWSER_EXECUTABLE_PATH=
SLOW_MO=200
LOGIN_WAIT_MS=60000
SEARCH_WAIT_MS=8000
MANUAL_LOGIN_TIMEOUT_MS=180000
KEEP_BROWSER_OPEN_ON_ERROR=true
SAVE_STORAGE_STATE=true
STORAGE_STATE_PATH=./storage/jushuitan.json
```

## 运行

```bash
npm run start
```

1688 清除链接默认只预览：

```bash
npm run cleanup:1688 -- --mode preview --file <handoff.jsonl>
```

真实清除必须显式使用 `--mode execute --yes`。首次接入先运行 `probe --limit 1`。

## 输出结果

脚本会在 `results/时间戳/平台/` 下输出：

- `update-popups.xlsx`：每批操作后的弹窗提示
- `remaining-rows.xlsx`：复查后仍可查询到的数据
- `summary.txt`：平台执行摘要
- `*-group-message.txt`：可直接发群的文本

截图会保存在 `artifacts/时间戳/`。

## 注意事项

- 当前项目使用了基于中文文本和常见组件结构的选择器，第一次接真实页面时，大概率需要根据聚水潭页面 DOM 微调 [src/selectors.ts](./src/selectors.ts)。
- Excel 数据里没有平台列，所以脚本默认会对 `.env` 中配置的所有平台分别执行一遍。
- 如果你希望首次登录后复用会话，可以保留 `STORAGE_STATE_PATH` 输出的状态文件，后续可以扩展成免登录流程。
- 旧批量改码流程可保留人工登录等待；1688 清除链接不会等待人工处理验证码、短信或风控，而是停止并记录失败。
- 如果 `npx playwright install chromium` 下载很慢或失败，可以直接配置 `BROWSER_CHANNEL=chrome` 或 `BROWSER_CHANNEL=msedge`，复用本机已安装浏览器。
