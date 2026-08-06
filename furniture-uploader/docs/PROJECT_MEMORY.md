# Project Memory

## 2026-08-06 Managed Update (Listing S4U Daily Runtime)

- 正式 Crawler Worker 已在执行机证明 `Administrator/S4U/Highest` 可于 Session 0 启动真实 Edge/CDP；Listing 浏览器链路复用相同的账号 Profile、`ensure_1688_authenticated_session`、租约与 CDP 绑定，不依赖可见桌面。
- Listing Daily 改为 `Administrator/S4U/Highest`。SYSTEM Launcher 是无日触发的按需 fallback：对 S4U 任务使用 `Start-ScheduledTask`，仅对历史 Interactive 任务保留 `Schedule.Service.RunEx(session_id)` 兼容路径，避免 08:00 重复启动。
- 执行机 S4U 探针只输出配置布尔值，已验证 `app-writer`、`listing-source`、`muke_lixiang` 三个 Credential Manager 引用可读，且账号 Profile 存在；未读取或记录秘密值，也未启动 9306。
- 安全边界不变：清除 `ENABLE_1688_LISTING_SUBMIT`、`MultipleInstances IgnoreNew`、账号租约、任务 claim、唯一 draft/Offer 幂等门禁继续生效。

## 2026-08-05 Managed Update (Interrupted Daily Manager Recovery)

- 安全复核补强：Manager 锁释放改为 Windows 独占句柄内复核 cycle/run/token 指纹并标记删除；恢复清单必须与批准的 52 missing + 7 technical 四字段身份集合和双 SHA-256 完全一致，否则只产诊断。
- Child scope 只接受正式 pipeline Summary/report 证据；仅含 `run_id + reason=higher_priority_browser_write` 且无 Summary/数据库行的日志记录为 `pre_audit_evidence` orphan，不伪造 child。未知日志继续阻塞收口。
- 聚水潭 `already_cleared` 只接受表头映射后的店铺、商品 ID、线上 SKU、平台店铺商品编码四列精确匹配或精确筛选后的显式零行；`ABC1` 不匹配 `ABC10`。
- Outbox Worker 必须按带 SHA-256 的批准 `operation_key` 集合事务领取，完整集合不一致则回滚；日常下架/替换 handoff 同样作为本批次批准集合。
- 以上仍是开发变更，未部署或执行生产恢复。

- 新增正式中断收口工具 `scripts/recover_interrupted_stop_sale_daily_manager.py`。它只在管理器锁的 `cycle/manager_run_id/token` 匹配、锁 PID 已死亡、artifact 与数据库 child run 范围完全一致、所有 child run 已终态且 Summary 与审计一致时继续。
- 收口顺序固定为：原子写入管理器 `summary.json`，再次读取并校验完整锁快照和 fencing/token 证据，然后才释放管理器锁。二次校验漂移时保留锁并标记 `blocked_lock_revalidation`，禁止手删锁。
- 新增 `scripts/build_interrupted_stop_sale_recovery_manifest.py`，仅接受已完成收口且 `lock_removed=true` 的管理器 Summary；按原始 Preview CSV 和 child reports 精确生成 `missing.csv`、`technical.csv`、`recovery.csv` 与 `manifest.json`。
- 恢复清单排除业务终态、1688 与聚水潭已完成项；登录、风控、身份和页面技术异常单独归入 `technical`，缺少任何 1688 执行证据的项归入 `missing`。缺失不得伪装为 0 或成功。
- Saga/Outbox 已兼容移植 `bfc3c6d`：逐项保留同批次中已验证成功结果；只有精确查询零行或目标链接已不存在时才记 `already_cleared`；单条重排使用 operation/status/error/attempt/run/saga 的锁内 CAS，禁止批量重排。
- 当前仅完成开发验收。生产批次 `daily_20260804_130814_970163` 尚未收口；此前观察到的 52 个未执行、7 个技术失败和历史 11 条 Outbox 尚未通过新工具在生产重新核对或处理。
- 开发验证：Python 全量 `616/616`，恢复与 Saga/Outbox 定向 `19/19`，扩展下架集合 `76/76`；聚水潭 TypeScript `26/26`、`check`、`build` 通过；Python `compileall`、PowerShell 解析与 `git diff --check` 通过。生产部署、Credential Manager、真实页面、数据库和浏览器验收仍待独立执行。

## 2026-07-28 Managed Update (Bounded Slider Login Recovery)

