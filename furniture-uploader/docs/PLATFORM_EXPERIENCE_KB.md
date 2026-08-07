# Platform Experience Knowledge Base

## 2026-08-06 S4U Browser Runtime Findings

- Edge 是否能运行不能用 `explorer.exe` 判断。执行机 `Administrator/S4U` Worker 已在 Session 0 启动独立 Profile Edge、renderer 和 CDP，并完成真实爬虫任务。
- Windows Credential Manager 也不能仅凭 SSH 的 `winerror=1312` 判定不可用；同一用户的 Task Scheduler S4U token 已实测可读取三个 Listing 正式引用。SSH 网络登录和 S4U 批处理登录是不同安全上下文。
- Listing 原阻塞来自 Launcher 的代码门禁：它只接受 SessionId > 0 并强制 RunEx。浏览器会话 helper 本身复用 Crawler 的 S4U 兼容实现。
- S4U 不提供可见桌面，但 CDP/Selenium 自动化不要求可见窗口；验证码、身份错配和未知风控仍必须失败关闭。

## 2026-08-05 Interrupted Manager and Jushuitan Recovery Findings

- Manager 进程退出和计划任务变为 Ready 都不是日批收口证据。必须同时核对管理器锁、锁 PID、Manager run id、child run 范围、数据库终态和 child Summary。
- Manager 生成的每次尝试日志不是 child 身份真源。只有正式 pipeline Summary/report 才参与 child scope；`higher_priority_browser_write` 预审拒绝在确认无 Summary、无数据库行后记录为 orphan，其他未知日志必须阻塞。
- 释放孤儿 Manager 锁之前必须先持久化可审计 Summary，再重新读取完整锁快照。只校验文件仍存在或 token 字符串不足以防止新 owner 接管；任何快照漂移都应保留锁并失败关闭。
- 恢复范围必须从原始 Preview CSV 与 child reports 做四字段身份匹配。未执行、技术失败、业务终态、1688 已完成和聚水潭已完成不能混为一个“失败列表”。
- 聚水潭搜索零行只有在商品 ID、线上 SKU 输入值精确回读且页面明确显示零行时，才能证明目标链接已不存在。存在同店铺/商品/SKU 的 sibling 行但目标平台编码不存在，也可形成 `already_cleared` 证据；普通 `task_not_found` 仍失败关闭。
- Outbox 重排必须是一条 operation 的 compare-and-swap，至少绑定旧 status、error code、attempt count、run id 和 Saga state。批量 UPDATE 或先读取后无条件写入会覆盖并发恢复结果。
- 批次子进程返回非零时，Outbox Worker 仍应逐项保留报告中已验证成功的 operation；不能把同批次所有 claim 一律回退为失败。

## 2026-07-28 Bounded Slider and Identity Findings

- 账号独立 Profile 登录恢复必须由业务调用方显式开启，不能改变普通爬虫登录的默认行为。现行命令同时携带 `--auto-solve-slider`、`--slider-max-attempts 4` 和 `--verify-account-identity`。
- 现有 `SliderCaptchaHandler` 只在确认滑块容器后使用；最多 4 次。短信、扫码、处罚页和未知风控不做坐标兜底或无限重试。
- 滑块消失不是登录成功。必须等待真实登录成功，再从工作台链接和页面上下文取得唯一 `member_id`，与外置 `accounts.json.expected_member_id` 和目标店铺一起核对。
- `shop_mismatch`/`member_id_mismatch` 是安全停店条件；`member_id_missing`、`member_id_ambiguous`、登录超时、页面超时和 renderer 异常属于可观测技术失败，应写明细、审计、钉钉并继续其他店铺。
- `browser_window_closed`、`management_search_timeout` 和通用 `automation_error` 在重试前都要关闭当前代码拥有的浏览器并重开相同 Profile，不能继续复用可能损坏的 renderer；重建仍失败后再落异常终态。

## 2026-07-27 Long-Running Stop-Sale Recovery Findings

