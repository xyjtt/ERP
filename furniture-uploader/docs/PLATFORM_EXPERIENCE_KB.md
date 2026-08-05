# Platform Experience Knowledge Base

## 2026-08-06 Delayed Submit Success and Scheduler Findings

- 1688 submit 成功后可能先进入结果页，再延迟完成页面组件切换。只要已从受信成功 URL、响应或页面状态提取并验证 Offer ID，就必须停止后续点击和旧按钮等待；不得因 `#submitFormButton` 已不存在而再次 submit。
- 浏览器 Execution 误报超时不等于发布失败。只有失败上下文能重建同一任务、草稿、账号、字段重放契约和成功 Offer，且数据库仍是唯一 `submit_pending/prepared` 状态时，才允许一次 Repository reconciliation；已有成功 Execution、审计或 Offer 时必须停止。
- writeback 不是第二次发布。真实详情页独立核验后，`--mode writeback` 只把 `submitted` 推进为 `offer_written_back` 并记录审计，不启动浏览器。CTG0286 已用 Offer `1072868453052` 验证此边界。
- Windows PowerShell 5.1 会把无 BOM UTF-8 脚本中的中文常量按本地代码页解析，可能造成身份比较假失败。生产 wrapper 应保持 ASCII，或从已做 SHA-256 固定并显式按 UTF-8 读取的 payload 取得 `shop_name`。
- `Start-ScheduledTask` 对 Interactive 长驻 Worker 可能只产生排队事件而不绑定当前登录会话，表现为 `Ready/0x800710E0`。执行机受控启动应使用 `Schedule.Service.RunEx(..., session 2)`，随后要求 `Running/0x41301`、唯一父子 Python 链和正式数据库 preview；不能通过重复点击或全局启动第二个 Worker 解决。
- 日度计划任务安装成功不等于产生新业务数据。当前已完成候选应稳定归类为 `duplicate_existing`，不启动浏览器；只有新的完整审核 payload 且实时源生命周期仍合格时，自动链路才可保存既有草稿，并仍停在人工复核前。

## 2026-08-06 Nonpersistent Draft Field Replay Findings

- HTTP 2xx 和 `success=true` 只能证明草稿保存请求被接受，不能证明所有 React 字段在重新打开后仍存在。对配送服务、地址、物流和买家保障必须同时保留请求值、保存前页面状态、响应草稿 ID 和页面允许值。
- 不能把“刷新后为空”直接视为正常，也不能无限重复保存同一草稿。仅当上述证据完整且绑定同一 `draft_id` 时，字段才能进入 `submit_reapply_required`；否则保持 `failed`。
- 配送服务重放必须再次读取当前 `serviceTemplates`，请求 ID 必须属于当前允许集合；不能复用历史 ID。买家保障必须精确读回 `24小时发货/essxsfh` 的 `from=1` 步骤。
- submit 前的重放结果必须覆盖契约中的全部字段，且每项状态为 `reapplied_and_read_back`。页面仍出现“必填/请填写/请完善”时必须停止，成功页 reconciliation 也必须携带相同契约哈希。
- 独立检查只允许上述四个字段使用重放状态。主图、详情图、标题、规格、价格和库存等字段必须真实持久化，缺失时不能借用重放契约通过检查。

## 2026-08-05 CTG0286 Native Component Contract Findings

- 配送服务 ID 不能跨类目硬编码。当前真实页面只允许 `365841=送到楼下` 和 `4511641=市区物流点自提`；应从 `customExtraService.props.serviceTemplates` 读取允许值，并优先保留当前已选值。
- 当前类目 `buyerProtection.props.processOffer=false` 时，`24小时发货` 的原生步骤为 `{from: 1, value: "essxsfh"}`。不要添加 `serviceName`、`spsCode`，不要自动勾选未选择的 `sstbt/czbz/jgdz` 服务，也不要保留空步骤组。
- React 地址组件的可见选择不足以保证请求完整。保存请求必须同时包含 `cbuSendAddress.value=<id>` 和 `freight.sendAddressId=<id>`。
- `draft2offer` 的真实保存请求可以合法使用 `edit=false` 且没有 `isItemEdit`。既有草稿安全门禁应校验请求和响应中的唯一 `draftId`，不应推断或强制另一组编辑标志。
- 对复杂表单请求只保存截断 body 不足以诊断。应同时保留限定字段的结构化原始/补丁后摘要，且不得包含凭据或无关完整业务请求。

## 2026-08-05 Listing Tool Identity and Scheduling Findings

- 独立检查器和容量探针不能只把 `debugger_address` 改为 9306；它们同样会占用账号 Profile，图片探针还会上传素材，因此必须先取得 `ali1688_account_<account_key>.lock` 和跨项目数据库租约，再执行正式 RPA 登录和浏览器动作。
- 业务身份只能来自候选 payload 的 `shop.account_key/shop_name`。外置 `accounts.json` 只提供 Profile/CDP 绑定，平台显示名称不得反向覆盖业务店铺字段。
- 日度上架不能从 `dbo.jst_sku` 猜发布 payload。该表只做最后时刻生命周期门禁：`enabled=1`、`stock_disabled=0`、`other_5=销售`、`item_type=成品`；完整标题、图片、价格、库存、物流和店铺必须来自已审核候选。
- 自动计划只能保存草稿并停在待独立复核。即使草稿保存成功，也必须用独立会话重新打开正式草稿入口并核对完整字段，之后才能由独立人工门禁授权一次 submit。
- `draft_id` 不是可由命令行覆盖的普通参数，而是上架操作的核心身份。日度、检查、复核、Saga 和 submit 必须从同一 payload 得到唯一既有值；缺失、多值或参数不一致时必须停止，不能生成占位 operation key 或创建第二草稿。
- Saga 幂等键必须区分 draft 与 submit，并包含账号、任务和草稿。旧版通用 listing operation key 可能把保存草稿和提交 Offer 混为同一终态，部署迁移时只能作为历史证据，不能直接短路新阶段动作。
- 原子 claim 只解决并发领取；真正执行前仍要在账号租约内重读正式任务 payload，并校验 claim owner 与 payload 哈希，防止磁盘 inbox 或旧子进程重放过期业务内容。
- 即使 event history 标记为 `authorized_draft_rebuild_resumed`，也不能推导出可新建草稿的权限；执行器必须继续要求现有 `draft_id`，否则以业务契约错误终止。

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

## 2026-08-04 CDP Pre-Attach Recovery Rule

- Do not hard-code `9222` when classifying an ERP browser pre-attach failure. Each account may have a dedicated CDP port.
- Parse only `127.0.0.1:<port>` from the failure context, then require the formal failed execution row to report the same endpoint.
- Endpoint agreement is not enough by itself: `result_context` must be empty, the execution must have no Offer/result data, and no later audit event or outbox row may exist.
- A different host, mismatched port, or any side-effect evidence must stop controlled recovery.