- 下架和替换仍先复用账号独立真实 Edge/Profile；登录失效时调用共享 1688 `src.cli login`，仅该调用显式启用既有滑块 RPA，最多 4 次，不处理短信、扫码、处罚页或未知风控。
- 滑块消失不作为成功证据。登录后必须从外置 `accounts.json` 读取 `expected_member_id`，并与目标店铺共同核验；真实 `member_id`/店铺不匹配返回店铺安全终态。
- 默认停店范围收窄为 `store_mismatch`。商品管理搜索超时、Edge 窗口/renderer 异常先重建当前店铺 Profile 会话重试一次；仍失败以及登录未恢复、身份信息缺失等逐批记录、写审计并钉钉通知，随后继续其他店铺；业务终态仍不自动整商品下架。
- preflight 新增 `all_account_identities_configured`，四店缺少合法 `expected_member_id` 或同值稳定 `shop_id=1688-member:<member_id>` 时不得进入 execute。
- 开发回归：ERP `325 passed, 5 subtests passed`；聚水潭 `check`、`18/18`、`build` 通过。共享 1688 仓库本次相关测试全过，全量为 `726 passed, 2 skipped, 75 subtests passed, 9` 个与 `origin/main` 一致的既有工单/文档测试失败。开发机没有正式四店 Profile，真实滑块、真实登录、1688 页面、聚水潭、审计和钉钉仍必须由执行机单条 Canary 验收。

## 2026-07-27 Managed Update (Daily Stop-Sale Recovery Hardening)

- 执行机批次 `daily_20260727_123002_977385` 于 12:30:02 至 16:58:59 运行：加载 116 条、选中 104 条、重复 12 条；1688 成功 13 条、已下架 10 条、失败 64 条、未尝试 17 条；聚水潭成功 23 条，最终状态为 `failed`。
- 主要技术故障包括：乐畅登录失效并触发安全停店、工莱/沃来等待“销售信息”超时、淘淘 Edge renderer 超时。业务终态继续按异常记录并通知，不允许自动整商品下架。
- 最后两个淘淘重试批次在启动 Pipeline 前发现 Crawler Worker 被外部重新启动，旧实现因此没有生成批次 Summary、审计终态和批次通知。调查已确认 `1688-Watchdog` 只做状态探测，不是 Worker 启动源。
- 调度器现在在每次店铺批次和每次重试前重新检查 Worker；如被外部启动，只禁用/停止 Worker 自身并等待其进程退出，同时在管理器 Summary 中记录 `worker_pause_check_count` 和 `worker_reassertions`。
- Pipeline 的 Worker/活动爬虫保护检查已移入审计批次生命周期。保护失败也会把待处理明细记为 `not_attempted`，生成 Summary，完成审计并发送钉钉通知。
- 1688 自动化遇到可重试的 `automation_error` 时，不再复用可能损坏的 renderer；先关闭当前店铺拥有的浏览器并重建 Profile 会话，再重试同一商品组。登录、风控和业务终态仍沿用原有保守处理。
- 开发机验证通过：Python `319 passed, 5 subtests passed`、`compileall` 通过；聚水潭 `npm run check`、`18/18` 测试和 `npm run build` 通过。执行机部署、真实浏览器 Canary、正式审计库和钉钉结果复核仍是交付门槛。

## 2026-07-23 Managed Update (1688 SKU Replacement + Jushuitan Link Sync)

- 新增 `1688_sku_replace` 执行系统，筛选 `平台=Alibaba / 处理说明=全渠道替换`。
- 数据字段采用 `线上商品编码 -> 可替换商品编码（新）`，兼容旧表头 `可替换商品编码`；空值、非 SKU 值、旧新相同、映射冲突和目标货号碰撞会进入 rejected 报告，合法行继续生成 preview。
- 下架和替换均强制从商品管理 `全部` Tab 搜索，URL 固定归一化为 `tab=all&q=&filterOfferId=`。
- 同一店铺、同一商品 ID 的多个 SKU 在一个编辑页内完成，替换只提交一次，并重新打开编辑页复核新货号。
- 替换成功/已替换后生成聚水潭 `sync_by_link` 任务：进入“手动同步商品 -> 按链接同步”，按店铺分批填写去重商品 ID 并点击“立即下载”。
- 新入口：`scripts/run_1688_sku_replace_pipeline.py`；默认 preview，真实执行必须显式 `--mode execute --yes`，并受共享锁和 Worker 静止检查保护。
- 正式审计使用独立表 `JSReportReplica.app.ali1688_sku_replace_run/item`，不与下架审计混用；2026-07-23 DDL 已提交，契约检查 `missing_tables=[] / ready=true`。
- 本地验证：Python `286 passed, 5 subtests`；聚水潭 TypeScript `check`、`18/18`、`build` 通过；两条样本端到端 preview 通过。
- `2026-07-23` 正式源只读 preview：568 条加载，526 条格式合法，26 条重复，500 条可执行，42 条因 `运营自行组合替换` 被拒绝；尚未执行真实 1688 替换或聚水潭立即下载。
- 尚未完成 live 验收：未获得业务批准的真实旧 SKU -> 新 SKU canary 映射，因此没有点击 1688 发布或聚水潭“立即下载”。
## 2026-07-30 Managed Update (24-Hour Shipping Contract)