- 本节的整机暂停 Worker 是历史恢复策略；2026-07-28 起由共享 `account_key` 锁和账号级 `crawler_task` 门禁取代。
- 长时间每日下架不能假设 Crawler Worker 在外层上下文中一直保持 Disabled。其他计划任务或人工操作可能在中途重新启用它，因此每个浏览器批次尝试前都要重新检查计划任务状态、Worker 进程和 Profile Edge 进程。
- 重申暂停时只允许操作指定的 `YYDD-1688-Crawler-Worker` 任务并等待其拥有的进程自然退出；未知 Profile Edge 必须等待或安全失败，不能通过全局结束 Python/Edge/Node 兜底。
- Worker 或活动爬虫保护失败属于“批次已创建但未执行”的终态，应写 `not_attempted`、Pipeline Summary、审计结束状态和钉钉通知。仅打印异常或只发管理器总通知不足以追踪单批次。
- `Timed out receiving message from renderer`、通用 selector 超时等 `automation_error` 可能表示当前 Edge renderer 已损坏。继续在同一个浏览器对象内重试价值很低；应关闭当前代码拥有的店铺浏览器、重新打开相同 Profile、校验会话后再重试。
- 浏览器重建仅适用于可重试自动化异常。`login_required` 使用账户级自动登录恢复；受限滑块未解决和其他风控记录并通知，只有真实店铺/账号不匹配停止对应店铺。`sole_sku_requires_product_offline`、`product_unavailable` 等业务终态只记录和通知。
- 2026-07-27 现场排查已明确：`1688-Watchdog` 只上报 CDP/爬虫状态，不会启动 Crawler Worker，不能把它写成事故根因。

## 2026-07-23 SKU Replacement Findings

- 商品管理入口对下架和替换都必须归一化为 `tab=all`，并在页面加载后显式确认“全部”Tab；“销售中”会漏掉已下架、审核中或其他状态商品。
- `tab=all` URL 不能作为最终证据：商品管理内容在 iframe/SPA 内渲染，搜索前必须在 iframe 中确认“全部”具有 active/aria-selected 状态；无法确认时按 `management_tab_mismatch` 停止整个店铺并告警。
- 下架和替换 Pipeline 默认不等待共享锁；重复启动会立即失败并返回占锁状态。运行日志必须保留完整前台输出，不允许通过 `head` 截断后再次启动同一批次。
- SKU 单品货号位于发布页运行时 `SellPublishSdk.engine.getJsonState().components.skuTable` 的 `sku_cargoNumber`；受控写入使用 `core.changeElementValue('skuTable', nextValues, {isDepth:false})`。
- 同商品多 SKU 应先做整组冲突校验，再一次性修改 `skuTable`、一次提交，最后重新打开商品编辑页逐项复核新货号。
- 幂等判断：旧货号不存在且新货号已存在时记为 `already_replaced`；新货号属于未参与映射的其他行时记为 `replacement_sku_conflict`，禁止提交。
- 聚水潭替换后不是“清除链接”，而是“手动同步商品 -> 按链接同步”；按店铺填写去重商品 ID，每批限制 50 个，并为每个原始 SKU 保留独立结果。

## 2026-07-24 Live Management Tab Probe

- 开发机使用工莱真实 Profile 打开 1688 商品管理 iframe，未触发自动登录。
- 探针先将真实页面切到 `销售中(1104)`，再调用统一的 Tab 校验逻辑；运行上下文记录 `management_products_tab_click=all`，最终 DOM 同时满足“全部”按钮 `aria-selected=true` 和父节点 `ant-tabs-tab-active`。
- 本探针只切换商品列表 Tab，没有搜索商品、修改 SKU 或提交发布。
- 后续只读商品探针出现过 iframe 超过 10 秒才渲染 Tab 的情况；生产默认等待已调整为 30 秒，超时后仍按 `management_tab_mismatch` fail-closed。

## 2026-07-24 Duplicate Barcode Offline Rule

- 同一商品 ID 内允许多个规格行使用完全相同的单品货号/条形码。停产输入仍按商品 ID + 条形码去重，但编辑页必须定位并下架该条形码的全部匹配行，只提交一次。
- 每切换一行后重新扫描当前 React DOM，避免第一行切换引起表格重渲染后继续使用失效的 WebElement。
- 提交前的 `skuTable` 状态写入和提交后复核都必须覆盖全部匹配索引；任一重复行仍在线时，该任务不得记为成功。
- 若目标条形码的全部匹配行就是商品当前全部在线 SKU，批量下架会导致零在线 SKU，仍按 `sole_sku_requires_product_offline` 业务异常停止并钉钉告警，不自动整商品下架。
## 2026-07-30 Managed Update (Buyer-Protection Shipping Time)

