# 2026-07-28 1688 SKU 替换真实执行验收报告

## 1. 验收范围

- 数据日期：`2026-07-28`
- 执行环境：开发机真实 1688 账号、真实商品页面、真实聚水潭页面
- 审计数据库：`JSReportReplica/app`
- 数据源：`app.op_stop_sale`
- 处理说明：`全渠道替换`
- 唯一任务键：`(店铺, 商品ID, 线上商品编码, 可替换商品编码（新）)`
- 输入总数：`566`，唯一任务数：`566`，重复任务数：`0`

## 2. 最终业务结果

| 店铺 | 目标数 | 1688 成功 | 1688 已替换 | 业务异常 | 聚水潭成功/已同步 |
|---|---:|---:|---:|---:|---:|
| 阿里巴巴-常州乐畅家居有限公司 | 4 | 0 | 4 | 0 | 4 |
| 阿里巴巴-常州工莱家具 | 434 | 345 | 42 | 47 | 387 |
| 阿里巴巴-广州沃来贸易有限公司 | 14 | 0 | 14 | 0 | 14 |
| 阿里巴巴-广州淘淘家居有限公司 | 114 | 80 | 11 | 23 | 91 |
| **合计** | **566** | **425** | **71** | **70** | **496** |

结论：`566/566` 个唯一任务均有最终结果。`496` 条已在 1688 成功替换或确认已替换，且聚水潭全部为 `success` 或 `already_synced`；`70` 条为真实页面业务异常，按规则记录审计并发送钉钉，不继续无效重试。

## 3. 业务异常分类

| 异常分类 | 数量 | 含义 |
|---|---:|---|
| `sku_not_found` | 43 | 原 SKU/替换 SKU 不在真实商品 SKU 表中，或 SKU 表为空 |
| `submit_blocked_before_request` | 12 | 1688 页面校验阻止提交，未产生提交请求 |
| `task_not_found` | 6 | 页面未找到对应任务目标 |
| `replacement_sku_conflict` | 5 | 替换 SKU 已属于其他 SKU 行 |
| `product_unavailable` | 3 | 商品在管理页面不可用 |
| `delivery_service_backfill_failed` | 1 | 页面配送服务必填项无法自动补齐 |

这些分类均为逐条业务终态。它们不阻塞同批次其他任务，也不触发无限重试。

## 4. 队列与补偿结果

- 乐畅：直接批次执行完成，`4/4` 已替换，聚水潭 `4/4` 成功。
- 沃来：直接批次执行完成，`14/14` 已替换，聚水潭 `14/14` 成功。
- 淘淘：队列 `local_replace_queue_20260729_taotao` 完成，12 个批次中 5 个成功、7 个业务终态，技术耗尽为 0。
- 工莱：队列 `local_replace_queue_20260729_gonglai` 完成，49 个批次中 23 个成功、26 个业务终态，技术耗尽为 0。
- 工莱 `batch_043` 曾因聚水潭 `standalone qos limit` 限流失败；冷却后 `b043_a03` 补偿成功，1688 `already_replaced=9`，聚水潭 `success=9`。
- 历史 SQL Server 启动失败和账号锁等待不再消耗业务重试预算；恢复器会重分类历史尝试并只补跑未闭环批次。

## 5. 审计与通知验收

正式库 `JSReportReplica/app` 查询结果：

- 相关审计批次：`75`
- 活动审计批次：`0`
- 最新唯一任务：`566`
- 数据库最新状态与文件级汇总完全一致
- 最新任务对应的 Pipeline 通知：`566/566 notification_sent=true`

审计表：

- `app.ali1688_sku_replace_run`
- `app.ali1688_sku_replace_item`

## 6. 稳定性修复

- 替换批次管理器区分 `queue_blocked`、可重试技术失败和业务终态。
- 活动审计、账号锁等待和 SQL Server `08001` 启动失败不消耗业务重试预算。
- SQL Server 连接增加有界重试。
- 审计心跳改为有超时、可重试的独立子进程，心跳失败会停止当前拥有的阶段进程树。
- 1688 按 `account_key` 使用独立锁；聚水潭继续使用全局单路锁。
- 同一店铺保持串行；不同店铺可按独立 Profile 并发，避免同账号页面状态互相覆盖。

## 7. 回归结果

- ERP Python：`365 passed, 7 subtests passed`
- Python `compileall`：通过
- 聚水潭 TypeScript `check`：通过
- 聚水潭测试：`20/20` 通过
- 聚水潭构建：通过
- `git diff --check`：通过（仅有既有 CRLF 提示）

## 8. 关键证据

- 淘淘队列：`logs/sku_replace/queues/local_replace_queue_20260729_taotao/queue.summary.json`
- 工莱队列：`logs/sku_replace/queues/local_replace_queue_20260729_gonglai/queue.summary.json`
- Pipeline 汇总：`logs/sku_replace/pipelines/*.summary.json`
- 1688 逐条报告：`logs/sku_replace/run_reports/*.jsonl`
- 页面异常截图：`logs/screenshots/*_replace_group_submit_*.png`
- 页面异常 HTML：`logs/html_snapshots/*_replace_group_submit_*.html`

## 9. 运行边界

- 不能仅以进程退出码或队列状态宣告业务完成；必须同时核对最新任务、聚水潭结果、正式审计和通知。
- 同一账号不得并发打开多个替换/下架浏览器会话。
- 聚水潭页面自动化保持单路执行。
- 业务异常记录并通知后继续，不绕过 1688 页面校验，不进行无限重试。