- User-confirmed buyer-protection shipping time is fixed to `24小时发货`; the live 1688 service code is `essxsfh`.
- Publish UI default, draft page-state patch, `draftSubmit` request patch, post-save verification, and submit reconciliation now share the same name/code contract.
- A normal user Edge session can open target draft `6a69bee6e4b01cad1b297a52`, but the dedicated `muke_lixiang` automation profile still receives first-page `SYS_ERROR`. Do not save or submit through that profile until its authenticated session is refreshed and independently rechecked.

## 2026-07-29 Managed Update (CTG0286 Independent Persistence Gate)

- User-authorized cleanup deleted corrupt drafts `6a635fcee4b0eda6ebbdf340` and `6a6976c1e4b09c827f2edbc9`. The only CTG0286 draft is now `6a69bee6e4b01cad1b297a52`; do not create another draft while it remains present.
- The product-management `tab=all` page shows 4 total drafts and exactly one row for the target ID under shop `木刻理想`.
- A patched `draftSubmit` returned HTTP 200 and the expected ID, but a fresh independent browser reopened the saved URL with empty title, price, inventory, images, specifications, and logistics. The official management-row link returned `SYS_ERROR` before any publish-page XHR/fetch request.
- Two non-target control drafts, including the oldest visible draft, also returned `SYS_ERROR` from their official management links. This establishes an account/platform draft-entry blocker rather than a target selector or tab error.
- Draft execution and post-save verification must use the official `fillProductInfo.htm?operator=draft2offer&offerDraftId=...` entry. A same-session reload of the `operator=new&catId=...` URL is not persistence evidence.
- Formal `JSReportReplica.app` state is `workflow_state=blocked`, `approval_status=rejected`; latest audit event is `draft_verification_failed` by `codex-listing-independent-verifier`. No submit, Offer ID, or writeback occurred.
- Development verification passes: listing `421/421`, title engine `89 passed, 3 subtests passed`, 12 JSON files valid, Python compile and `git diff --check` clean.
- Approval, submit, executor deployment, and another delete/rebuild are prohibited until the official draft entry loads and an independent full-field inspection passes.

## 2026-07-29 Managed Update (CTG0286 Draft Identity Recovery)

- CTG0286 recovery is limited to the two historical draft IDs already present in workflow evidence: `6a635fcee4b0eda6ebbdf340` and `6a6976c1e4b09c827f2edbc9`. Do not create a third draft and do not delete either draft automatically.
- Draft repair now opens the platform's `fillProductInfo.htm?operator=draft2offer&offerDraftId=...` entry and verifies the expected draft ID before save. The `draftSubmit` request and response must carry the same ID; a missing or different ID fails closed.
- The product-management draft box currently contains 5 rows and proves both target IDs still exist. It exposes the exact formal `offerDraftId` edit link for each row; old-draft title text is `--`, while the newer row shows the target title.
- A real click on the old draft's row link opened the correct ID and still rendered 1688 `SYS_ERROR` with a complete page and `SellPublishSdk` present. This is platform-unavailable evidence, not proof that the draft was deleted.
- The product-management draft probe records every visible draft row, every row/page link and `href`, related `data-*` attributes, and extracted `draftId/offerDraftId` values. Independent inspection supports the same real management-link click path and fails fast on explicit platform error pages.
- The fresh source lifecycle gate passed at `2026-07-29 13:42 +08:00`. Development verification is `400/400` listing tests plus `89 passed, 3 subtests passed` for the title engine; doctor, Python compile, and 12 JSON files also pass.
- Real draft repair, refreshed field acceptance, approval, one submit, Offer ID verification, audit, and Offer writeback remain blocked by the 1688 error page. Do not create a third draft or delete either historical draft; recovery now requires platform availability or an explicit human decision to change that policy.

## 2026-07-28 Managed Update (CTG0286 Pre-Save Field Convergence)

- Scope remains the existing draft `6a635fcee4b0eda6ebbdf340` for SKU `CTG028601N1416V01`; creating a second draft and submitting an Offer remain prohibited.
- Live evidence showed that detail-image batch reloads can clear the title, main image, committed color/size specs, and logistics fields after their original publish steps complete.
- Draft save now converges nonpersistent fields before the request: restore a missing/non-square main image, reset and reapply mismatched committed specs, then reapply the exact payload title before patching price, quantity, and description.
- The pre-save gate now requires the exact payload title, exact committed spec values, a square first main image when configured, and all uploaded detail-image URLs represented in the description.
- A fully uploaded contiguous detail-image checkpoint can now be resumed without uploading the same 48 assets again; the save path only rewrites the description HTML from the recorded CDN URLs.
- Validation: `380/380` unit tests passed; Python compile, all 11 JSON config files, `doctor`, and `git diff --check` passed. Live draft repair and formal audit are still required for business acceptance.

## 2026-03-31 Managed Update (T-005 Draft System-Error Isolation Round-2)