- For the current 1688 listing contract, `24小时发货` maps to service code `essxsfh` in `buyerProtection.channelRenderMap.dsc`.
- Keep the display name and service code synchronized in the page-state patch, `draftSubmit` patch, persisted schedule verification, submit-time reapply, and success reconciliation.
- A working normal-browser draft page does not prove the dedicated automation Profile is healthy. Compare the same official draft URL under both sessions before changing selectors or deleting/rebuilding a draft.

## 2026-07-29 Managed Update (Independent Draft Persistence)

- A successful `draftSubmit` response, matching response ID, and same-session page reload are necessary but not sufficient. Persistent acceptance requires a fresh official `draft2offer` reopen and exact full-field checks.
- Never use `offer-new ... operator=new&catId=...&draftId=...` as repair execution or persistence evidence. It can render a valid empty form for the expected ID while the official draft entry is unusable.
- `SYS_ERROR` can be returned in the initial publish HTML before any XHR/fetch request. Capture URL, body text, operation code, screenshot, and boot-network records, then classify the draft entry as unavailable.
- Use non-target draft controls before diagnosing one draft as corrupt. For `木刻理想`, the target and two control drafts all returned `SYS_ERROR`, which indicates account/platform draft-entry failure.
- On an independent-field failure, write `draft_verification_failed` to the formal audit and block approval/submit. Do not preserve a false `draft_pending_review` state from an earlier same-session check.

## 2026-07-29 Managed Update (1688 Draft Identity and Recovery Links)

- Existing-draft editing should start from `https://offer.1688.com/offer/post/fillProductInfo.htm?operator=draft2offer&offerDraftId=<id>`, then verify the final URL and `SellPublishSdk` state expose the same ID.
- Do not trust a successful click or HTTP 200 alone. A repair save must prove the expected ID in the outgoing `draftSubmit` URL/body and in the successful JSON response.
- The current platform may redirect the formal entry to `offer-new.1688.com/popular/publish.htm`. If the page is complete but shows `SYS_ERROR`, preserve the error code and screenshot and classify the draft as unavailable.
- The product-management draft box is the authoritative recovery source when a direct URL fails. Preserve each visible row's full text, all links and `href` values, row/link/descendant `data-*` attributes, and extracted `draftId/offerDraftId` values before choosing an edit action.
- A `SYS_ERROR` does not prove that a draft is absent. Never create a replacement draft until the draft-box evidence and management entry establish that no recoverable historical draft exists and the workflow policy is explicitly changed.
- CTG0286 live evidence confirmed both draft IDs in a 5-row draft box. Clicking the old row's actual “继续发布商品” link still opened the matching ID and returned `SYS_ERROR`; direct navigation versus management-page click therefore does not explain the failure.
- Store identity elements can go stale or temporarily disappear during management-page React re-render. Retry identity observation briefly and retain the body-text fallback instead of treating one empty read as a wrong account.

## 2026-07-28 Managed Update (1688 Draft Re-Render)

- Live finding: reloading between image-picker batches can leave later steps successful in the run log while title, primary picture, sale specs, and logistics are empty in the final React state.
- A text value left in `.value-select-item.resident input` is not a committed SKU specification. Pressing Tab creates a `.value-select-item:not(.resident)` item; Enter did not commit on the observed page.
- Before draft save, repair fields in this order: square main image, committed specs, title, then core price/inventory/description state. Do not re-upload completed detail batches.
- Verify the exact payload title and exact committed spec set immediately before save. Presence-only checks can accept stale or wrong values.
- Keep the pre-save guard fail-closed. A failed guard is evidence that no draft save was attempted, not evidence that the draft is repaired.

## 2026-03-26 Managed Update

- New live-page finding:
  - required `配送服务` lives under `#guid-customExtraService .special-service-wrapper`;
  - it can show inline `配送服务为必填项` even when toast-level message selectors stay empty.
- Engineering updates:
  - added delivery-service auto-fill (prefer existing checked option, otherwise enable switch + check first option);
  - added broader stale retry support for dynamic 1688 React re-render behavior.
- Practical limitation observed:
  - some products still show `submitted but still online` after execute, meaning submit-state confirmation may need extra page-level success signals beyond current toast selectors.

更新时间：2026-03-26

## 用途

记录平台级经验，供后续同平台功能复用。

## 适用范围

当前主要覆盖：

- 1688 发布页自动上架
- 1688 商品管理页 SKU 下架

未来可以继续扩展到：

