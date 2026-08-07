# 1688 SKU 替换运行手册

更新时间：2026-08-08

## 业务规则

- 数据筛选：`平台=Alibaba`、`处理说明=全渠道替换`。
- 旧货号：`线上商品编码`。
- 新货号：`可替换商品编码（新）`，兼容读取旧表头 `可替换商品编码`。
- `可替换商品编码（新）` 去除前后空格后等于 `运营自行组合替换` 时，按业务跳过，异常原因固定为 `组合货号`；明细进入独立 `business_skipped` CSV/JSON。
- 其他非 SKU 值、缺字段、旧新相同、冲突映射和链式映射进入 rejected CSV/JSON；合法任务继续生成。
- 1688 商品搜索必须使用“全部”Tab。
- 同一店铺、同一商品 ID 的多个 SKU 在同一编辑页修改并一次提交。
- 1688 成功或 `already_replaced` 后，聚水潭执行“手动同步商品 -> 按链接同步 -> 立即下载”。
- 任一系统异常均写日志并发送钉钉；只有真实店铺或 `member_id` 不匹配停止对应店铺。

## Preview

首次部署先应用正式审计表：

```powershell
python scripts\apply_1688_sku_replace_audit_ddl.py `
  --shared-runtime-root E:\1688\1688-script-new `
  --apply
```

从正式数据源生成替换输入：

```powershell
python scripts\build_1688_stop_sale_preview.py `
  --date 2026-07-23 `
  --handling "全渠道替换" `
  --database JSReportReplica `
  --table app.op_stop_sale `
  --driver "ODBC Driver 17 for SQL Server" `
  --shared-runtime-root E:\1688\1688-script-new
```

`2026-07-23` 正式源历史只读结果：加载 568 条；42 条 `运营自行组合替换` 现应归类为 `business_skipped / 组合货号`；其余 526 条去除 26 条重复后，生成 500 条可执行 preview。合法任务分布为乐畅 6、工莱 400、沃来 4、淘淘 90。该结果不代表已执行线上替换，新分类仍需部署后重新生成 preview 复核。

`business_skipped` 行不会申请共享租约、创建审计运行或 Saga、启动 1688 浏览器，也不会生成聚水潭同步任务。全为该类行时，流水线返回 `status=business_skipped` 和 `online_actions_started=false`。

验证完整 1688 + 聚水潭契约，不操作线上：

```powershell
python scripts\run_1688_sku_replace_pipeline.py `
  --mode preview `
  --file templates\1688_sku_replace_sample.csv `
  --no-notify `
  --shared-runtime-root E:\1688\1688-script-new
```

## 受控执行

1. 使用业务批准的一条真实旧 SKU -> 新 SKU 映射生成单店 CSV。
2. 确认 preflight 为 `status=ok`、Crawler Worker 无活动任务、共享锁可用。
3. 先执行 `--limit 1`：

```powershell
python scripts\run_1688_sku_replace_pipeline.py `
  --mode execute `
  --file <approved-replacement.csv> `
  --limit 1 `
  --yes `
  --shared-runtime-root E:\1688\1688-script-new
```

4. 验收 1688 报告中的 `success/already_replaced`、新货号持久化证据，以及聚水潭结果中的 `success/already_synced`。
5. 确认钉钉通知、Worker 恢复和共享锁释放后再扩大批量。

### 登录态失效处理

生产执行默认复用映射的 Profile。发现登录失效后，执行器会释放 ERP 浏览器并调用共享 1688 项目的账号级登录一次；该调用显式复用既有滑块 RPA，最多 4 次。随后重新打开管理页，并核对 `expected_member_id` 和店铺。未解决滑块、未知风控或技术异常写审计并发钉钉，只有真实身份不匹配停止该店。只有人工调试时才使用 `--require-manual-login`。

## 正式日度任务

安装或更新任务定义：

```powershell
PowerShell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\manage_1688_sku_replace_daily_task.ps1 `
  -Action install `
  -DailyAt 14:00 `
  -ProjectRoot E:\1688\ERP-final-75368f8\furniture-uploader `
  -SharedRuntimeRoot E:\1688\1688-script-new `
  -JushuitanRoot E:\1688\ERP-final-75368f8\jushuitan-sku-offline-batch
```

- 正式任务：`YYDD-1688-Replace-Daily`，S4U，每天 14:00，`MultipleInstances=IgnoreNew`。
- 受控启动器：`YYDD-1688-Replace-Daily-Launcher`，SYSTEM；不得创建第二套 Replace 任务名。
- 手工只读 Preview 使用 `-Action preview`；等价启动使用 `-Action start`。状态核对使用 `-Action status`，同时读取 Task、Launcher 和 `logs/sku_replace/scheduler/latest.summary.json`。
- 同一业务日期已有 `success/completed_with_exceptions/business_skipped/no_tasks` Summary 时不再次执行；需要恢复时必须按精确失败范围走独立恢复流程，不能删除 Summary 后广泛重跑。
- `LastTaskResult=0`、任务 `Running/Ready` 或 manager exit 0 不是业务验收。必须核对 Replace Run/Item、Saga、Outbox、1688 持久化和聚水潭终态。

## 日志

- Preview：`logs/sku_replace/previews/`
- 数据源 Preview、业务跳过与拒绝明细：`logs/sku_replace/db_previews/`
- 1688 结果：`logs/sku_replace/run_reports/`
- 流水线：`logs/sku_replace/pipelines/`
- 正式审计：`JSReportReplica.app.ali1688_sku_replace_run/item`
- 聚水潭证据：`jushuitan-sku-offline-batch/artifacts/1688-link-sync/`
- 聚水潭幂等账本：`jushuitan-sku-offline-batch/storage/1688-link-sync-ledger.jsonl`

## 禁止事项

- 不允许从“销售中”Tab搜索替换或下架商品。
- 不允许把替换自动降级为整商品下架或新增 SKU。
- 不允许在旧新货号冲突时继续提交。
- 不允许把 `运营自行组合替换` 当作 SKU 写入平台；必须按 `组合货号` 业务跳过。其他说明文字仍按非法 SKU 拒绝。
- 不允许 `A->B、B->C` 或互换式链式映射；该映射无法保证幂等重跑。
- 不处理短信、扫码、处罚页或未知验证码；滑块只允许受限恢复最多 4 次，不得无限重试。
- 不使用 `--no-shared-lock` 执行生产任务。