- 主线仍为 `T-005`（仅 `draft`），`T-001` 冻结未动。
- 已新增 `draftSubmit` 系统错误隔离策略：
  - `draft_request_patch_retry_modes` 支持 `full -> capture_only` 自动降级；
  - 当 backend reject（`系统错误，请稍后尝试`）时，重试阶段自动切换到仅抓包模式，避免补丁重写持续干扰；
  - run_report 新增重试轨迹字段：`draft_submit_retry_count`、`draft_submit_retry_errors`、`draft_submit_backend_message`、`draft_request_patch_mode_history` 等。
- 配置同步：
  - `config/platforms/1688.json` 增加默认 `draft_request_patch_retry_modes=["full","capture_only"]`；
  - `config/platforms/1688.local.json` 同步该策略并上调退避参数（`draft_verify_retry_count=8`，`wait/backoff` 增强）。
- 回归结果：
  - 单测 `140/140` 通过；
  - `doctor` 通过（保留既有 2 条 warning）；
  - `product/variant validate-only` 均通过。
- 当前结论：
  - 代码侧可控问题继续收敛；
  - `50 连续成功` 仍被 live 平台返回 `system error` 阻塞，下一步需要在有效登录会话中继续压测确认。

## 2026-03-31 Managed Update (T-005 Fast Iteration Optimization)

- 已完成两项提速修复并实跑验证：
  - `main_image` 在本地配置改为 `upload_via_react_bridge=true`，避免主图 picker 偶发超时阻断；
  - 新增 `draft_submit_backend_reject_fast_fail_count`，当同类 backend reject 连续达到阈值时快速失败，避免单次运行长时间无效退避。
- 最新 live 报告：
  - `logs/run_reports/20260331_091725.jsonl`：定位到 `main_image` 步骤超时；
  - `logs/run_reports/20260331_092030.jsonl`：主图步骤已通过，失败回到 `draft_submit system error`；
  - `logs/run_reports/20260331_093204.jsonl`：快速失败生效（`3/3`），并记录 `patchMode= capture_only / patchApplied=false`。
- 结果：
  - 已确认当前核心阻塞仍是平台返回 `系统错误，请稍后尝试`；
  - 调试迭代效率已提升，可更快进行多批次压测与归因。

## 2026-03-31 Managed Update (T-005 Draft Hardening + Live Blocker)

- 主线仍为 `T-005`（仅 `draft`），`T-001` 冻结未动。
- 代码已新增并通过回归：
  - 买家保障发货时效 `force_reselect`；
  - draft 校验 `buyer protection` 告警 grace refresh；
  - draft submit 瞬时后端错误自动重试（含页面刷新 + 退避等待）；
  - draft page-state 输出 required labels 与 runtime cat-prop 回写；
  - draft request patch 增加 `catProp` runtime 值注入，避免请求体属性回落 `null`。
- 配置已补齐：
  - `config/platforms/1688.local.json` 新增 submit 重试参数；
  - `draft_page_state_patch.cat_props` 增加品牌/材质/是否配送上门/是否提供安装服务等兜底。
- 验证结果：
  - 单测 `136/136` 通过；
  - `doctor` 通过（保留 2 条既有 warning）；
  - live 历史最长连续成功链更新为 `13`（截至 `20260330_232214.jsonl`）。
- 当前阻塞：
  - 自 `2026-03-31` 凌晨起，`draftSubmit` 连续返回
    `{\"success\":false,\"data\":{\"message\":\"系统错误，请稍后尝试\"}}`；
  - 代表报告：`20260331_001653.jsonl`、`20260331_003545.jsonl`、`20260331_005844.jsonl`、`20260331_011321.jsonl`；
  - 导致 `50 连续成功` 验收暂停，需等待平台接口恢复或会话侧人工复位后继续压测。

## 2026-03-30 Managed Update (T-005 Draft Priority Activation)

- 项目执行优先级已切换：
  - `T-005` 提升为当前 P0 执行任务（仅 `draft` 范围）；
  - `T-001` 已按策略冻结，避免并行改动冲突。
- 根目录治理文档已同步：
  - [D:\script_files\TASK_BOARD.md](D:/script_files/TASK_BOARD.md)
  - [D:\script_files\HANDOFF.md](D:/script_files/HANDOFF.md)
  - [D:\script_files\TASK_PACKAGE_T-005.md](D:/script_files/TASK_PACKAGE_T-005.md)
- 单机启动器界面已完成中文化改动：
  - `system/platform/input-mode/limit` 标签改为中文；
  - `input-mode` 下拉项改为 `变体/商品/自动`，内部参数仍映射到 `variant/product/auto`；
  - 文件选择器类型描述和参数报错文案改为中文。
- 开工前检查结果：
  - 单元测试通过 `129/129`；
  - `doctor` 状态 `ok`（保留两条既有 warning：`sanitize_spec_inputs`、`extract_match_source_id`）；
  - `product/variant` 两条 `validate-only` 均通过。
