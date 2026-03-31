# Project Memory

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
- 店铺名称与实际登录店铺的自动映射尚未接入
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
