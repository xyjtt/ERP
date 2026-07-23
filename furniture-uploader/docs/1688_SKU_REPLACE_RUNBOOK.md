# 1688 SKU 替换运行手册

更新时间：2026-07-23

## 业务规则

- 数据筛选：`平台=Alibaba`、`处理说明=全渠道替换`。
- 旧货号：`线上商品编码`。
- 新货号：`可替换商品编码（新）`，兼容读取旧表头 `可替换商品编码`。
- 非 SKU 值、缺字段、旧新相同、冲突映射和链式映射进入 rejected CSV/JSON；其他合法任务继续生成。
- 1688 商品搜索必须使用“全部”Tab。
- 同一店铺、同一商品 ID 的多个 SKU 在同一编辑页修改并一次提交。
- 1688 成功或 `already_replaced` 后，聚水潭执行“手动同步商品 -> 按链接同步 -> 立即下载”。
- 任一系统异常均写日志并发送钉钉；登录、风控、店铺不匹配会停止对应安全范围。

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

`2026-07-23` 正式源只读验收：加载 568 条；42 条 `运营自行组合替换` 被拒绝；其余 526 条去除 26 条重复后，生成 500 条可执行 preview。合法任务分布为乐畅 6、工莱 400、沃来 4、淘淘 90。该结果不代表已执行线上替换。

验证完整 1688 + 聚水潭契约，不操作线上：

```powershell
python scripts\run_1688_sku_replace_pipeline.py `
  --mode preview `
  --file templates\1688_sku_replace_sample.csv `
  --no-notify
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
  --yes
```

4. 验收 1688 报告中的 `success/already_replaced`、新货号持久化证据，以及聚水潭结果中的 `success/already_synced`。
5. 确认钉钉通知、Worker 恢复和共享锁释放后再扩大批量。

## 日志

- Preview：`logs/sku_replace/previews/`
- 数据源 Preview 与拒绝明细：`logs/sku_replace/db_previews/`
- 1688 结果：`logs/sku_replace/run_reports/`
- 流水线：`logs/sku_replace/pipelines/`
- 正式审计：`JSReportReplica.app.ali1688_sku_replace_run/item`
- 聚水潭证据：`jushuitan-sku-offline-batch/artifacts/1688-link-sync/`
- 聚水潭幂等账本：`jushuitan-sku-offline-batch/storage/1688-link-sync-ledger.jsonl`

## 禁止事项

- 不允许从“销售中”Tab搜索替换或下架商品。
- 不允许把替换自动降级为整商品下架或新增 SKU。
- 不允许在旧新货号冲突时继续提交。
- 不允许把 `运营自行组合替换` 等说明文字当作 SKU 写入平台。
- 不允许 `A->B、B->C` 或互换式链式映射；该映射无法保证幂等重跑。
- 不自动处理验证码、滑块或平台风控。
- 不使用 `--no-shared-lock` 执行生产任务。
