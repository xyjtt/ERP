# 1688 下架中断日批收口与精确恢复

更新日期：2026-08-05

## 状态边界

- 本文对应开发分支 `codex/stop-sale-deploy-integration-20260804`。
- 开发代码和自动化测试已完成，但尚未连接生产、执行机、数据库或浏览器。
- `daily_20260804_130814_970163` 尚未生产收口。
- 先前只读证据中的 52 个未执行、7 个技术失败和历史 11 条 Outbox 只是待复核范围，不是本次开发已处理结果。
- 禁止手删 `ali1688_stop_sale_daily.lock`、广泛重跑、批量重排 Outbox、删除审计或业务数据。

## 工具职责

`scripts/recover_interrupted_stop_sale_daily_manager.py` 负责中断 Manager 收口：

1. 校验锁的 `cycle`、`manager_run_id` 和 token。
2. 确认锁 owner PID 已死亡。
3. 要求 artifacts 与数据库 child run 集合完全一致。
4. 要求每个 child run 已终态，数据库 `finished_at/status` 与 child Summary 一致。
5. 原子写入 Manager `summary.json`。
6. 再次读取并比对完整锁快照。
7. 仅在二次校验一致时释放锁并写 `latest.summary.json`。

`scripts/build_interrupted_stop_sale_recovery_manifest.py` 负责生成精确恢复范围：

- Manager Summary 必须是 `recovery_status=finalized` 且 `lock_removed=true`。
- 原始 Preview 的 `selected_count` 必须等于 CSV 行数，四字段身份不得重复。
- `missing.csv` 只包含没有 1688 执行证据的行。
- `technical.csv` 只包含登录、风控、身份、页面技术异常或聚水潭未终态行。
- `excluded` 包含业务终态，以及 1688 与聚水潭均已完成的行，不写入恢复 CSV。

## 部署前门禁

1. 执行机部署目录和目标分支已确认，工作树无未知改动。远端必须是
   `gitee=https://gitee.com/xyjtt/erp.git`，分支必须是
   `codex/stop-sale-deploy-integration-20260804`。部署必须先执行
   `git fetch gitee --prune`、`git checkout codex/stop-sale-deploy-integration-20260804`、
   `git merge --ff-only gitee/codex/stop-sale-deploy-integration-20260804`，随后要求
   `git rev-parse HEAD` 与 `git rev-parse gitee/codex/stop-sale-deploy-integration-20260804`
   完全一致。最终批准 SHA 以交付回报为准；HEAD 不同或工作树非干净时停止。
2. 新 commit 已完成 Python、TypeScript、编译、差异和密钥扫描。
3. `YYDD-1688-Stop-Sale-Daily` 没有新的 Manager 正在运行。
4. 锁文件仍存在且内容属于 `daily_20260804_130814_970163`；不得提前修改。
5. 锁中 PID 已死亡，且没有同 run id 的 Python/Node/Edge 子进程。
6. Credential Manager、`JSReportReplica/app` 和钉钉配置由正式任务用户可访问。
7. Crawler 队列无需清零；仅当相同账号租约或浏览器槽位冲突时等待对应当前任务自然结束。

## 部署后收口命令

以下命令会写 Manager Summary 并在全部校验通过后释放该 Manager 锁，只能在上述门禁全部满足后执行一次：

```powershell
Set-Location <ERP_DEPLOY_ROOT>\furniture-uploader
$ManagerRunId = "daily_20260804_130814_970163"
$ManagerDir = Join-Path (Get-Location) "logs\sku_offline\scheduler\$ManagerRunId"

python scripts\recover_interrupted_stop_sale_daily_manager.py `
  --manager-run-id $ManagerRunId `
  --manager-dir $ManagerDir `
  --shared-runtime-root E:\1688\1688-script-new `
  --reason "manager process interrupted after child execution; evidence reconciled" `
  --yes
```

验收输出必须满足：

- `manager_run_id` 精确匹配。
- `recovery_status=finalized`。
- `lock_removed=true`。
- `child_run_count` 与数据库及 artifacts 完全一致。
- `summary.json` 和 `latest.summary.json` 内容一致。

若输出 `blocked_lock_revalidation` 或任一 child 不一致，立即停止；锁必须保留，不得手工处理。

## 生成精确恢复清单

Manager 收口成功后执行：

```powershell
Set-Location <ERP_DEPLOY_ROOT>\furniture-uploader
$ManagerRunId = "daily_20260804_130814_970163"
$ManagerDir = Join-Path (Get-Location) "logs\sku_offline\scheduler\$ManagerRunId"
$RecoveryDir = Join-Path (Get-Location) "artifacts\stop_sale_recovery\$ManagerRunId"