- 最新 live draft 验证：
  - 运行报告：`logs/run_reports/20260330_185234.jsonl`
  - 结果：`status=success`
  - `draftId=69ca5699e4b095959ac8beff`
  - 草稿校验字段：`draft_send_address_value=861873672`、`draft_logistics_dimensions` 已完整写入。
- 当前剩余工作：
  - 进入 `T-005` 连续批次验证，达成“连续 50 条草稿成功”验收阈值。

## 2026-03-27 Managed Update (P15 Blocking Fixes In Code)

- `1688_direct` 针对 `P15` 的草稿持久化问题已补一轮代码级修复：
  - draft page state patch 增加 `deliveryServiceIds`，会同步写入 `customExtraService.customServices` 和 `viewModelMap`；
  - draft request patch 增加配送服务快照解析与请求体重写，尽量避免 `customServices=[]`；
  - 运行态 `draft_delivery_service_state` 现在会保留 `selectedLabels/selectedServiceIds`，用于后续补丁精确注入。
- 类目确认回跳稳定性新增兜底：
  - 确认按钮点击支持多选择器回退；
  - `select.htm` 回跳超时后会尝试接管已存在的 `publish.htm` 窗口。
- 本地验证结果：
  - 单测通过 `115/115`；
  - 尚未产出新一轮 live run report（需要下一步在登录会话中实跑确认）。

## 2026-03-26 Managed Update (Direct Publish Runtime Stabilization)

- `1688_direct` runtime hardening completed:
  - category selection now supports visible-option click only and category-confirm new-tab attach;
  - quantity fallback added for SKU-table mode when `#guid-totalSales` is hidden;
  - main-image step now accepts already-present main image when picker re-open times out;
  - optional category prop/spec rules now skip safely on missing fields (required rules still fail).
- Draft path now has strict request-trace behavior:
  - draft submit trace returns explicit `present/complete` status;
  - if no request is captured, code retries via dispatch-event click before failing;
  - pre-draft send-address and delivery-service auto-selection helpers added.
- Submit path (`auto_submit`) feature-complete in code:
  - submit request trace install/assert with retry;
  - optional submit post-verification hook;
  - run-report now carries `submit_request_trace_*` and `post_submit_verified` fields.
- Live status on current 9224 managed session:
  - main path reaches draft save and captures successful draftSubmit responses;
  - remaining blocker: refresh still shows `配送服务` and `买家保障第1行发货时间` required warnings in some runs, so P15 live closeout not finished yet.

## 2026-03-26 Managed Update (Submit Path Fixed)

- `sku_offline` execution now includes:
  - pre-submit switch-state stability gate;
  - runtime `skuTable` target-SKU status enforcement (`sku_status=-2`) check;
  - submit request trace collection (`submit.htm` / `draftSubmit.htm`);
  - JS dispatch-event retry when first submit click does not emit a request.
- Latest live outcomes:
  - `logs/sku_offline/run_reports/20260326_211051.jsonl` -> success (`DNZ023901N1221V01`);
  - `logs/sku_offline/run_reports/20260326_211644.jsonl` -> success (`YJT000802N961V01`).
- Latest regression replay:
  - `logs/sku_offline/run_reports/20260326_212944.jsonl` -> `already_offline=2`, `failed=1`;
  - failed SKU remains `973480751525 / DNZ005372N965V01` (submit request captured, but status not persisted offline).
- Practical effect:
  - previously frequent `submit_failed`/`still online` cases are now mostly passing on live regression samples, with one tracked SKU-specific exception.

## 2026-03-26 Managed Update

- Added resilient stale-element retry handling in shared Selenium helper:
  - `BrowserRPA._run_with_stale_retry` now retries both typed stale exceptions and webdriver stale-message variants.
- Added `sku_offline` reliability hardening:
  - robust SKU match fallback by normalized code comparison;
  - improved `sku_not_found` error categorization and reporting;
  - required delivery-service auto-fill in `#guid-customExtraService`.
- Added utility script to derive offline candidates from local html snapshots:
  - `scripts/export_online_sku_candidates.py`
  - generated file: `templates/1688_sku_offline_online_candidates_20260326.csv`
- Current live status:
  - login/session and page entry are stable;
  - execute reaches submit stage consistently;
  - some products still fail final verification with `submit_failed` (`submitted but still online`), requiring further live selector/submit-state diagnosis.

更新时间：2026-03-26

## 项目定位

- 项目：`furniture-uploader`
- 当前业务目标：实现电商后台自动上架的最小可落地版本
- 当前执行入口：`1688_direct`
- 当前新增入口：`1688_sku_offline`
- 当前首个平台：`1688`
- 当前执行引擎：`Python + Selenium`
- 当前默认发布策略：人工复核优先，代码层已经支持 `manual / draft / submit`

## 当前已验证事实

