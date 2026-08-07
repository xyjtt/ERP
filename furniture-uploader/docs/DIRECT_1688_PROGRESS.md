# DIRECT 1688 Progress

## 2026-08-07 Managed Update (Interrupted Child Recovery Classification)

- The interrupted child `daily_20260807_123003_304694_s02_b003` has 10 audit items. Its JSONL report proves one pre-toggle `sole_sku_requires_product_offline` business terminal; the other nine have no execution report.
- Recovery now preserves recorded failed evidence and blocks on any recorded success/already-offline action. The nine unrecorded items remain eligible only for exact interrupted-work recovery after child and manager closeout.
- Focused recovery tests pass. Production apply still requires the exact dead-owner lock, stale runtime request/lease CAS, matching run id and unchanged report evidence.

## 2026-08-06 Managed Update (Listing No Longer Requires Interactive Desktop)

- 根因已定位为 Listing 计划任务脚本强制 `Interactive + RunEx`，不是 Selenium、Edge、Credential Manager 或账号 Profile 的底层限制。
- 执行机正式 Crawler 现状证明 `Administrator/S4U` 在 Session 0 可运行 Python 和真实 Edge；独立 Listing S4U 探针进一步验证三个正式 Credential Manager 引用均可读。
- Listing Daily 已改为 S4U；无日触发的按需 Launcher 可直接启动 S4U 正式任务，并保留旧 Interactive 兼容分支。任务仍为 draft-only，已有 `offer_written_back` 候选只返回 `duplicate_existing`，不得再次保存或提交。

## 2026-08-05 Managed Update (Stop-Sale Recovery Code Complete)

- 中断日批 Manager 现在有正式 fail-closed 收口入口：校验锁归属、死 PID、child run 范围和终态后，先写 Summary，再二次校验锁快照，最后释放锁。
- P0 child 发现误判已修复：普通 pipeline log 不再进入 child 集合；明确的预审资源拒绝作为 orphan 证据写入 Manager Summary，未知日志仍失败关闭。
- 新恢复清单工具从原始输入和 child reports 逐条分类 `missing`、`technical`、`excluded`，只把前两类写入定向恢复 CSV，不会重跑业务终态或已完成的聚水潭项。
- 聚水潭 Outbox 已补齐逐项结果保留、精确 `already_cleared` 证据和单条 fenced CAS 重排；不允许把失败批次中的成功项重新归零，也不允许批量重排历史 11 条。
- 本地开发验收已通过：Python `616/616`；定向 `19/19`；扩展下架 `76/76`；TypeScript `26/26`、`check`、`build`；`compileall`、PowerShell 解析和差异检查通过。
- 当前节点不是生产完成：`daily_20260804_130814_970163` 仍须在执行机按操作手册完成收口，再重新生成清单核对此前 52 个 missing、7 个 technical 和历史 11 条 Outbox。禁止手删锁或广泛补跑。
- 正式执行顺序见 `docs/operations/2026-08-05_interrupted_stop_sale_recovery.md`。Crawler 是否仍在后台运行不构成本开发、提交或部署的统一前置条件；只在同账号租约或浏览器资源实际冲突时等待对应任务自然结束。

## 2026-07-28 Managed Update (Automatic Login Identity Gate)

- 下架/替换账号恢复改为显式 `--auto-solve-slider --slider-max-attempts 4 --verify-account-identity`，普通爬虫登录默认行为不变。
- 登录后新增 `expected_member_id + 店铺名` 双重核验；真实不匹配 fail closed，缺少/无法读取身份按技术失败记录，不伪造成功。
- `stop_store_on_error_categories` 默认仅保留 `store_mismatch`；页面超时、登录恢复失败、滑块未解决和浏览器异常均形成明细、审计和钉钉结果，并继续其他店铺。
- 开发验证已完成；下一节点是执行机 preflight 确认四店 `expected_member_id`，然后只执行替换 Preview 和一条合法旧 SKU -> 新 SKU Canary。Canary 通过前不执行全量替换。

## 2026-07-27 Managed Update (Stop-Sale Runtime Recovery Fixes)