- 1688 草稿保存
- 1688 正式发布
- 1688 多类目、多店铺差异化铺货
- 1688 SKU 下架批处理

## 当前已验证的 1688 经验

### 1. 当前 live 页面关键选择器

- 标题：`#guid-title input[maxlength="60"]`
- 价格：`#guid-priceRange td[data-next-table-col="2"] input`
- 库存：`#guid-totalSales input`
- 发布按钮：`#submitFormButton`
- 类目修改按钮：`#guid-catNamer button`
- 类目确认按钮：`#submitButton`
- 错误信息面板：`#guid-assistBoard .info-list li div`

### 2. 已验证类目路径

- `家装建材 > 客厅家具 > 角几/边几`

### 3. 已验证上传策略

- 主图：优先用 React bridge
- 详情图：优先用 React bridge，完成后插入 TinyMCE
- 描述：优先直接写 TinyMCE 内容

### 4. 已验证容错策略

- 可选属性不存在时自动跳过
- 类目文本没有 `>` 分隔符时，仍可识别为当前类目

### 5. 1688 SKU 下架当前页面理解

- 入口页：商品中心 -> 商品管理
- 搜索字段：`商品ID`
- 编辑入口：`修改详情`
- 实际执行位置：编辑页 `销售信息`
- SKU 定位键：`单品货号`
- 执行动作：切换 `是否上架`
- 当前保存动作：优先尝试 `我要发布`

### 6. 1688 SKU 下架店铺过滤经验

- 店铺名经常存在“公司全称 vs 运营简称”的差异，例如 `阿里巴巴-常州速班达家居有限公司` 与 `速班达家居`
- 过滤应使用“规范化匹配”，不能只做字符串全等
- 预览必须输出过滤诊断（`applied_filters / loaded_store_names / filter_hint`），避免批次误判为“无任务”

## 当前可复用代码清单

### A. 类目路径自动化

- 文件：[rpa/browser_rpa.py](D:/script_files/ERP/furniture-uploader/rpa/browser_rpa.py)
- 关键词：`category_path`
- 可复用场景：同平台其他类目的路径选择

### B. 非 DOM 结果提取

- 文件：[rpa/browser_rpa.py](D:/script_files/ERP/furniture-uploader/rpa/browser_rpa.py)
- 关键词：`current_url`, `body_text`, `page_source`
- 可复用场景：成功页不稳定、弹窗不稳定的平台

### C. 选择器体检

- 文件：[rpa/doctor.py](D:/script_files/ERP/furniture-uploader/rpa/doctor.py)
- 可复用场景：新平台接入前快速发现空 selector

### D. smoke 模板回归

- 文件：[templates/1688_corner_table_smoke.csv](D:/script_files/ERP/furniture-uploader/templates/1688_corner_table_smoke.csv)
- 可复用场景：联调前的标准回归入口

### E. SKU 下架任务解析

- 文件：[rpa/sku_offline_tasks.py](D:/script_files/ERP/furniture-uploader/rpa/sku_offline_tasks.py)
- 可复用场景：同平台其他批处理任务的 Excel 读取、筛选、去重、防重

### F. SKU 下架预览诊断

- 文件：[rpa/sku_offline_main.py](D:/script_files/ERP/furniture-uploader/rpa/sku_offline_main.py)
- 关键词：`applied_filters`, `loaded_store_names`, `filter_hint`
- 可复用场景：批次预检、托管模式自动巡检

## 当前踩坑记录

### 坑 1：类目路径不要按 `/` 拆分

因为像 `角几/边几` 这种叶子类目本身就带 `/`。

### 坑 2：不要把不存在的可选字段当失败

不同类目下品牌、材质等属性可能完全不出现。

### 坑 3：不要默认成功页一定有稳定按钮或卡片

应优先从 URL 和页面文本做兜底提取。

### 坑 4：不要直接用业务模板做基础联调

业务模板常带脏数据、无效图片路径和非标准类目。

### 坑 5：不要把“商品 ID 命中”当成“SKU 已命中”

1688 下架是 SKU 级操作，搜索到商品后还必须再按 `单品货号` 精确定位。

### 坑 6：不要假设店铺名在 Excel 和配置中完全一致

真实数据中经常出现全称/简称差异，直接全等过滤会导致 `selected_count = 0` 的假阴性结果。

### 坑 7：不能把 Profile 失效等同于要求运营手动登录