- Python 依赖已安装
- 单元测试通过
- `doctor` 通过
- 已支持连接本机已登录的 `Edge/Chrome` 调试会话
- 1688 发布页主线已完成真实干跑
- 1688 SKU 下架第一期代码骨架已落地
- 1688 SKU 下架已支持 Excel/CSV 预览、筛选、去重、防重和运行报告
- 1688 SKU 下架店铺过滤已支持“全称/简称”规范化匹配
- 1688 SKU 下架预览已输出过滤诊断（applied_filters / loaded_store_names / filter_hint）
- 类目自动选择已在 live 页面验证
- 成功结果提取已支持 `current_url / body_text / page_source`
- 可选属性缺失时会自动跳过，不阻塞整单

## 当前已经做完的核心能力

- 模板读取与字段校验
- 图片路径校验
- 本地配置覆盖机制
- 选择器采集工具
- 环境体检
- 数据库预检骨架
- 发布前错误捕获
- 主图上传桥接
- 详情图上传桥接
- TinyMCE 描述写入
- 类目路径自动化
- 1688 smoke 回归模板
- 1688 SKU 下架任务解析与定时扫描入口
- 1688 SKU 下架店铺别名过滤与预览诊断

## 当前剩余 blocker

- 真实“保存草稿”按钮还没有完成 live 选择器确认
- 真实“发布成功”后的 offer 链接/ID 还没有完成最终验证
- 自动提交模式还没有完成生产级验证
- 1688 下架页 live 选择器仍需继续联调确认
- 店铺名称与账号/Profile 映射已接入；执行机仍需维护真实 Profile 路径和账号键
- SQL Server 真实账号密码仍待提供
- 聚水潭系统链路仍属于后续阶段，不是当前 1688 主线 blocker

## 当前推荐下一步

1. 用测试店铺跑一次 `1688_sku_offline --mode execute`
2. 根据 live 页面修正下架选择器
3. 接入店铺映射和钉钉 webhook
4. 回到 `1688_direct` 继续验证草稿和提交
5. 再决定是否开启自动提交

## 关键文件

- [config/platforms/1688.json](D:/script_files/ERP/furniture-uploader/config/platforms/1688.json)
- [config/operator_config.local.json](D:/script_files/ERP/furniture-uploader/config/operator_config.local.json)
- [rpa/browser_rpa.py](D:/script_files/ERP/furniture-uploader/rpa/browser_rpa.py)
- [rpa/doctor.py](D:/script_files/ERP/furniture-uploader/rpa/doctor.py)
- [templates/1688_corner_table_smoke.csv](D:/script_files/ERP/furniture-uploader/templates/1688_corner_table_smoke.csv)
- [docs/PLATFORM_EXPERIENCE_KB.md](D:/script_files/ERP/furniture-uploader/docs/PLATFORM_EXPERIENCE_KB.md)

## 永久说明

新的人类同事或 AI 接手时，默认先读：

1. `docs/README.md`
2. `docs/PROJECT_MEMORY.md`
3. `docs/DIRECT_1688_PROGRESS.md`
4. `docs/DELIVERY_HANDOVER_2026-03-24.md`
5. `docs/AI_CONTINUITY_GUIDE.md`

## 2026-07-24 当前事实：下架/替换自动登录

- `1688_sku_offline` 和继承它配置的 `1688_sku_replace` 默认复用店铺 Profile；检测到登录失效后，会关闭 ERP Selenium 会话，调用共享 1688 项目的 `python -m src.cli login --account-key ... --shop-name ...`，再重新打开并校验管理页。
- 每个店铺每次执行最多自动登录 1 次。自 2026-07-28 起，该显式登录允许既有滑块 RPA 最多 4 次；未解决滑块、未知风控或技术异常记录并通知，只有真实店铺/`member_id` 不匹配停止对应店铺。
- 自动登录只使用共享 1688 项目的账号凭据引用和 Profile，不在 ERP 代码、命令输出、报告或通知中写入密码、Cookie、Token。
- 自动登录统计写入执行汇总：`auto_login_attempts`、`auto_login_success`、`auto_login_failed`。
- 该能力已完成本地代码和测试验证，尚未替代执行机上的真实过期 Profile canary；交付前必须验证“过期态 -> 自动登录 -> 店铺身份复核 -> 继续执行”。

## 2026-07-28 当前事实：下架双店并发