- 已完成三项开发修复：每个 Pipeline 尝试前重申 Worker 暂停、启动保护失败形成审计/Summary/钉钉闭环、`automation_error` 重试前重建当前店铺浏览器。
- 已增加回归测试，覆盖 Worker 在两次尝试之间被外部重启、保护检查失败不启动 1688 子进程、分组结果异常和直接抛异常两种浏览器恢复路径。
- 开发机全量验证：Python `319 passed, 5 subtests passed`，聚水潭 `check`、`18/18` 测试和 `build` 全部通过。
- 现场根因不归因于 `1688-Watchdog`；该任务仅探测状态。真正的外部 Worker 启动源仍需在执行机任务历史和进程创建链中继续确认，但代码已能在每次尝试前安全收口。
- `daily_20260727_123002_977385` 的生产结果保持为未全量完成，不能因本次开发回归而改写为成功。
- 下一节点：提交并推送后，在无正在运行的业务任务时部署到执行机，执行 preflight、单店单商品真实 Canary，核对 1688 页面、聚水潭、审计表、Summary 和钉钉，再决定是否恢复每日全量任务。

## 2026-07-23 Managed Update (SKU Replacement Code Complete, Live Canary Pending)

- `1688_sku_replace` 已完成代码闭环：BI preview、分店/商品分组、旧货号改新货号、一次提交、提交后复核、日志和钉钉异常字段。
- 商品搜索边界统一：SKU 下架与 SKU 替换均使用商品管理“全部”Tab，不再依赖“销售中”。
- 聚水潭新增 `sync:1688`：替换后按店铺执行“手动同步商品 -> 按链接同步 -> 立即下载”，同商品多 SKU 只提交一个商品 ID，每批最多 50 个商品 ID。
- 新增完整 preview 流水线 `scripts/run_1688_sku_replace_pipeline.py` 和样本 `templates/1688_sku_replace_sample.csv`。
- 正式源 preview 已支持逐行拒绝：`2026-07-23` 加载 568 条，500 条可执行，42 条非 SKU 占位值拒绝，26 条重复；拒绝项写 CSV/JSON 并尝试钉钉通知。
- 正式验收库 `JSReportReplica.app.ali1688_sku_replace_run/item` 已创建并通过契约检查。
- 本地回归全部通过；下一节点是业务提供一条可真实替换的受控映射后，执行 1688 + 聚水潭单条 live canary。
## 2026-07-30 Managed Update (24-Hour Shipping Requirement)

- Buyer-protection shipping time is now `24小时发货` with platform code `essxsfh` across field entry, draft persistence, request patching, submit reapply, and reconciliation gates.
- The user's normal Edge session opens the target draft successfully. The dedicated automation profile still returns `SYS_ERROR`, so real repair/save/submit remains blocked on automation-session refresh rather than field selectors.

## 2026-07-29 Managed Update (Independent Draft Acceptance Result)

- User-authorized deletion removed the two corrupt historical CTG0286 drafts. One replacement draft was created: `6a69bee6e4b01cad1b297a52`.
- Product management was verified on `tab=all`: shop `木刻理想`, 4 total drafts, one target row, and the target row's official `offerDraftId` link.
- Request identity repair reached HTTP 200 with the expected ID, but independent persistence acceptance failed every business field. The official row link returns `SYS_ERROR`; startup network capture records no publish-page XHR/fetch before the error.
- Old and non-target draft controls also return `SYS_ERROR`, so deleting/recreating the target again is not justified.
- Code now rejects saved `operator=new` URLs for repair execution and post-save acceptance. Both repair and verification reopen through the official `draft2offer` entry and fail closed on `SYS_ERROR`.
- Development verification passes: listing `421/421`, title engine `89 passed, 3 subtests passed`, 12 JSON files valid, and `git diff --check` clean.
- Formal audit is corrected to `blocked/rejected` with no Offer. Approval, submit, writeback, and executor deployment remain pending platform recovery and a passed independent inspection.

## 2026-07-29 Managed Update (Existing-Draft Recovery Guard)

