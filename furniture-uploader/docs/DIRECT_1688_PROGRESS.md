# DIRECT 1688 Progress

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
| O3 | SKU 下架批处理 | 多店铺映射、钉钉通知、定时无人值守 | 未开始 | 依赖会话映射 |

## 当前节点

当前做到：

- 上架主线：`P15 真实草稿验证前`
- 下架主线：`O2 live 联调前`

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