1688 的账号级登录能力已经存在于共享项目 `src.cli`。下架或替换执行时，正确顺序是：

1. 复用账号 Profile 打开管理页并检查登录跳转、页面风控和店铺身份。
2. 仅在登录失效且没有检测到风控时关闭 ERP 浏览器，调用 `python -m src.cli login`。
3. 自动登录成功后重新创建 ERP 浏览器，再次检查管理页和店铺身份。

自动登录最多每店一次。自 2026-07-28 起，仅下架/替换恢复可调用既有滑块 RPA，单次最多 4 次；其他验证码或风控记录并告警，不做无限重试。

### 坑 8：显式 Edge 路径不能和系统其他安装目录混合选版本

当 `browser_binary_path` 已指定时，驱动版本检测只能检查该路径所属的 `Application` 目录。继续扫描 `PROGRAMFILES/LOCALAPPDATA` 并选最高版本，会把另一套 Edge 的版本误认为目标浏览器版本，最终造成驱动错配。只有未提供显式路径时才允许扫描系统默认安装目录。

### 坑 9：多店并发不能继续使用整机浏览器全局锁

不同店铺使用独立账号 Profile 时，整机全局锁会让无关账号互相阻塞；直接取消锁又会让爬虫和下架同时操作同一 Profile。正确边界是：

1. 浏览器锁按 `account_key` 命名，共享爬虫 Worker 和下架 pipeline 使用同一锁文件协议。
2. `crawler_task` 活跃检查必须带当前 `account_key`，近期下架审计必须带当前店铺。
3. 同店商品、SKU 和重试保持串行，只允许不同账号店铺并行。
4. 聚水潭这类共享单会话资源单独使用全局锁，不随 1688 店铺并发。
5. 管理器仍需单实例锁，避免操作者重复启动两个完整日批。

`--shared-lock-path` 是兼容旧入口的显式覆盖；并发日批不得把多个账号配置到同一个自定义锁文件。

## 知识库更新规则

出现以下情况就必须更新本文件：

- 抓到新的 live selector
- 验证了新的类目路径
- 发现新的稳定上传方式
- 修掉一个平台级坑
- 总结出可以复用到同平台其他页面的实现
## 2026-07-28 Live Finding: 1688 SKU Specification Entry

- Scope: `#guid-saleProp .module-spec-decorator` on the current 1688 publish page.
- Color accepts direct Chinese text. The verified sequence is: scroll the input into view, click the nearest `.value-select-container[aria-haspopup="true"]`, focus the input, clear and type the Chinese value, then press Tab.
- Do not require an exact suggestion option and do not choose the first standard color. The standard color-family overlay is optional UI assistance.
- Acceptance requires the expected value in `.value-select-item:not(.resident)` and the local required-field warning to be absent. Text left only in the resident input is not committed.
- Apply the same direct-entry and read-back rule to size.
- Never click a direct-save confirmation when the modal states that incomplete specification data will be cleared.

## 2026-07-29 SQL Server Driver Finding

- The executor has ODBC Driver 17, while the development machine currently exposes SQL Server Native Client 10.0.
- Read-only lifecycle probes must resolve an installed SQL Server driver; a hard-coded ODBC 17 dependency can fail before any query is issued.
- Driver compatibility does not relax the data gate: the current row must still match all four lifecycle fields exactly.

## 2026-07-28 Required Specification Review Rule

- A required 1688 specification must be checked for exact persisted text after refresh; non-empty alone is insufficient.
- Direct Chinese input plus Tab is valid for color, but review must compare the committed value with the payload.
- For CTG028601N1416V01, expected persisted values are color `胡桃色` and size `48/40/50`.

## 2026-08-04 Live Finding: Hidden Validation Before SKU Offline Submit

- The 1688 edit page can show an offline switch as selected while a hidden product-attribute validation still prevents the save request. Observed example: `毛重必须为数字` for attribute `p-1957`.
- A visible switch state is not proof of persistence. When no submit request is emitted, collect assist and hidden validation text before classifying the result.
- Preserve explicit platform text as `system_prompt`; fail and skip only the current product ID/SKU, then continue the store batch.
- Do not retry the same unchanged product, stop the store, create a Jushuitan handoff, or report the SKU as successfully offline.
- Keep dedicated business classifications, such as `sole_sku_requires_product_offline`, ahead of the generic system-prompt classification.
# 2026-08-05 聚水潭已清除判定