- Replaced the synthetic `offer-new ... draftId=` repair URL with the platform's formal `fillProductInfo ... offerDraftId=` entry.
- Added three identity gates: verify the loaded page ID, require the outgoing `draftSubmit` request to carry the expected ID, and reject a response with a missing or different draft ID.
- Explicit review repair may rebind only to a draft ID already recorded by a historical `draft_saved` event. CTG0286 therefore permits the two known IDs only and prohibits a third draft.
- Independent inspection can target a specified historical draft and now emits a structured `unavailable` report plus screenshot when the publish runtime does not load.
- The management-page probe now preserves row text, links, `href` values, `data-*` attributes, and extracted draft identifiers from the draft tab.
- Current live finding: the draft box has 5 rows and contains both historical IDs. The old row's real “继续发布商品” click opens the correct ID but still renders `SYS_ERROR`, so the form cannot be inspected or repaired.
- Development gates pass: listing `400/400`; title engine `89 passed, 3 subtests passed`; doctor `ok`; Python compile and 12 JSON files valid. No real save, approval, submit, Offer, or writeback was completed by this result.
- Current blocker requires platform recovery or an explicit human policy decision. Creating a third draft, deleting either historical draft, approving without refreshed field evidence, and blind submit remain prohibited.

## 2026-07-28 Managed Update (CTG0286 Pre-Save Repair)

- Reproduced a real draft-save blocker after all image batches completed: the page had re-rendered with an empty title, empty committed specs, no visible main image, and empty logistics inputs.
- Added an idempotent pre-save repair order: main image, committed specs, title, then price/inventory/description state patch.
- Specification reads now ignore the resident editor input and accept only `.value-select-item:not(.resident) input` values; Tab remains the confirmed commit key.
- Save is blocked unless title/specs exactly match the task payload, the configured square-main-image requirement passes, and the description contains every uploaded detail URL.
- Complete contiguous detail-upload checkpoints are reusable, so the next repair can rebuild the description from all 48 recorded CDN URLs without uploading them again.
- Development verification passed `380/380`. Executor deployment, one guarded reuse of draft `6a635fcee4b0eda6ebbdf340`, read-only inspection, and formal audit remain pending.

## 2026-03-31 Managed Update (T-005 Draft System-Error Isolation Round-2)

- 继续只执行 `T-005`（仅 `draft`），`T-001` 冻结。
- 针对 `draftSubmit` 连续 `系统错误，请稍后尝试` 增加第二轮修复：
  - 引入 `draft_request_patch_retry_modes`，默认 `full -> capture_only`；
  - 当 draft backend reject 时，重试自动降级为“仅抓包不改包”模式；
  - run_report 补充重试轨迹与补丁模式字段，支持逐条复核失败原因。
- 配置已同步：
  - `config/platforms/1688.json` 与 `config/platforms/1688.local.json` 已启用该策略；
  - 本地重试退避参数上调（`retry_count=8` + 更长 backoff）。
- 回归验证：
  - `python -X utf8 -m unittest discover -s tests -p "test_*.py"` -> `140/140`；
  - `doctor` -> `status: ok`（保留既有 warning）；
  - `product/variant` `validate-only` 均通过。
- 当前状态：
  - 代码侧已补齐隔离与观测能力；
  - `50 连续草稿成功` 仍待 live 会话继续压测完成。

## 2026-03-31 Managed Update (T-005 Fast Iteration Optimization)

- 提速修复：
  - 本地 `main_image` 步骤切换为 React bridge 上传，绕开 picker 打开超时；
  - `draft_submit_backend_reject_fast_fail_count` 已启用（本地阈值=3），连续 backend reject 时快速结束本轮。
- 实跑结果：
  - `20260331_091725.jsonl`：`main_image` timeout（旧路径）；
  - `20260331_092030.jsonl`：主流程已推进至 draft submit，确认 platform `system error`；
  - `20260331_093204.jsonl`：快速失败触发（3/3），并记录补丁模式历史 `['full','capture_only']`。
- 当前判断：
  - 代码流程已可稳定到达草稿保存阶段；
  - 连续成功链仍受平台接口返回 `system error` 阻塞。

## 2026-03-30 Managed Update (T-005 Draft-Only Execution Start)

- 执行优先级切换完成：
  - 当前主任务为 `T-005`（仅 `draft`）；
  - `T-001` 下架链路已冻结，避免并行修改冲突。
- 任务治理已落地：
  - [D:\script_files\TASK_BOARD.md](D:/script_files/TASK_BOARD.md) 已标记 `T-005 in_progress`
  - [D:\script_files\HANDOFF.md](D:/script_files/HANDOFF.md) 已更新当前阶段
  - 新增任务包 [D:\script_files\TASK_PACKAGE_T-005.md](D:/script_files/TASK_PACKAGE_T-005.md)
- 启动器界面中文化已完成：
  - 参数标签中文化；
  - `input-mode` 选项中文显示，命令参数保持英文兼容映射；
  - 文件类型与错误提示中文化。
