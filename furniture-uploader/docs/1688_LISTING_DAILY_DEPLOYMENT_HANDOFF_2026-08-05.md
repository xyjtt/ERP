# 1688 上架日度部署与 Canary 交接

更新时间：2026-08-05

## 1. 交付边界

- 开发工作树：`D:\script_files\ERP_listing_production_closeout_20260804\furniture-uploader`
- 正式执行目录：`E:\1688\ERP-auto-listing\furniture-uploader`
- 正式任务：`YYDD-1688-Listing-Daily`
- 固定账号：`account_key=muke_lixiang`
- 账号绑定 CDP：`9306`
- 自动链路终点：保存草稿并进入 `draft_pending_review`
- 自动链路禁止：审批、submit、Offer writeback、创建第二个 CTG0286 草稿
- 草稿身份门禁：payload 必须只包含一个既有 `draft_id`，且检查器命令行值必须与其一致；缺失、多值或不一致立即停止
- 历史 `authorized_draft_rebuild_resumed` 不能授权生产新建草稿；没有现有 draft ID 的执行必须 fail closed

## 2. 输入契约

日度管理器只读取 `C:\ProgramData\YYDD\1688-listing\inbox\*.json`。每个文件必须是完整、已审核的 `listing_task_payload_v1`，包含真实业务 `account_key/shop_name`、标题、图片、规格、价格、库存和物流。

候选文件不移动、不删除。防重依赖批内 `task_id/idempotency_key` 和 `JSReportReplica.app.ali1688_listing_task`，当前源生命周期不符合条件时明确记为 `source_ineligible`。

## 3. 受控部署

在执行机交互式管理员 PowerShell 中执行，先确认没有同账号浏览器租约和正在进行的上架/下架写操作：

```powershell
Set-Location E:\1688\ERP-auto-listing\furniture-uploader
git fetch gitee --prune
git status --short --branch
git rev-parse HEAD
git merge --ff-only gitee/codex/listing-production-closeout-20260804
python -X utf8 -m unittest discover -s tests -p "test_*.py"
python -m compileall -q rpa scripts tests
```

若工作树非干净、HEAD 不等于交付 commit、账号绑定不是 `muke_lixiang:9306` 或租约状态未知，停止部署并报告，不清理现场。

## 4. Preview

Preview 会读取正式源和审计契约，但不会启动浏览器：

```powershell
& .\scripts\manage_1688_listing_daily_task.ps1 `
  -Action preview `
  -ProjectRoot E:\1688\ERP-auto-listing\furniture-uploader `
  -SharedRuntimeRoot E:\1688\1688-script-new `
  -CandidateRoot C:\ProgramData\YYDD\1688-listing\inbox
```

验收 `latest.summary.json`：身份必须为 `muke_lixiang`，候选只能是 `preview_ready`、`duplicate_existing`、`duplicate_in_batch` 或 `source_ineligible`；`identity_mismatch/invalid/infrastructure_failed` 必须停止。

## 5. 真实单草稿 Canary

1. 只保留一个已批准候选，确认 CTG0286 现有草稿 ID，不创建第二草稿。
2. 执行一次 `-Action run -MaxItems 1`。该命令只调用 draft，不含 submit。
3. 必须取得 `draft_saved_pending_review`、单商品 result、正式审计和 Saga 记录。
4. 使用 `inspect_1688_saved_draft.py` 从商品管理真实行进入，核对草稿 ID、标题、4 张主图、详情图、规格、价格库存、物流、发货地址和 `24小时发货/essxsfh`。
5. 独立复核未全部通过时，不审批、不提交、不重建草稿。

```powershell
python -X utf8 .\scripts\inspect_1688_saved_draft.py `
  --payload <CANARY_RESULT_JSON> `
  --output <INSPECTION_JSON> `
  --draft-id <EXISTING_DRAFT_ID> `
  --open-from-management `
  --account-key muke_lixiang `
  --expected-cdp-port 9306 `
  --shared-runtime-root E:\1688\1688-script-new
```

## 6. 仅一次提交门禁

日度管理器和计划任务永远不提交。只有独立复核 `status=passed`、审计状态一致、业务人工明确批准后，才可在单独受控命令中执行一次 `approve` 和一次 `submit`。提交前重新查询 Saga/Offer，已有 Offer 或 submit 成功证据时禁止再次点击。

approve 和 submit 必须使用同一个独立复核 artifact。artifact 必须绑定 `draft_id/account_key/shop_name/CDP/inspector_build_sha/payload_contract_sha256` 且全部必检字段通过；submit 还要求审计中的 `post_save_verified=true`。任何字段漂移都必须重新只读复核，不能复用旧批准。

## 7. 安装计划任务

真实草稿保存、独立复核和一次 submit 全部验收后才安装：

```powershell
& .\scripts\manage_1688_listing_daily_task.ps1 `
  -Action install `
  -DailyAt 08:00 `
  -ProjectRoot E:\1688\ERP-auto-listing\furniture-uploader `
  -SharedRuntimeRoot E:\1688\1688-script-new `
  -CandidateRoot C:\ProgramData\YYDD\1688-listing\inbox

& .\scripts\manage_1688_listing_daily_task.ps1 -Action status
```

核对任务 Action、Interactive/Highest、IgnoreNew、触发时间、账号、`LastTaskResult` 和 `latest.summary.json`。安装并不等于业务验收，首次自然触发后仍需核对审计、Saga、草稿和业务页面。

## 8. 风险与未验证项

- 本提交未连接生产、数据库、执行机或真实浏览器。
- CTG0286 历史草稿 ID 曾变化，必须以候选 payload、正式审计和商品管理行三方一致为准。
- 正式 Profile 仍可能遇到 `SYS_ERROR`、登录风控或滑块；这些必须 fail closed，不能绕过。
- 图片探针会向素材库上传图片但不会保存草稿，仍需在租约内受控执行。
- 日度候选生成前置业务流程不在本次实现范围；缺少完整审核候选时计划任务应安全空跑。
- 旧版通用 listing Saga operation key 没有区分 draft 与 submit，其 terminal 状态不可信。部署前必须按新的 `listing-draft` / `listing-submit` 阶段键核对或受控 reconcile，禁止把旧 terminal 状态当作已提交证据。