- 不能用整行文本 `includes` 判断店铺、商品、SKU 或平台编码，前缀值会产生误匹配。
- 必须按表头解析四个结构化列并逐列规范化等值比较。目标不存在只能由精确 sibling 行或精确筛选回读后的显式零行证明，缺列时失败关闭。

## 2026-08-06 中断恢复不能只清运行锁

- `Run=failed` 和文件锁消失不代表下架审计已闭环；必须同时检查 Item、Saga 和 Outbox。
- 对浏览器进程中断，Item 记录 `automation_error`，Saga 记录 `failed_terminal/interrupted_executor_process`，且两者都保留原始中断原因。
- 只有 1688 成功或已下架才允许产生聚水潭 Outbox；技术失败的正确终态是无 Outbox，并在证据中明确 `not_created_ali1688_failed`。

## 2026-08-07 过期 Stop-Sale 运行时恢复经验

- 不要凭文件锁消失、任务计划状态或单次 SQL 快照判断 owner 已死亡。恢复只能在记录 hostname 与真实本机一致且 PID 为正数时进行，并用正常隔离级别同时核对数据库服务器时间、request/lease TTL、精确 PID、同账号 Crawler task/attempt、目标 Stop-Sale run 和外来 owner；安全门禁不得使用 `NOLOCK` 脏读。
- 恢复快照必须覆盖 `account_key`、`run_id`、`request_key`、`task_type`、owner、PID、PID 存活证据、资格结论、`recovery_mode`、fencing、资源键和正式存储过程集合；只有 Preview 当时已经 `eligible=true` 才能 Apply，Apply 前还要重新计算完全一致的指纹。资源全部缺失时只有 `request_only` 模式可用，部分资源缺失或未知资源必须阻塞。旧版本 3 Preview 当时被阻塞时，即使条件后来变化也必须重新生成版本 4 Preview，不能复用。
- 可恢复资源只允许目标账号租约和当前主机 `hostname:1..3` 的单一浏览器槽位。`jushuitan`、未知资源、跨账号资源或额外租约必须停止，不能顺手释放。
- 完整资源恢复必须先释放浏览器槽位、再释放账号租约，并与 request 接管/完成放在同一事务中执行。`request_only` 不调用租约恢复过程，但必须在 request CAS 前以锁定查询确认目标账号、run、owner 相关租约为零；提交前还要在同一事务内再次验证 request 已 `cancelled`、`completed_at` 非空且相关租约为零。仅依赖逐条存储过程各自成功会留下部分释放窗口；任一后态不符必须整体回滚，提交后再做独立读取复核。
- 凭据或配置不可用同样是可审计阻塞，应输出结构化 `blocked` artifact，不能只留下 traceback。工具验证不等于真实页面、执行机或生产数据库验收。
- 本地复用契约由 `29/29` 个恢复工具测试覆盖；本轮 C 线下架/替换聚焦回归为 `324/324`，完整 `furniture-uploader` 回归为 `734/734`；毛重系统提示与 `combination_sku` 精确契约为 `6/6`（`2 + 4`）。生产 Preview、Apply 和真实页面 Canary 仍必须以执行机实时证据为准。

## 2026-08-07 全店账号映射经验

- `task.shop_name`、1688 源表店铺名和聚水潭店铺名是三个不同字段。前两者可以通过 authoritative roster + 只读源表 Preview 验证；聚水潭店铺名不存在权威值时必须缺失，不能由前两者改写或猜测。
- 两个 `account_key` 共用 member/shop/source 身份时不能按 Profile 名或历史使用习惯自动选择 owner。`pingcan`/`pingcan_rpa` 因此保持 fail closed。
- Profile 文件存在只证明配置路径可用，不证明登录有效。把映射加入配置后仍需逐店 preflight、RPA 登录以及 member/store 双重核验。

## 2026-08-07 Windows Marker 替换竞态

- PowerShell `Move-Item -Force` 更新 inspector 的 `.launcher.json` 时，读进程可能在替换窗口收到 `PermissionError`，不能把它误判为浏览器或 1688 业务失败。
- 生产 launcher 使用同卷临时文件和 `System.IO.File.Replace` 更新已有 marker，首次创建才使用 `File.Move`；读者始终看到旧的完整 JSON 或新的完整 JSON。
- 该修复必须同时通过 wrapper 回归、执行机全量回归和真实页面证据；测试通过本身不代表 Draft/Offer 验收。