- 本地与运行验证：
  - `python -X utf8 -m unittest discover -s tests -p "test_*.py"` -> `129/129` 通过；
  - `doctor` -> `status: ok`（保留两条既有 warning）；
  - 最新 draft 实跑：`logs/run_reports/20260330_185234.jsonl` -> `status=success`。
- 当前阶段目标不变：
  - 按任务包推进到“连续 50 条草稿成功”验收。

## 2026-03-27 Managed Update (P15 Runtime Patches)

- 继续推进 `P15` 阻塞修复（代码已落地，待 live 回归确认）：
  - 为草稿页面状态补丁增加 `deliveryServiceIds` 写入，强制同步 `customExtraService.customServices` 与 `viewModelMap` 的“配送服务”值；
  - 为 `draftSubmit` 请求重写补丁增加配送服务快照与注入，避免请求体回落成 `customServices=[]`；
  - 增强运行时采集：`draft_delivery_service_state` 现在记录 `selectedLabels/selectedServiceIds`，供请求补丁优先使用真实勾选值。
- 类目页稳定性补强：
  - 新增类目确认按钮多选择器兜底点击；
  - `select.htm -> publish.htm` 回跳超时后增加已存在发布页窗口接管兜底，减少偶发卡在 `select.htm`。
- 测试状态：
  - `python -X utf8 -m unittest discover -s tests` 通过，当前 `115/115`。

## 2026-03-26 Managed Update (Direct Publish Hardening)

- `1688_direct` flow hardened in runtime:
  - category-confirm now supports new-tab publish navigation and category-target matching;
  - category option matching now only clicks visible cascader options;
  - optional category-prop/spec rules now skip safely when field is missing (required rules still fail);
  - quantity step now has SKU-table fallback when `#guid-totalSales` is hidden;
  - main-image step now tolerates existing-main-image state when picker cannot be reopened.
- Draft reliability improved:
  - draft submit trace now returns explicit `present/complete` state;
  - added dispatch-event retry click when first draft click does not emit a request;
  - added pre-draft send-address auto-pick and delivery-service auto-check attempt;
  - draft failures now surface true request-trace status instead of silent false pass.
- Submit pipeline capability completed in code:
  - added submit request trace patch/assert (`/popular/submit.htm` + `/popular/draftSubmit.htm`);
  - added submit retry-on-no-trace and optional `submit_verification` gate;
  - run-report payload now includes submit trace + post-submit verification fields.
- Latest live reports (direct publish smoke on logged-in 9224 session):
  - `logs/run_reports/20260326_230705.jsonl`
  - `logs/run_reports/20260326_232024.jsonl`
  - `logs/run_reports/20260326_232754.jsonl`
- Current blocker (P15 live closeout):
  - after draft save + refresh, page still reports `配送服务为必填项` and `买家保障第1行发货时间为必填项` in some runs;
  - submit-mode code path is implemented, but direct live submit verification is gated by the above draft persistence issue.

## 2026-03-26 Managed Update (Submit Unblocked)

- `1688_sku_offline` submit path was hardened:
  - added stable pre-submit switch-state verification;
  - added runtime `skuTable` target-SKU offline patch verification;
  - added submit network trace capture for `/popular/submit.htm` and `/popular/draftSubmit.htm`;
  - added dispatch-event retry click when first submit click does not trigger request.
- Live execute verification on logged-in session:
  - success report: `logs/sku_offline/run_reports/20260326_211051.jsonl` (SKU `DNZ023901N1221V01`);
  - regression replay success: `logs/sku_offline/run_reports/20260326_211644.jsonl` (SKU `YJT000802N961V01`).
- Additional regression:
  - full failed-sample replay: `logs/sku_offline/run_reports/20260326_212944.jsonl` (`already_offline=2`, `failed=1`);
  - remaining failed case: `973480751525 / DNZ005372N965V01` (`submit_request_trace_present=true`, but post-submit still online).
- Current blocker status:
  - the prior `submitted but still online` issue is no longer reproducible on multiple live cases, but still exists on specific SKU `DNZ005372N965V01`.

## 2026-03-26 Managed Update

- Improved execute stability for `1688_sku_offline`:
  - stale-element retry now handles webdriver stale-message variants;
  - required delivery-service auto-fill was added before submit;
  - richer SKU-not-found classification (`sku_not_found`) retained.
