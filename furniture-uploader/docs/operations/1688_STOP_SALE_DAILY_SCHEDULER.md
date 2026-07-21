# 1688 停产下架每日调度管理

## 目标

Windows 执行机每天 13:00 执行一次停产下架闭环：

1. 从 `JSDataMiddlePlatform.dbo.op_stop_sale` 读取当前业务日期、Alibaba、全渠道下架的四店数据。
2. 按店铺拆分并按最多 10 条的可恢复批次执行指定 SKU 下架。
3. 1688 成功或已下架后，继续执行聚水潭 1688 链接清除。
4. 单商品业务异常记录审计并发送钉钉，继续后续商品。
5. 登录失效、风控、店铺错配、浏览器窗口关闭等安全异常只停止当前店铺。
6. 执行前暂停 `YYDD-1688-Crawler-Worker`，执行后恢复原状态。

计划任务默认读取执行当天的指标日期，不自动补跑前几天。业务源当天通知会继续包含此前未成功的 SKU，系统只需保证当天输入在本次任务内充分重试和完整留痕。

脚本不会把最后一个在线 SKU 自动改成整商品下架。1688 平台拒绝的业务规则会进入异常终态并通知钉钉，不会阻塞其他店铺。

执行顺序按“店铺 -> 商品 ID -> SKU”组织。同一店铺、同一商品 ID 的多个目标 SKU 在 1688 只打开一次编辑页，依次切换目标 SKU 后统一提交一次；每个 SKU 仍独立写入审计。聚水潭复用当前店铺和商品查询页，同组 SKU 依次清除链接，不重复切换店铺或重新查询同一商品。

## 部署路径

以下命令以执行机目录为例：

```powershell
$ErpRoot = "D:\deploy\erp-stop-sale"
$ProjectRoot = "$ErpRoot\furniture-uploader"
$SharedRuntimeRoot = "E:\1688\1688-script-new"
$JushuitanRoot = "$ErpRoot\jushuitan-sku-offline-batch"
Set-Location $ProjectRoot
```

执行用户必须能够访问四个已登录的 1688 Profile，并已配置数据库、聚水潭和钉钉凭据。不要把密码、Cookie、Token 或 Webhook 写入项目文件或发送到日志。

## 安装每日任务

建议在源数据 12:00 更新后，于北京时间 13:00 执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\manage_1688_stop_sale_daily_task.ps1 `
  -Action install `
  -DailyAt 13:00 `
  -ProjectRoot $ProjectRoot `
  -SharedRuntimeRoot $SharedRuntimeRoot `
  -JushuitanRoot $JushuitanRoot `
  -BatchSize 10 `
  -BatchMaxAttempts 2 `
  -BatchRetryBackoffSeconds 60 `
  -StopSale1688TimeoutSeconds 3600 `
  -StopSaleJushuitanTimeoutSeconds 1800
```

任务名称默认为 `YYDD-1688-Stop-Sale-Daily`，使用当前 Windows 用户的交互式会话和最高权限。交互式会话是为了让浏览器 Profile 能正常工作；用户退出 Windows 后，计划任务不会伪造登录或绕过验证码。

## 指定日期人工执行

日常计划任务始终使用当天日期，不会自动续跑历史日期。只有业务明确要求复核某个指定日期时，才人工传入 `-BusinessDate`，并先预览再执行：

```powershell
$BusinessDate = "2026-07-21"

powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\manage_1688_stop_sale_daily_task.ps1 `
  -Action preview `
  -BusinessDate $BusinessDate `
  -ProjectRoot $ProjectRoot `
  -SharedRuntimeRoot $SharedRuntimeRoot `
  -JushuitanRoot $JushuitanRoot `
  -BatchSize 10

powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\manage_1688_stop_sale_daily_task.ps1 `
  -Action run `
  -BusinessDate $BusinessDate `
  -ProjectRoot $ProjectRoot `
  -SharedRuntimeRoot $SharedRuntimeRoot `
  -JushuitanRoot $JushuitanRoot `
  -BatchSize 10
```

`run` 使用 `--skip-login` 复用已登录 Profile，不会出现要求人工按回车的登录流程。若 Profile 登录态失效、出现滑块或风控，任务会记录并通知，不能由脚本自动处理验证码。

## 查看任务状态

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\manage_1688_stop_sale_daily_task.ps1 `
  -Action status `
  -TaskName "YYDD-1688-Stop-Sale-Daily" `
  -ProjectRoot $ProjectRoot
```

状态输出包括：

- `state`、`enabled`：计划任务当前状态。
- `last_run_time`、`last_task_result`：上次运行时间和 Windows 结果码。
- `next_run_time`：下一次计划运行时间。
- `latest_summary`：最近一批的业务日期、选中数量、每店批次、Worker 状态、异常状态和日志路径。

任务管理命令：

```powershell
# 暂停后续定时执行
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\manage_1688_stop_sale_daily_task.ps1 -Action disable