- 每个店铺仍是一个串行执行单元；同店商品批次、同商品多个 SKU 和失败重试不会并发。
- 不同店铺可由 `manage_1688_stop_sale_daily.py run --max-parallel-stores 2` 并行调度，参数范围为 `1..4`，默认 `1` 保持旧行为。
- 1688 浏览器锁已从全局锁改为 `account_key` 级锁：`artifacts/locks/ali1688_account_<account_key>.lock`。共享爬虫 Worker 与下架 pipeline 使用同一协议，且 Worker 持锁直到校验和自有浏览器清理完成。
- `app.crawler_task` 活跃任务门禁仅检查当前 `account_key`；近期下架审计门禁仅检查当前店铺。管理器自身仍保留单实例锁，禁止重复启动同一个日批管理器。
- 聚水潭阶段使用全局 `ali1688_jushuitan.lock` 保持单路执行；不同店铺的 1688 阶段可以并行，但聚水潭页面操作不能并行。
- 每个店铺继续使用独立 Profile、run id、批次日志、审计结果和异常汇总；最终管理器汇总按原始店铺顺序稳定输出。
- 开发机验证：共享运行时 Python `659/659`、ERP Python `339/339`、聚水潭 TypeScript `18/18`，`check` 和 `build` 均通过。`doctor` 为 `ok`，2026-07-28 正式数据只读 preview 去重后 117 条（乐畅 9、工莱 56、沃来 17、淘淘 35）。
- 当前尚未部署执行机，也未完成乐畅、工莱各 1 条双账号真实 Canary；上述结果不代表真实页面或业务验收完成。
## 2026-07-28 CTG0286 Draft Integrity Update

- The executor draft `6a635fcee4b0eda6ebbdf340` exposed an invalid forced-save path: title, main image, color, size, and logistics fields were cleared even though the draft request returned HTTP 200.
- Live read-only evidence confirmed that the 1688 color specification accepts direct Chinese text entry followed by Tab. It does not require an exact match in the standard color suggestion list.
- Development now blocks draft save before dispatch when title, main image, required detail images, color, or size is missing. Post-save refresh verification remains required.
- Development validation: listing tests `371/371`; title engine `89 passed, 3 subtests passed`.
- This is not executor or business acceptance. Deploy the clean commit without preserving the executor's temporary forced-save implementation, then repair only the existing CTG0286 draft and independently inspect it.

## 2026-07-28 CTG0286 Independent Draft Acceptance Hardening

- Independent saved-draft inspection now requires exact persisted specification values, not merely non-empty fields.
- For CTG028601N1416V01, the acceptance contract is color `胡桃色` and size `48/40/50`; stale values such as `红色100` fail review.
- Development validation after this change: listing tests `373/373`; doctor `status: ok` with the two pre-existing empty-selector warnings only.

## 2026-07-29 Source Lifecycle Gate

- Use `scripts/query_1688_listing_source.py` for the fresh pre-browser source check.
- Credentials stay in Windows Credential Manager under the listing-owned reference `YYDD/1688/database/listing-source`; never persist them in code, payloads, or evidence files.
- The script must resolve an installed SQL Server driver rather than assuming ODBC Driver 17 exists.
- A passed source row is only a prerequisite. It does not authorize a second CTG0286 draft or prove draft/Offer acceptance.

## 2026-07-28 CTG0286 Specification Commit Correction

- Enter was disproved on the real publish page: it left `胡桃色` in the resident input and did not create a committed specification item.
- Tab creates the non-resident item, exposes the image-plus/delete controls, and creates the next empty resident input.
- The implementation now uses Tab for both color and size and verifies only committed non-resident values. Full listing regression is `375/375`.

## 2026-07-30 Draft Field Persistence Fix (Code Complete, Live Pending)

- Root causes fixed in the working tree (uncommitted): full patch mode now carries the existing draft edit identity; main-image bridge upload advances per slot (0-3); pre-save and post-refresh checks require 4 main images; logistics dimensions require strict persisted-value match; independent inspection no longer accepts "reapply at submit" as draft acceptance.
- Local validation: listing `432/432`; title engine `89/89` (requires the declared `jieba` dependency installed); doctor `status: ok` with the two pre-existing empty-selector warnings only.
- The unique draft remains `6a69bee6e4b01cad1b297a52`; no new draft, no delete, no submit. Live repair and independent inspection are still pending browser-mutex clearance.
- Full status and next steps: `docs/handoff/1688_LISTING_DRAFT_FIELD_PERSISTENCE_HANDOFF_2026-07-30.md`.

## 2026-07-31 Canary Failure Fix: Draft Rebind + Saga Failure Terminal

- Canary dry-run (draft `6a69bee6e4b01cad1b297a52` rebind) created a new draft `6a6c3d68e4b0651576fe9905` because `--draft-id` was ignored in draft mode: `pending_draft_id` stayed empty, `expected_draft_id` was never set, and the full patch went out without edit identity.
- `run_1688_listing_task.py` now honors `--draft-id` in draft mode (must reference a known historical draft ID) and fails closed when draft mode would silently create a new draft for a task that already has one; authorized rebuild remains the only allowed new-draft path.
- Controlled failures (`PublishValidationError`, `ListingContractError`, `ImageAlbumFullError`) now record the saga as `failed_terminal` with `error_code`; unexpected exceptions still leave the saga in `prepared` for reconcile.
- Local validation: listing `564/564`; new entry tests `13/13`; doctor `status: ok`.