- Added candidate export workflow:
  - script: `scripts/export_online_sku_candidates.py`
  - generated candidate table: `templates/1688_sku_offline_online_candidates_20260326.csv` (from local snapshots).
- Live execution status:
  - login/session issue resolved;
  - current blocker remains `submit_failed` on some products (`submitted but still online` after verification), pending deeper submit-state diagnosis.

更新时间：2026-03-26

## 项目目标

先交付一个可落地的 1688 自动化项目：

- 数据源：`Excel / CSV`
- 渠道：`1688`
- 执行方式：`Selenium`
- 当前策略：`自动填表/执行 + 保守人工确认`

## 进度表

| 编号 | 阶段 | 内容 | 状态 | 备注 |
|---|---|---|---|---|
| P1 | 项目骨架 | 复用 `furniture-uploader` 作为 1688 主仓 | 已完成 | 不新开仓库 |
| P2 | 执行入口 | 增加 `1688_direct` system | 已完成 | 主程序可识别 |
| P3 | 数据链路 | 模板读取、校验、运行入口打通 | 已完成 | `validate-only` 正常 |
| P4 | 文档基线 | 建立 1688 主线文档 | 已完成 | 已形成可交接文档 |
| P5 | 页面理解 | 依据 `1688.docx` 和 live 页面梳理步骤 | 已完成 | 已沉淀到知识库 |
| P6 | 会话接管 | 支持接管已登录浏览器 | 已完成 | 支持 `debugger_address` |
| P7 | selector 采集 | 1688 关键页面 selector 捕获 | 已完成 | 当前发布页可用 |
| P8 | 配置补齐 | 1688 核心字段 selector 补齐 | 已完成 | 基线已可运行 |
| P9 | smoke 联调 | 核心字段 live smoke | 已完成 | 文本/图片/详情已通 |
| P10 | 类目自动化 | 按类目路径自动选择 | 已完成 | live 验证通过 |
| P11 | 结果提取 | 成功结果抽取框架 | 已完成 | 支持 URL/页面文本/源码 |
| P12 | 容错增强 | 可选属性缺失自动跳过 | 已完成 | 不阻塞主流程 |
| P13 | 干跑闭环 | `--limit 1 --skip-login` 真实干跑 | 已完成 | `success_count = 1` |
| P14 | 草稿能力 | 支持保存草稿模式 | 已完成 | 已进代码，待 live 按钮确认 |
| P15 | 真实草稿验证 | 真实点击“保存草稿”并确认结果 | 进行中 | 缺 live 按钮选择器 |
| P16 | 真实提交验证 | 真实提交并确认成功结果 | 未开始 | 自动提交前置条件 |
| P17 | 生产收口 | 自动提交、监控、回写策略 | 未开始 | 最后阶段 |
| O1 | SKU 下架骨架 | Excel 读取、筛选、去重、预览、扫描入口 | 已完成 | `1688_sku_offline` 已进代码 |
| O2 | SKU 下架执行 | 商品搜索、修改详情、SKU 下架、提交保存 | 进行中 | 已补店铺名规范化过滤与预览诊断，继续收口 live 提交 |
| O3 | SKU 下架批处理 | 多店铺映射、钉钉通知、定时无人值守 | 进行中 | 已接入共享账号自动登录，待执行机过期 Profile canary |

## 当前节点

当前做到：

- 上架主线：`P15 真实草稿验证前`
- 下架主线：`O2 live 联调前`
- 下架无人值守：`O3 执行机自动登录 canary 前`

也就是：

- 主流程已经能稳定填完页面
- 默认会停在人工复核节点
- 草稿模式代码已经具备
- 真正缺的是草稿按钮在 live 页面上的最终选择器确认

## 最近里程碑

- `cc9062a` `feat: add 1688 category automation and result extraction`
- `803a2b2` `feat: add 1688 dry-run smoke coverage`
- `2fa6566` `feat: add draft-save publish mode support`
- `2026-03-26` `feat: normalize sku-offline store matching and add preview filter diagnostics`

## 下一步

1. 抓取真实草稿按钮选择器
2. 开启 `auto_save_draft`
3. 验证草稿保存结果
4. 再做一次真实提交验证
5. 用测试店铺验证 `1688_sku_offline` 提交闭环