# 恢复定时执行
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\manage_1688_stop_sale_daily_task.ps1 -Action enable

# 立即启动已安装任务
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\manage_1688_stop_sale_daily_task.ps1 -Action start

# 停止当前任务进程
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\manage_1688_stop_sale_daily_task.ps1 -Action stop

# 卸载计划任务，不删除历史日志
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\manage_1688_stop_sale_daily_task.ps1 -Action uninstall
```

## 日志和审计

假设项目路径为 `D:\deploy\erp-stop-sale\furniture-uploader`：

| 内容 | 路径 |
| --- | --- |
| 最近一次管理汇总 | `logs\sku_offline\scheduler\latest.summary.json` |
| 某次管理运行目录 | `logs\sku_offline\scheduler\daily_<run_id>\` |
| 计划任务标准输出 | `logs\sku_offline\scheduler\scheduled_logs\<timestamp>.log` |
| 预检日志 | `daily_<run_id>\preflight.log` |
| 数据库取数日志 | `daily_<run_id>\preview.log` |
| 每店每批执行日志 | `daily_<run_id>\<store>_<run_id>.log` |
| 1688 逐 SKU 报告 | `logs\sku_offline\run_reports\<pipeline_run_id>.jsonl` |
| 聚水潭逐 SKU 报告 | `logs\sku_offline\pipelines\<pipeline_run_id>.jushuitan-results\<pipeline_run_id>.jsonl` |
| Pipeline 汇总 | `logs\sku_offline\pipelines\<pipeline_run_id>.summary.json` |
| 批次重试输入 | `daily_<run_id>\inputs\<store>\batch_<n>.retry_<attempt>.csv` |

日志中的 `product_id`、`online_sku`、店铺、状态、错误分类和证据路径用于运营核对；密码、Cookie、Token 和 Webhook 不会写入报告。

审计数据库为 `JSReportReplica` 的 `app` Schema：

```sql
SELECT TOP (100)
    run_id, mode, source_database, source_table, status, total_count,
    offline_success_count, offline_already_count,
    jushuitan_success_count, jushuitan_already_count,
    jushuitan_failed_count, started_at, finished_at
FROM app.ali1688_stop_sale_run
ORDER BY started_at DESC;

SELECT TOP (200)
    run_id, store_name, product_id, online_sku,
    platform_store_item_code, offline_status,
    error_category, error_message, jushuitan_status,
    jushuitan_category, jushuitan_message
FROM app.ali1688_stop_sale_item
ORDER BY updated_at DESC;
```

## 状态定义

| 状态 | 含义 | 是否继续其他任务 |
| --- | --- | --- |
| `success` | 本批所有 1688 和聚水潭结果成功或已完成 | 是 |
| `completed_with_exceptions` | 有商品业务异常，但审计和通知已完成 | 是 |
| `no_tasks` | 该业务日期没有待处理数据 | 不需要 |
| `preview_only` | 只取数和生成报告，没有线上操作 | 不适用 |
| `failed` | 预检、数据库、Worker、锁、进程或审计基础设施失败 | 按报告处理，不能假设完成 |

商品不存在、SKU 不存在、已下架、唯一在线 SKU、活动限制、页面校验阻止等属于逐项异常或业务终态，记录后发送钉钉。登录失效、风控、店铺错配和浏览器窗口关闭属于安全异常，只停止当前店铺批次；不会全局关闭浏览器，也不会自动重试验证码。

## 超时和重试

- 每个商品在 RPA 内部最多尝试 2 次。
- 每个外层批次最多尝试 2 次；第二次输入只包含尚未闭环的 SKU。
- 普通 `management_search_timeout` 可以重试；只有页面明确出现“暂无数据/没有找到商品”时才转为 `product_unavailable` 业务终态。
- 1688 已成功但聚水潭缺少结果或失败时，该 SKU 保留重试资格；重试时 1688 幂等检查会返回 `already_offline`，随后继续聚水潭。
- 唯一在线 SKU、活动限制、商品不存在、SKU 不存在和提交前业务校验等明确业务终态不重试，只审计并通知。
- 登录失效、风控、店铺错配、账号映射和浏览器窗口关闭属于安全异常，不在当前店铺内自动重试。
- 批次尝试耗尽后继续其他批次和店铺，最终管理状态为 `failed`，钉钉汇总包含耗尽批次数和对应 run ID。
- 不增加跨日期重试队列；下一业务日由当天源数据自然再次提供此前失败 SKU。

## 上线前检查

```powershell
python scripts\preflight_1688_stop_sale_executor.py `
  --script-1688-root $SharedRuntimeRoot `
  --jushuitan-root $JushuitanRoot
```

必须看到 `status: ok`，并确认 `all_profiles_exist`、`shared_lock_module`、`app_audit_tables_exist`、聚水潭登录态和钉钉凭据检查通过。真实执行前，先用 `-Action preview` 核对昨日选中数量和四店分布。