python scripts\build_interrupted_stop_sale_recovery_manifest.py `
  --manager-run-id $ManagerRunId `
  --manager-dir $ManagerDir `
  --output-dir $RecoveryDir `
  --approved-scope-file <经审批的52_missing_7_technical身份清单.json> `
  --approved-scope-sha256 <批准文件的64位小写SHA-256>
```

必须人工和脚本共同核对 `manifest.json`：

- `selected_count` 与原始 Preview 一致。
- `classification_counts` 的总和等于 `selected_count`。
- `recovery_count = missing + technical`。
- 批准文件必须为 `version=1`，精确声明 `manager_run_id`、52 个
  `missing_identities`、7 个 `technical_identities` 和规范化身份集合 SHA-256。
- 当前分类必须与批准的 52/7 四字段身份集合及哈希完全一致。
- 任一数量、分类或身份漂移时只写 `diagnostic.json` 并返回 2，不得生成任何恢复 CSV 或 `manifest.json`。
- `excluded` 中不能出现尚未完成的聚水潭项。

## 定向恢复规则

- `missing.csv` 与 `technical.csv` 分开审批和执行，禁止直接把 `recovery.csv` 当作全量自动重跑入口。
- `system_prompt`、`sole_sku_requires_product_offline`、活动限制、商品/SKU 不存在等业务终态不得重跑。
- 1688 已完成但聚水潭未终态的项目只恢复 Outbox，不再重复修改 1688 页面。
- 登录或风控类技术项先恢复真实账号会话并复核店铺身份，再定向执行。
- 每次恢复后核对 1688 report、Jushuitan report、Saga、Outbox 和正式审计，不能仅看进程退出码。

## 历史 Outbox 单条恢复

历史 11 条必须逐条读取当前快照，并把快照值完整传给 CAS 工具。先 preview，不加 `--yes`：

```powershell
python scripts\requeue_1688_jushuitan_outbox.py `
  --operation-key <64位operation_key> `
  --expected-status <failed_retryable或failed_terminal> `
  --expected-error-code <当前error_code> `
  --expected-run-id <当前run_id> `
  --expected-attempt-count <当前attempt_count> `
  --expected-saga-state <当前saga_state> `
  --reason "verified cleanup recovery after deployment" `
  --shared-runtime-root E:\1688\1688-script-new
```

preview 的 `before` 与审批证据一致后，原命令追加 `--yes`。任一字段漂移都必须停止，不得调整参数去强行覆盖。

经批准的单条重排完成后，建立只包含本次批准 key 的版本 1 JSON：

```json
{"version":1,"run_id":"<目标run_id>","operation_keys":["<64位operation_key>"]}
```

计算文件 SHA-256 后运行独立 Outbox Worker：

```powershell
python scripts\run_1688_jushuitan_outbox_worker.py `
  --action cleanup `
  --run-id <目标run_id> `
  --approved-operation-keys-file <批准operation_key清单.json> `
  --approved-operation-keys-sha256 <批准文件的64位小写SHA-256> `
  --shared-runtime-root E:\1688\1688-script-new `
  --jushuitan-root ..\jushuitan-sku-offline-batch `
  --yes
```

Repository 在单一事务内校验所有批准 key 的 run_id、topic、状态和可领取性，再领取完整集合。
缺少、多出、被占用或状态漂移都会整体回滚；禁止退回 `run_id + limit` 模糊领取。

## 验收与回滚

- 收口验收：Manager Summary、锁状态、child runs 和通知一致。
- 清单验收：原始输入每行恰有一个分类，恢复 CSV 只含 `missing/technical`。
- Outbox 验收：每个 operation 形成 `success` 或有精确证据的 `already_cleared`；失败项保持可追溯终态。
- 真实页面验收：零行必须包含精确筛选值回读和显式空结果证据；不能用页面未加载代替空结果。
- 回滚代码只能部署上一批准 commit。不要通过重建锁、删除 Saga/Outbox 或清空审计回滚业务状态。

## 当前剩余风险

- 本地单测不能证明执行机锁、Credential Manager、真实页面或数据库状态仍与 2026-08-04 只读证据一致。
- Manager 收口会改变生产锁和 Summary，必须由执行机正式任务用户执行并保存输出。
- 历史 11 条 Outbox 的实时状态可能已漂移；必须逐条重新读取后再决定是否重排。
- `npm ci` 当前报告 5 个既有依赖漏洞；本次未执行破坏性依赖升级，不影响现有功能验证，但需单独治理。