## 2026-07-24 自动登录里程碑

- 下架和 SKU 替换的生产 pipeline 已透传 `--shared-runtime-root`。
- 1688 登录失效不再要求运营手动登录：默认执行一次账号级自动登录，成功后重新创建浏览器会话并复核店铺。
- 该 2026-07-24 边界已由 2026-07-28 更新：仅已识别滑块允许受限自动处理；未知验证和技术异常记录通知，真实店铺/`member_id` 不匹配才停止对应店铺。
- 人工调试仍可显式使用 `--require-manual-login`。
- 本地验证：ERP `293 passed, 5 subtests passed`；真实执行机过期 Profile canary 尚未完成，不把本地结果视为线上验收。

## 2026-07-28 双店并发里程碑

- `manage_1688_stop_sale_daily.py` 已增加 `--max-parallel-stores`，默认 1、首轮目标 2、硬上限 4。
- 并发粒度是店铺：不同账号的 1688 浏览器可并行；同店批次、同商品多个 SKU 和重试保持串行。
- 浏览器互斥改为 `account_key` 级锁，共享爬虫 Worker 和下架 pipeline 共用 `ali1688_account_<account_key>.lock`。
- `crawler_task` 门禁按当前账号过滤，近期下架审计按当前店铺过滤；无关账号不再阻塞本店。
- 聚水潭通过全局 `ali1688_jushuitan.lock` 保持单路执行；管理器全局单实例锁继续保留。
- 回归结果：共享 Python `659/659`，ERP Python `339/339`，聚水潭 `check`、`18/18`、`build` 全部通过。
- 开发机只读验收：`doctor=ok`；2026-07-28 preview 为 127 条加载、10 条重复、117 条选中，四店分布 9/56/17/35。
- 当前节点：代码与开发机自动化验证完成；执行机部署、乐畅/工莱各 1 条双账号真实 Canary 和正式批次尚未完成。
## 2026-07-28 Development Update: Required Draft Fields

- Replaced exact dropdown color matching with direct Chinese text entry plus Tab and committed-item verification.
- Color and size are both required and are checked before draft save and after refresh.
- Draft save is also blocked before dispatch when title, main image, or the configured minimum detail-image count is missing.
- Current development verification: listing `371/371`; title engine `89 passed, 3 subtests passed`; compile and JSON checks passed.
- Remaining work: controlled executor deployment, fresh browser mutex/capacity/source checks, repair of draft `6a635fcee4b0eda6ebbdf340`, and independent real-page inspection. No submit is authorized by this development result.

## 2026-07-28 CTG0286 Independent Inspection Gate

- `inspect_1688_saved_draft.py` now compares persisted color and size with the payload's exact expected values.
- A non-empty but wrong color or size no longer passes the post-save independent review.
- Local regression is `373/373`; live draft repair and real-page inspection remain required before business acceptance.

## 2026-07-28 Live Specification Commit Correction

- A real CTG0286 page probe proved that Enter leaves text in the resident input and does not create a SKU specification item.
- Direct Chinese color and size values must be committed with Tab; verification now ignores resident input text and accepts only non-resident committed items.
- Full listing regression after the correction is `375/375`. Real draft repair and refreshed-page inspection remain required.

## 2026-07-29 Listing Source Gate Compatibility

- Added the version-controlled read-only source gate `scripts/query_1688_listing_source.py`.
- The gate uses Windows Credential Manager and selects an installed SQL Server driver, so both executor ODBC 17 and development Native Client 10 are supported.
- Real CTG028601N1416V01 source checks passed on both machines with `enabled=1`, `stock_disabled=0`, `other_5=销售`, and `item_type=成品`.
- Listing regression is `382/382`; title engine remains `89 passed, 3 subtests passed`. CTG0286 draft repair and independent page inspection are still pending browser-mutex clearance.

## 2026-07-30 Draft Field Persistence Fix (Code Complete, Live Pending)

