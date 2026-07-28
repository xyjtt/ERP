# 1688 停产下架 Windows 执行机部署

更新日期：2026-07-18

## Gitee 来源

- 仓库：`https://gitee.com/xyjtt/erp.git`
- 部署分支：`deploy/1688-stop-sale-windows-20260718`
- 代码目录：`furniture-uploader`、`jushuitan-sku-offline-batch`。
- 执行机 1688 正式项目：`E:\1688\1688-script-new`。
- `D:\script_1688` 只是历史日志目录，不能作为共享运行时根目录。

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
5. 将 4 店 `account_key` 映射到 `C:\ProgramData\YYDD\1688-crawler\profiles` 中的已有 Profile。
6. 把敏感配置写入执行任务用户的环境变量，不写项目文件。
7. 验证并应用 `sql/360_ali1688_stop_sale_audit.sql`。
8. 运行执行机 preflight。
9. 先跑 preview，再跑单店单条 execute。
10. 核对共享锁、运营群消息、1688/JST 报告和新库逐项审计。

当前 `npm ci` 审计基线报告 5 项依赖告警（1 低、3 中、1 高）。部署测试阶段不得直接执行 `npm audit fix --force`，避免未经回归的破坏性升级；由执行机 AI 单独输出审计报告后再安排依赖治理。

## 双数据库边界

| 用途 | 正式位置 | 规则 |
| --- | --- | --- |
| 停产业务来源 | `JSDataMiddlePlatform.dbo.op_stop_sale` | 只读；数据未迁移且不会迁入 `JSDataWarehouse` |
| AI 执行审计 | `JSReportReplica/app` | 只保存批次、逐 SKU 状态和证据索引 |

preview 只读取旧业务源，不写任何数据库。execute 在打开浏览器前必须连通新库并创建批次记录；新库审计不可用时禁止线上操作。

## 环境变量

必须配置但不得输出具体值：

- `STOP_SALE_SOURCE_SQLSERVER_HOST`
- `STOP_SALE_SOURCE_SQLSERVER_USER`
- `STOP_SALE_SOURCE_SQLSERVER_PASSWORD`
- `JST_USERNAME`
- `JST_PASSWORD`
- `DINGTALK_WEBHOOK`
- `DINGTALK_SECRET`
- 可选：`STOP_SALE_SOURCE_SQLSERVER_PORT`、`STOP_SALE_SOURCE_SQLSERVER_DATABASE`、`SCRIPT_1688_ROOT`

旧名称 `STOP_SALE_SQLSERVER_*` 暂时兼容，但新部署统一使用 `STOP_SALE_SOURCE_SQLSERVER_*`，明确它们只用于旧业务源读取。

新库默认复用执行机 1688 外置配置和 Windows Credential Manager 引用 `YYDD/1688/database/app-writer`。仅在无法复用时才使用 `STOP_SALE_APP_SQLSERVER_*` 环境变量；密码不得写入 `.env`。

钉钉优先复用 1688 项目 Credential Manager 中的 `YYDD/1688/notification/dingtalk/webhook` 和 `YYDD/1688/notification/dingtalk/secret`。只有这两个引用不存在时才要求 `DINGTALK_*` 环境变量。

聚水潭清链子进程由 pipeline 注入正式登录页、商品管理页和当前 JSONL handoff 路径；不需要人工配置 `JST_LOGIN_URL`、`JST_PRODUCT_URL` 或遗留的 `EXCEL_PATH`。执行机只需提供 `JST_USERNAME`、`JST_PASSWORD`。

## Profile 映射

在执行机创建 `furniture-uploader/config/systems/1688_sku_offline.local.json`，只覆盖 `execution.store_accounts` 中的 Profile 路径和必要账号键。路径必须指向执行机已登录的独立账号 Profile。

同一账号已登录时优先复用 Profile。登录态失效时，执行器会释放 ERP 浏览器并调用共享 1688 项目的账号级登录一次；仅已识别滑块允许受限自动处理最多 4 次。随后重新打开管理页并核对 `expected_member_id` 和店铺；未知验证和技术异常记录告警，真实身份不匹配才停止对应店铺。

## 共享锁

真实 `execute` 默认获取：

`E:\1688\1688-script-new\artifacts\locks\ali1688_full_cycle.lock`

现有爬虫必须经正式 orchestrator 启动。锁等待超时返回 `75`，下架浏览器不会启动，并向运营群发送延迟执行通知。

不要在共机生产环境使用 `--no-shared-lock`。

## Preflight

```powershell
cd <ERP仓库>\furniture-uploader
python scripts\preflight_1688_stop_sale_executor.py `
  --script-1688-root E:\1688\1688-script-new `
  --jushuitan-root ..\jushuitan-sku-offline-batch
```

Preflight 只输出环境变量是否已设置，并只读验证 `JSReportReplica/app` 审计表；不输出账号、密码、Webhook 或 Token。

## 新库 DDL

```powershell
# 默认执行完整 DDL 后回滚
python scripts\apply_1688_stop_sale_audit_ddl.py `
  --shared-runtime-root E:\1688\1688-script-new

