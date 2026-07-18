# 1688 停产下架 Windows 执行机部署

更新日期：2026-07-18

## Gitee 来源

- 仓库：`https://gitee.com/xyjtt/erp.git`
- 部署分支：`deploy/1688-stop-sale-windows-20260718`
- 代码目录：`furniture-uploader`、`jushuitan-sku-offline-batch`。
- 执行机已有项目：`D:\script_1688`。

不要提交或复制开发机的 Cookie、Token、`.local.json`、`.env`、浏览器 Profile、运行日志和截图。

执行机首次克隆：

```powershell
git clone --branch deploy/1688-stop-sale-windows-20260718 `
  https://gitee.com/xyjtt/erp.git `
  D:\deploy\erp-stop-sale
```

## 执行机 AI 接手顺序

1. 从 Gitee 克隆或拉取指定部署分支。
2. 阅读本文件和 `jushuitan-sku-offline-batch/docs/1688_LINK_CLEANUP.md`。
3. 安装 Python 依赖：`python -m pip install -r furniture-uploader/requirements.txt`。
4. 安装 Node 依赖：在 `jushuitan-sku-offline-batch` 执行 `npm ci`。
5. 将 4 店 `account_key` 映射到执行机 `D:\script_1688` 账号清单中的已有 Profile。
6. 把敏感配置写入执行任务用户的环境变量，不写项目文件。
7. 运行执行机 preflight。
8. 先跑 preview，再跑单店单条 execute。
9. 核对共享锁、运营群消息、1688 报告、聚水潭反查和幂等账本。

当前 `npm ci` 审计基线报告 5 项依赖告警（1 低、3 中、1 高）。部署测试阶段不得直接执行 `npm audit fix --force`，避免未经回归的破坏性升级；由执行机 AI 单独输出审计报告后再安排依赖治理。

## 环境变量

必须配置但不得输出具体值：

- `STOP_SALE_SQLSERVER_HOST`
- `STOP_SALE_SQLSERVER_USER`
- `STOP_SALE_SQLSERVER_PASSWORD`
- `JST_USERNAME`
- `JST_PASSWORD`
- `DINGTALK_WEBHOOK`
- `DINGTALK_SECRET`
- 可选：`STOP_SALE_SQLSERVER_PORT`、`STOP_SALE_SQLSERVER_DATABASE`、`SCRIPT_1688_ROOT`

## Profile 映射

在执行机创建 `furniture-uploader/config/systems/1688_sku_offline.local.json`，只覆盖 `execution.store_accounts` 中的 Profile 路径和必要账号键。路径必须指向执行机已登录的独立账号 Profile。

同一账号已登录时通常不需要重新登录。登录态失效或出现滑块、验证码、风控时，脚本停止对应店铺并发运营告警。

## 共享锁

真实 `execute` 默认获取：

`D:\script_1688\artifacts\locks\ali1688_full_cycle.lock`

现有爬虫必须经正式 orchestrator 启动。锁等待超时返回 `75`，下架浏览器不会启动，并向运营群发送延迟执行通知。

不要在共机生产环境使用 `--no-shared-lock`。

## Preflight

```powershell
cd <ERP仓库>\furniture-uploader
python scripts\preflight_1688_stop_sale_executor.py `
  --script-1688-root D:\script_1688 `
  --jushuitan-root ..\jushuitan-sku-offline-batch
```

Preflight 只输出环境变量是否已设置，不输出账号、密码、Webhook 或 Token。

## 首轮测试

1. 数据库 preview：

```powershell
python scripts\build_1688_stop_sale_preview.py --date <当天日期> --limit 1
```

2. 使用生成的单店 CSV 运行完整 preview：

```powershell
python scripts\run_1688_stop_sale_pipeline.py --mode preview --file <单店CSV>
```

3. 确认店铺、商品 ID、SKU 和平台店铺商品编码后，运行单条 execute：

```powershell
python scripts\run_1688_stop_sale_pipeline.py `
  --mode execute `
  --yes `
  --limit 1 `
  --file <单店CSV> `
  --shared-runtime-root D:\script_1688
```

首轮通过后再安装 Windows 任务计划，不能在部署当天直接启动全量。

## 运营通知

钉钉群机器人发送：批次总数、分店成功/已完成/异常数量，以及异常店铺、商品 ID、SKU 和中文原因。群消息不含本机路径、技术堆栈或敏感信息。