- Fix round for the CTG0286 draft fields that returned HTTP 200 but did not persist (main images 1/4, 24-hour shipping, send address, logistics dimensions).
- `1688.json`: retry modes `full -> identity_only`, `minimum_main_image_count=4`, strict logistics persistence with required field sources, empty `submit_reapply_nonpersistent_fields`.
- `browser_rpa.py`: full mode injects draft edit identity when `expected_draft_id` exists; bridge upload advances per slot; pre-save/post-refresh main-image count gates; strict logistics value match after refresh.
- `inspect_1688_saved_draft.py`: real-field acceptance for main image count, send address, and `24小时发货/essxsfh`; reapply-record fallback removed.
- Local validation: listing `432/432`; title engine `89/89`; doctor `status: ok`.
- Not done: live draft repair, independent saved-draft inspection, approval, submit, executor deployment. See `docs/handoff/1688_LISTING_DRAFT_FIELD_PERSISTENCE_HANDOFF_2026-07-30.md`.

## 2026-07-31 Canary Failure Fix: Draft Rebind + Saga Failure Terminal

- Root cause of the new-draft canary failure: `--draft-id` was parsed but never consumed in draft mode; the execution fell back to an `operator=new` publish URL with no `expected_draft_id`, so the full patch carried no edit identity and 1688 created draft `6a6c3d68e4b0651576fe9905`.
- Fix 1: draft mode rebinds only known historical draft IDs via `--draft-id`, and fails closed instead of creating a duplicate draft when history exists but no `pending_draft_id` is set.
- Fix 2: controlled failures write the saga `failed_terminal` state with `error_code` (`record_ali1688_result`), so retries no longer require manual reconcile; uncontrolled exceptions still require reconcile.
- Local validation: listing `564/564`; doctor `status: ok`. Real canary rerun and independent inspection remain with the main session.

## 2026-07-31 Edit-Page TinyMCE Adaptation

- Fix for the canary `detail_images` timeout (`selector=#tinyMCE-0`) on the draft2offer edit page.
- Runtime editor detection replaces the hardcoded `tinyMCE-0` ID across readiness wait, content write, and post-save description checks.
- 9 new helper tests; full listing regression `573/573`; doctor `status: ok`.
- Saga from this canary stays `prepared` by design (uncontrolled TimeoutException); the main session reconciles before the next rerun.

## 2026-08-01 TinyMCE Lazy-Init Probe Loop

- Fix for the R4 canary timing flake: the draft2offer edit page lazy-initializes the detail editor, so a single selector wait is unreliable.
- `_ensure_old_tinymce_mode` probes for editor-or-toggle while triggering lazy load via scroll; `_tinymce_ready` accepts a per-call timeout override.
- 6 new probe-loop tests; full listing regression `579/579`; doctor `status: ok`.

## 2026-08-01 Main-Image Pre-Save Repair

- Fix for the R6 canary `main images are incomplete before save (1/4)` failure on the draft2offer edit page.
- Pre-save gate now re-uploads local main images once when the count is below minimum, right before the save request; repair evidence (uploaded URLs, post-repair state, skip/error reasons) is written into the run context.
- 6 new repair tests; full listing regression `585/585`; doctor `status: ok`.

## 2026-08-01 Adaptive Bridge Slots for Edit-Page Main Images

- Fix for the R7 canary `Step 'main_image' timed out` (cover-empty XPath) on the draft2offer edit page.
- Root cause from the R7 failure context: edit-page `imageList` only materializes persisted entries (length 1), so writes to slots 1-3 could not land; the picker fallback's empty-slot opener does not exist on an occupied edit page.
- Bridge upload now extends missing slots with placeholders and detects landing by new remote URL (any slot), recording slot mismatches.
- 4 updated/new bridge tests; full listing regression `588/588`; doctor `status: ok`.

## 2026-08-04 Stop-Sale System Prompt Handling

- Development now classifies explicit submit-blocking platform text such as `毛重必须为数字` as `system_prompt` / `系统提示`.
- The affected product ID/SKU is recorded as failed and skipped; execution continues with the next item in the same store.
- This category is non-retryable and does not stop the store. It is not counted as an offline success and does not create a Jushuitan handoff.
- Targeted behavior tests and the related stop-sale suite pass `126/126`. Executor deployment and a real hidden-validation Canary remain pending.

## 2026-08-06 Interrupted Stop-Sale Recovery Closure

- Recovery now terminalizes the exact pending Item and prepared Saga together with the failed Run; a failed 1688 action creates no Jushuitan Outbox.
- CAS scope includes run ID, operation key, and account fencing token. A changed identity, terminal state, or unexpected Outbox fails closed.
- Legacy partial recovery can be rerun idempotently only when its persisted recovery summary proves the same reason and exact removed lock.
- Focused C-line regression: `151 passed, 2 subtests passed`. Real hidden-validation Canary remains a separate production acceptance step.
# 2026-08-05 下架恢复安全补强