## 2026-07-31 Edit-Page TinyMCE Adaptation

- Canary rerun confirmed the draft rebind works (draft2offer edited `6a69bee6e4b01cad1b297a52`, no new draft), then `detail_images` timed out on selector `#tinyMCE-0`: the TinyMCE helpers hardcoded the editor ID, but the draft2offer edit page may initialize the detail editor with a different ID.
- `browser_rpa.py` now resolves the editor at runtime: `_detect_tinymce_editor_id` prefers the configured ID, then the first visible `tinyMCE-*` editor, then any `tinyMCE-*` editor; `_tinymce_ready` waits for any visible `iframe[id^='tinyMCE-'][id$='_ifr']`; `_write_tinymce_content`, `_draft_description_present`, and `_draft_description_image_count` are editor-ID agnostic.
- Local validation: listing `573/573`; doctor `status: ok`.

## 2026-08-01 TinyMCE Lazy-Init Probe Loop

- Canary R3/R4 proved the draft2offer edit page lazy-initializes the detail editor: R3 found `tinyMCE-0` in place, R4 found no `[id^='tinyMCE-']` editor at all within the wait window.
- `_ensure_old_tinymce_mode` is now a probe loop (default 60s / 2.5s poll, configurable via `editor_init_timeout_seconds` / `editor_init_poll_seconds`): fast-path when the editor is ready, otherwise poll for the editor or the old-mode toggle while scrolling the description module into view (`_trigger_description_lazy_load`) to trigger lazy loading.
- The previous silent return when no toggle exists is replaced by a clear `TimeoutException` ("lazy-load probe exhausted"), so optional steps skip via the standard timeout path instead of failing later with a hard write error.
- Local validation: listing `579/579`; doctor `status: ok`.

## 2026-08-01 Main-Image Pre-Save Repair

- Canary R6: `detail_images` passed; the pre-save gate failed with `main images are incomplete before save (1/4)`. Per-slot bridge readbacks had passed during upload, so the edit page appears to reset the primary-picture module back to the server-persisted draft state (1 image) before the save gate.
- `_repair_main_images_before_save` now re-uploads the local main images immediately before the save request when the pre-save count is below the configured minimum (default on; `repair_main_image_before_save` in draft_verification gates it). The post-repair engine state is recorded, so a bridge-write/SDK-state mismatch shows up in the failure context.
- The saga failure-terminal change proved itself in production: R6's PublishValidationError auto-recorded `failed_terminal` with the original error, no manual reconcile needed.
- Local validation: listing `585/585`; doctor `status: ok`.

## 2026-08-01 Adaptive Bridge Slots for Edit-Page Main Images

- Canary R7 evidence (`draft4-r7.failure-context.json`): the edit page held one server-persisted main image (`imageList` length 1), the bridge upload to slot 1 never landed (slots 1-3 do not exist until created), and the picker fallback then waited for a `cover-empty` slot that an occupied edit page does not render.
- The bridge upload is now slot-adaptive: `_ensure_primary_picture_bridge_slot` extends `imageList` with placeholder entries before writing; landing detection waits for a NEW remote URL anywhere in the list (robust to the component ignoring the requested slot index) and records `main_image_bridge_slot_mismatches` diagnostics.
- The pre-save repair from the previous round automatically inherits the adaptive upload.
- Local validation: listing `588/588`; doctor `status: ok`.

## 2026-08-04 SKU 下架隐藏校验分类

- 1688 编辑页可能在 SKU 已切换为下架后，通过隐藏校验阻止提交，例如 `毛重必须为数字`。这类结果不是下架成功，也不是整店安全故障。
- 提交未产生平台请求且能读取到明确平台提示时，执行器记录 `error_category=system_prompt`，并把平台原文写入 `page_error_text` 和 `system_prompt`。
- `system_prompt` 不重试、不停止整店；当前商品 ID/SKU 记录失败后继续下一商品。唯一在线 SKU 的专用分类仍优先于通用系统提示。
- 本地相关回归 `126/126` 通过；执行机部署和真实 Canary 仍需单独验证，不能用本地测试代替生产验收。

## 2026-08-06 下架中断恢复审计收口

- 中断恢复不能只结束 Run 和删除文件锁；对应 Item 必须从 `pending` 转为技术失败，Saga 必须从 `prepared` 转为 `failed_terminal` 并写入 `finished_at`。
- 恢复按 `run_id + operation_key + account_fencing_token` 做 CAS，并要求 Outbox 为零；1688 未成功时不创建聚水潭任务。
- 已完成一次旧版恢复的 Run 可以在校验原恢复 summary、原因和锁路径后幂等补齐 Item/Saga，不重复通知或页面操作。
- C 线聚焦回归为 `151 passed, 2 subtests passed`；真实毛重系统提示仍必须由独立页面 Canary 验收。
