# 1688 下架后聚水潭清除链接 SOP

更新日期：2026-07-17

## 业务边界

本链路只处理 1688 平台，动作是“店铺商品管理 -> 按 SKU -> 更新/清除链接 -> 清除链接”。

- 不执行旧流程的 `txcj` 批量改码。
- 不处理淘宝、天猫、抖音等其他平台。
- 不接受尚未在 1688 验证为 `success` 或 `already_offline` 的真实执行任务。
- 不自动处理验证码、滑块、短信验证或平台风控。
- 聚水潭失败不回写、不撤销、也不伪造 1688 的成功状态。

## 精确任务身份

每条任务必须同时具备：

- `store_name`：聚水潭中的完整 1688 店铺名，例如 `阿里巴巴-常州工莱家具`
- `product_id`：1688 商品 ID
- `online_sku`：线上商品编码
- `platform_store_item_code`：聚水潭店铺商品编码
- `platform=Alibaba`
- `handling=全渠道下架`
- `source_status=success|already_offline`（真实执行时）

幂等键由“店铺 + 商品 ID + SKU + 平台店铺商品编码”生成。相同商品/SKU 下的不同平台店铺商品编码不会被错误合并。

## 三层入口

### 1. Preview

只校验任务和生成报告，不启动浏览器：

```powershell
npm run cleanup:1688 -- --mode preview --file <handoff.jsonl>
```

### 2. Probe

登录并只读查询精确行，不勾选、不清除：

```powershell
npm run cleanup:1688 -- --mode probe --file <handoff.jsonl> --limit 1
```

### 3. Execute

只有显式 `execute --yes` 才会清除链接：

```powershell
npm run cleanup:1688 -- --mode execute --yes --file <handoff.jsonl> --limit 1
```

生产调度可从 1688 项目使用一条命令串联：

```powershell
python D:\script_files\ERP\furniture-uploader\scripts\run_1688_stop_sale_pipeline.py `
  --mode preview `
  --file <stop-sale.csv>
```

真实链路必须额外使用 `--mode execute --yes`。未验证前不得直接对当天全量任务执行。

## 凭据

以下敏感值只从进程或 Windows 用户环境变量读取，不写入 `.env`、JSON、日志或报告：

- `JST_USERNAME`
- `JST_PASSWORD`
- `DINGTALK_WEBHOOK`
- `DINGTALK_SECRET`

修改用户环境变量后，应重启承载调度任务的终端或 Worker，使新进程继承变量。

## 执行校验

每条任务按以下顺序执行：

1. 校验本地成功账本，已有成功记录则返回 `already_cleared`，不启动浏览器。
2. 登录聚水潭；出现验证码、滑块或风控时停止会话。
3. 进入店铺商品管理并切换“按 SKU”。
4. 精确选择 1 个 1688 平台、1 个目标店铺。
5. 同时填写商品 ID 和线上 SKU 后查询。
6. 在结果行再次校验店铺、商品 ID、SKU、平台店铺商品编码。
7. 必须唯一命中 1 行，才允许勾选。
8. 打开“更新/清除链接”，选择“清除链接”。
9. 在平台风险提示中确认。
10. 使用相同四字段反查；精确行消失才记为 `success` 并写入账本。

## 失败与补偿

以下情况均记录 `failed`，不伪报成功：

- `login_required` / `risk_control`：停止整个聚水潭会话。
- `store_mismatch`：停止对应店铺。
- `task_not_found`：四字段没有精确命中。
- `ambiguous_match`：命中多行。
- `row_selection_failed`：页面未接受勾选。
- `action_unavailable` / `confirmation_failed`：清除入口或确认按钮不可用。
- `verification_failed`：提交后精确行仍存在。

清除链接没有可靠的自动回滚动作。需要恢复时应在聚水潭按原店铺、商品和 SKU 人工重新匹配链接；1688 商品保持下架，不能为了补偿聚水潭失败而自动恢复上架。

## 报告与告警

- 结果：`results/1688-link-cleanup/<run-id>.jsonl`
- 汇总：`results/1688-link-cleanup/<run-id>.summary.json`
- 页面证据：`artifacts/1688-link-cleanup/<run-id>/`
- 幂等账本：`storage/1688-link-cleanup-ledger.jsonl`

真实执行默认发送钉钉汇总；`--no-notify` 仅用于受控测试。聚水潭进程存在失败时返回非零退出码，调度器必须据此报警和创建补偿任务。

运营群消息包含分店汇总及异常任务的店铺、商品 ID、SKU、中文原因；不包含本机路径和技术堆栈。异常明细最多发送 60 条，更多内容保留在技术报告中。

## 2026-07-17 受控验收

- 任务：常州工莱家具 `1005537490740 / CY001301N35 / 6166627859436`
- `probe`：唯一精确命中，未修改。
- 首次 `execute`：清除后反查无匹配，`success=1`。
- 幂等重放：不启动浏览器，`already_cleared=1`。
- 成功报告：`results/1688-link-cleanup/20260717-191138.jsonl`
- 幂等报告：`results/1688-link-cleanup/20260717-191327.jsonl`

随后对已完成 1688 验收的 10 条样本执行跨店铺受控批次：

- Preview：`9 preview + 1 already_cleared + 0 failed`。
- 首轮发现店铺弹窗会保留上一店铺选择，安全校验在 `2/3/4 个店铺` 时阻断；没有发生错店清除。
- 修复后每次先清空并验证 `0 平台 / 0 店铺`，再验证目标 `1 平台 / 1 店铺`。
- 最终：3 条已清除或账本幂等，7 条精确查询无匹配，按 `task_not_found` 记录并停止重试。
- 最终报告：`results/1688-link-cleanup/20260717-194734.jsonl`。
- 钉钉受控批次结论消息已实际发送，返回 `notification_sent=true`。
- 2026-07-18 新版运营格式已实际发送 2 条消息（分店汇总 + 7 条异常明细），全部发送成功。

该结果证明单商品闭环、跨店铺安全停止和无匹配异常路径可用，不等于授权执行 434 条全量生产任务。