- 中断 Manager 锁改为 owner/token 不可误删的 Windows 原子释放。
- 59 项恢复范围必须精确匹配批准的 52 missing + 7 technical 身份集合和哈希。
- 聚水潭已清除判断改为四列精确匹配，Outbox 改为批准 operation_key 集合原子领取。
- 当前仅完成开发侧修复；执行机部署和生产恢复仍未执行。

## 2026-08-07 Stop-Sale Runtime Recovery Closeout

- Added `scripts/recover_expired_stop_sale_runtime.py` for exact-scope recovery of one expired Stop-Sale runtime owner. Scope is fixed by `account_key`, `run_id`, `request_key`, host, and either the complete account/browser resource set or an explicitly proven empty resource set for request-only recovery.
- Preview now produces a fingerprinted version 4 snapshot with an explicit `recovery_mode`. A complete target resource set uses `request_and_resources`; an empty target set may use `request_only` only when the request is expired, the owner PID is dead, no same-account Crawler/request/Stop-Sale work or foreign lease exists, and all official procedures are present. A partial or unknown resource set remains blocked.
- Apply requires a saved snapshot that was already `eligible=true` plus `--yes`; eligibility, PID liveness, recovery mode, and resource absence are fingerprinted. It repeats the live snapshot check, recovers browser slot before account lease for the complete-resource mode, and uses request CAS plus completion for both modes. The transaction rechecks related leases before request takeover and verifies `cancelled + completed_at + leases=0` again before commit; any mismatch rolls back. A blocked or version 3 Preview cannot be reused after conditions change.
- Post-apply verification requires the new owner, Stop-Sale request identity, `status=cancelled`, a populated `completed_at`, and zero related runtime leases. Configuration failures also produce a structured `blocked` artifact instead of an unstructured traceback.
- Final local validation: full `furniture-uploader` regression `734/734`; C-line Stop-Sale/replacement focus `324/324`; recovery utility `29/29`; exact system-prompt plus `combination_sku` regression `6/6` (`2 + 4`); `compileall` and `git diff --check` passed.
- This development closeout does not claim executor or production acceptance. No production database, lock, task, browser, Preview, or Apply was touched in this change.

## 2026-08-07 All-Shop Store Mapping Expansion

- Stop-Sale and SKU Replace now share 17 authoritative `store_accounts`: four existing mappings plus 13 uniquely derived mappings.
- The sync tool fails closed unless external accounts, task roster, member/profile identity, source-store Preview, and the checked-in blocker policy all agree.
- `pingcan`, `pingcan_rpa`, and `xinbaiguang_shanzhu` remain excluded for explicit identity/Jushuitan gaps. No Jushuitan name was inferred.
- This is deployment-ready configuration, not full-shop production acceptance; all 17 configured accounts were `waiting_auth` in the source snapshot.

## 2026-08-07 Executor Marker Race Fix

- The first executor closeout attempt reached the updated code but its `747`-case Python regression had one Windows-only `PermissionError` while the saved-draft launcher replaced `.launcher.json`.
- The wrapper now replaces an existing marker with same-volume `System.IO.File.Replace` instead of `Move-Item -Force`. Focused regression is `3/3`; a 12-run stress loop is `12/12`.
- Deployment was rolled back by the guarded script after the failed test. The next deployment must target `eb69091` plus this fix and repeat the executor full regression before tasks are re-enabled.

## 2026-08-07 Exact Interrupted Stop-Sale Recovery Gate

- Added an execute-only approval path for an exact set of interrupted Stop-Sale operation keys. Approval binds source run, new recovery run, account, input hash, fixed failure contract, count, and key set.
- Saga preparation is all-or-nothing under SERIALIZABLE locking and rejects any Outbox, successful historical Item, non-interrupted source Item, business-key drift, or state drift before page execution.
- Exact recovery is fail-fast: standard account lock only, no custom/disabled lock, no interactive login, notifications enabled, and all lock/lease/crawler/Jushuitan waits set to zero.
- Development validation: focused `59/59`, full Python `765/765`, Jushuitan `28/28`, TypeScript `check/build`, `compileall`, and `doctor=ok` with two pre-existing warnings.
- Status remains development-only. No executor deployment or production recovery has run.