# 验证通过后显式提交
python scripts\apply_1688_stop_sale_audit_ddl.py `
  --shared-runtime-root E:\1688\1688-script-new `
  --apply
```

正式对象：

- `app.ali1688_stop_sale_run`：每次 execute 一个批次。
- `app.ali1688_stop_sale_item`：每个商品 ID + SKU + 平台商品编码的逐项结果。

## 首轮测试

1. 数据库 preview：

```powershell
python scripts\build_1688_stop_sale_preview.py --date <当天日期> --limit 1
```

2. 使用生成的单店 CSV 运行完整 preview：

```powershell
python scripts\run_1688_stop_sale_pipeline.py --mode preview --file <单店CSV>
```

使用已有 CSV 做受控重试时，preflight 可跳过当前不参与执行的旧源数据库读取检查：

```powershell
python scripts\preflight_1688_stop_sale_executor.py `
  --existing-input `
  --script-1688-root E:\1688\1688-script-new
```

该参数只豁免旧源读取检查，不豁免新库审计、共享锁、Profile、聚水潭认证或钉钉检查。聚水潭可复用 `storage/jushuitan.json` 中的已有登录态；登录态失效且未配置 `JST_USERNAME/JST_PASSWORD` 时，execute 必须以 `login_required` 停止整个聚水潭会话。

3. 确认店铺、商品 ID、SKU 和平台店铺商品编码后，运行单条 execute：

```powershell
python scripts\run_1688_stop_sale_pipeline.py `
  --mode execute `
  --yes `
  --limit 1 `
  --file <单店CSV> `
  --shared-runtime-root E:\1688\1688-script-new `
  --1688-timeout-seconds 900 `
  --jushuitan-timeout-seconds 600
```

execute 使用统一 `run_id` 关联 1688 JSONL、聚水潭 JSONL 和新库两张审计表。preview 不创建新库记录。

真实执行期间 pipeline 每 30 秒刷新 `app.ali1688_stop_sale_run.updated_at`。跨机器门禁按最近心跳判断活跃批次；单次心跳遇到瞬时数据库通信错误时会有限重试，连续失败后当前 pipeline 才会终止自己启动的子进程树并记录异常，避免线上操作脱离正式审计。

单条 canary 建议使用上面的 15 分钟/10 分钟超时。按店铺执行 10 条时默认上限分别为 45 分钟和 20 分钟。超时只终止该 pipeline 子进程拥有的 PID 进程树，写入 `PipelineStageTimeoutError`、审计和钉钉，不允许使用全局关闭 Edge 的命令。

补偿执行必须只包含未完成项。已经完成 1688 与聚水潭闭环的商品不得重新加入补偿 CSV；1688 已完成但聚水潭失败的任务允许通过完整 pipeline 幂等重放，预期结果为 `already_offline + success/already_cleared`。

正式 pipeline 默认复用店铺映射中的已登录 Profile，相当于 `--skip-login`。登录态失效时每店自动调用一次共享运行时的账号级登录；该显式恢复允许既有滑块 RPA 最多 4 次，成功后重建浏览器并复核 `expected_member_id`、当前店铺、商品 ID 和 SKU 身份。未解决滑块或其他风控写审计并通知，只有真实身份不匹配停止店铺。只有人工调试时才显式使用 `--require-manual-login` 恢复旧的回车确认流程。执行摘要会记录 `auto_login_attempts`、`auto_login_success`、`auto_login_failed`。

自动登录调用约束：

```powershell
python rpa\sku_offline_main.py `
  --mode execute `
  --file <单店CSV> `
  --yes `
  --shared-runtime-root E:\1688\1688-script-new
```

执行器只把 `account_key`、期望店铺名和非敏感恢复开关传给 `src.cli login`，密码仍由共享项目从 Credential Manager 读取。preflight 还要求四店 `expected_member_id` 已配置。真实交付前必须在执行机完成一次过期 Profile canary；本地测试通过不等于线上登录验收通过。

## 共机 Worker 门禁

`YYDD-1688-Crawler-Worker` 不使用全局锁。真实下架前必须先确认 `app.crawler_task` 中没有 `claimed/preflight/running/persisted/validating` 的 task/variant，再暂停计划任务：

```powershell
Stop-ScheduledTask -TaskName "YYDD-1688-Crawler-Worker"

Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains("run_crawler_task_worker.py")
  } |
  Select-Object ProcessId, ParentProcessId
```

进程查询必须为空。pipeline 会再次检查计划任务状态、残留 Worker 进程和新库活动任务；任一不满足都会在打开浏览器前拒绝 execute。执行完成后恢复：

```powershell
Start-ScheduledTask -TaskName "YYDD-1688-Crawler-Worker"
```

首轮通过后再安装 Windows 任务计划，不能在部署当天直接启动全量。

## 运营通知

钉钉群机器人发送：批次总数、分店成功/已完成/异常数量，以及异常店铺、商品 ID、SKU 和中文原因。群消息不含本机路径、技术堆栈或敏感信息。
