# 1688 上架日度部署与 Canary 交接

更新时间：2026-08-06

> 2026-08-06 S4U 修订：正式 Daily 使用 `Administrator/S4U/Highest`，不再要求 Administrator 保持 explorer 会话。无日触发的 SYSTEM Launcher 仅作为按需 fallback：对 S4U Daily 使用 `Start-ScheduledTask`；旧 Interactive 任务仅作为兼容路径继续使用 `RunEx(session_id)`。

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
- 独立检查版本：`listing_draft_inspection_v2`
- 非持久字段重放契约：`listing_submit_reapply_v1`

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

部署版本必须包含 CTG0286 原生请求契约修复：删除过期配送服务 ID `3385307`，从页面允许值中采用真实配送选择；地址同时写入 `cbuSendAddress` 和 `freight.sendAddressId`；`processOffer=false` 时保障步骤不得包含 `serviceName/spsCode` 或未选择的可选服务；只修正唯一 `draftId`，保留平台原生 `edit/isItemEdit`。保存后必须在结构化 request trace 中核对上述字段。

1. 只保留一个已批准候选，确认 CTG0286 现有草稿 ID，不创建第二草稿。
2. 执行一次 `-Action run -MaxItems 1`。该命令只调用 draft，不含 submit。
3. 必须取得 `draft_saved_pending_review`、单商品 result、正式审计和 Saga 记录。
4. 使用 `inspect_1688_saved_draft.py` 从商品管理真实行进入，核对草稿 ID、标题、4 张主图、详情图、规格、价格库存、配送服务、物流、发货地址和 `24小时发货/essxsfh`。
5. 独立复核未全部通过时，不审批、不提交、不重建草稿。

检查器对每个字段输出 `persisted`、`submit_reapply_required` 或 `failed`。只有 `delivery_service/send_address/logistics/buyer_protection` 可在完整保存证据下标记为 `submit_reapply_required`；该状态不表示字段已持久化，而是表示提交前必须按已绑定契约重新应用并精确读回。其他字段必须为 `persisted`。

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

approve 和 submit 必须使用同一个独立复核 artifact。artifact 必须绑定 `draft_id/account_key/shop_name/CDP/inspector_build_sha/payload_contract_sha256/field_outcomes_sha256/submit_reapply_contract_sha256` 且全部必检字段通过；submit 还要求审计中的 `post_save_verified=true`。任何字段漂移都必须重新只读复核，不能复用旧批准。

submit 点击前必须重新应用契约内全部非持久字段，并从当前 React 状态逐项精确读回。配送 ID 必须仍属于当前页面允许集合，买家保障必须为 `24小时发货/essxsfh`，页面不得存在任何必填提示。任一条件失败时禁止点击，不得用旧 artifact 或旧页面状态继续。

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

安装会注册 `YYDD-1688-Listing-Daily` 的 Administrator/S4U/Highest draft-only 日任务，并额外注册没有日触发的 `YYDD-1688-Listing-Daily-Launcher`（SYSTEM）作为按需 fallback。Launcher 只启动正式 Daily：S4U 任务走 `Start-ScheduledTask`；历史 Interactive 任务才解析唯一 Administrator 会话并使用 `Schedule.Service.RunEx($null, 4, session_id, $null)`。Launcher 不直接执行 Python、浏览器或 submit。

核对两个任务的 Action、Principal、IgnoreNew、触发时间、账号、`LastTaskResult` 和 `latest.summary.json`。受控等价触发统一使用 `-Action start`；脚本会根据 Principal 选择 S4U 或 Interactive 兼容路径。不得绕过脚本直接重复启动业务任务。验收必须包含 launcher/worker 任务历史、scheduled log、Saga、Offer/业务库和无第二草稿证明；安装不等于业务验收。

每次 Daily 运行还会在 `logs/listing_daily/scheduler/latest.runtime.live.json` 写入只读 `listing_runtime_live_v1` 快照。快照按现有 `draft_id` 分别计算 `listing-draft` 和 `listing-submit` operation key，读取 `app.ali1688_operation_saga` 与对应 outbox，并同时保留 `app.ali1688_listing_task` 当前行。Listing Saga 已为 terminal `completed/failed_terminal` 且没有下游 topic 时，空 outbox 标记为 `not_required`；仍需下游的 Saga 缺行才标记为 `missing`。Credential Manager、数据库连接或契约读取异常必须使本次调度 `infrastructure_failed`，不得将缺失证据解释为成功。

## 8. 风险与未验证项

- 本轮 launcher 修复已部署并通过执行机 668 项 unittest、compileall、JSON、PowerShell parser 和 diff-check；本轮日度验证明确命中 `duplicate_existing`，未对唯一草稿执行新的保存、审批或 submit。Submit Saga/Offer 仍以既有 accepted reconciliation 与 fresh 只读审计为业务证据。
- 执行机 Gitee fetch 因凭据不可交互认证失败，部署使用了权威 `b8c55de` 的唯一 bundle；该 bundle 和原有 untracked 项均保留，后续需恢复非交互 Gitee 凭据或继续使用可审计的 bundle 线。
- CTG0286 历史草稿 ID 曾变化，必须以候选 payload、正式审计和商品管理行三方一致为准。
- 正式 Profile 仍可能遇到 `SYS_ERROR`、登录风控或滑块；这些必须 fail closed，不能绕过。
- 图片探针会向素材库上传图片但不会保存草稿，仍需在租约内受控执行。
- 日度候选生成前置业务流程不在本次实现范围；缺少完整审核候选时计划任务应安全空跑。
- 旧版通用 listing Saga operation key 没有区分 draft 与 submit，其 terminal 状态不可信。部署前必须按新的 `listing-draft` / `listing-submit` 阶段键核对或受控 reconcile，禁止把旧 terminal 状态当作已提交证据。
