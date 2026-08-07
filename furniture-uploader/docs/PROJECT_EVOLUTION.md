# Project Evolution

## 2026-08-07 Interrupted Child Recovery Preserves Partial Reports

- Child recovery evolved from marking every pending audit item as an interruption failure to reconciling the persisted JSONL report first.
- Recorded platform failures retain their original business or technical classification. Unrecorded items alone receive `interrupted_executor_process`.
- Recorded success/already-offline evidence is a hard stop because it may represent an irreversible 1688 action that needs explicit Saga reconciliation before any retry.

## 2026-08-06 Listing Scheduling Moves to S4U

- 上架日度从“必须保留 Administrator explorer 会话，再由 SYSTEM Launcher 调用 RunEx”演进为“Administrator S4U 任务在 Session 0 直接运行”。
- 无日触发的按需 Launcher 按正式任务 Principal 分流：S4U 使用 `Start-ScheduledTask`；历史 Interactive 安装仍使用经过校验的单一 session `RunEx`，未知 LogonType 失败关闭。只有 Daily 持有 08:00 触发，避免两任务先后重复运行。
- 该变化只移除桌面依赖，不放宽账号租约、Credential Manager、身份、唯一草稿、重复任务、审批和 submit 门禁。

## 2026-08-05 Interrupted Stop-Sale Recovery Becomes Evidence-Driven

- 中断日批不再通过人工删除 Manager 锁恢复。恢复工具必须证明锁归属、owner PID 已死亡、child run 集合完整且全部终态，并采用“先 Summary、后二次锁校验、最后释放”的顺序。
- Child 集合从“文件名中出现 run id”演进为“正式 Summary/report 与数据库审计交叉证明”。审计前被高优先级写操作拒绝的日志只能作为 orphan 证据，不能替代 child run。
- 补跑范围不再从日志数量或人工猜测生成。原始 Preview CSV 是范围真源，Manager Summary 限定 child run，逐项报告决定 `missing/technical/excluded`；只有 `missing` 和 `technical` 可进入定向恢复清单。
- 聚水潭恢复从“重跑失败批次”改为 Saga/Outbox 单 operation fenced 恢复。已验证成功和 `already_cleared` 结果保留，状态或 fencing 证据漂移时 CAS 拒绝修改。
- 该演进只完成开发验收，不改变 `daily_20260804_130814_970163` 的生产状态，也不授权手删锁、批量重排、广泛重跑或绕过 Credential Manager。

## 2026-07-28 Login Recovery Evolution

- 账号恢复从“遇到滑块直接停店”演进为“仅下架/替换显式启用既有滑块 RPA，最多 4 次”；普通爬虫和普通登录仍默认不自动处理。
- 登录成功判定从“页面离开登录页”提升为“登录态成功 + `expected_member_id` 唯一匹配 + 目标店铺匹配”。
- 停店策略从登录、风控、浏览器和页面异常的宽泛停店，收窄为真实店铺/账号身份不匹配；其他异常必须审计、通知并继续隔离后的业务范围。

## 2026-07-27 Daily Stop-Sale Recovery Evolution

- 每日调度从“进入任务时暂停一次 Worker”演进为“进入任务时暂停，并在每个批次尝试前再次确认”。这样即使外部计划任务在长批次期间重新启用 Worker，也不会与下一次 1688 操作并发。
- 上述整机暂停策略是历史阶段，已由 2026-07-28 的账号级共享锁取代。
- 执行保护从 Pipeline 外部前置检查演进为审计生命周期内检查：未通过保护的批次也必须有审计批次、`not_attempted` 明细、Summary 和钉钉结果，不再留下不可追踪的空洞批次。
- 浏览器恢复从“同一 Selenium/Edge 会话内重试”演进为“自动化故障时重建当前店铺拥有的会话后重试”，减少 renderer 卡死和动态页面损坏的重复失败。
- 安全边界不变：不全局终止进程、不绕过验证码/风控、不对唯一在线 SKU 自动整商品下架；开发回归不等于执行机真实验收。
## 2026-07-29 - Draft Acceptance Became Independently Verifiable

- User-authorized cleanup reduced CTG0286 to one replacement draft, `6a69bee6e4b01cad1b297a52`.
- Identity-patched save reached HTTP 200, but a fresh browser proved that the saved business fields were absent and the official management link returned `SYS_ERROR`.
- Repair execution and post-save verification now always use the platform's official `draft2offer` entry; saved new-listing URLs are retained only as diagnostics.
- The independent inspector can follow the actual `tab=all` row link, fail fast on the platform error page, and capture publish-boot network evidence without saving or submitting.
- Formal workflow correction changed the task from false `draft_pending_review` to `blocked`. This prevents approval, submit, Offer writeback, and executor deployment from advancing on a same-session false positive.

## 2026-07-29 - Draft Recovery Became Identity-Bound

- Draft repair moved from reconstructing a publish URL to using the platform's own `offerDraftId` edit entry.
- Recovery is now bound end to end: workflow history authorizes the draft ID, the loaded page must expose it, the save request must carry it, and the successful response must return it.
- A known historical draft can be explicitly rebound after review rejection, but an unknown ID cannot enter the state machine.
- A platform `SYS_ERROR` is now reported as `unavailable` with URL, title, runtime flags, body text, and screenshot. It never authorizes a new draft or a blind retry.
- Product-management evidence was expanded from draft count only to row-level link and identifier evidence so recovery can follow the exact edit URL rendered by 1688.
- Live recovery proved that both historical IDs remain in the draft box. Clicking the old row's exact platform link still returns `SYS_ERROR`, which moves the remaining CTG0286 work from selector uncertainty to a confirmed platform-availability blocker.

## 2026-07-28 - Draft Save Convergence Gate

- The 1688 direct path moved from presence-only pre-save checks to payload-exact checks for title and committed specifications.
- Dynamic page reloads are treated as a normal loss-of-state condition: the save path reapplies only nonpersistent main-image/spec/title fields and does not repeat completed detail uploads.
- The resident specification editor is no longer considered persisted data; only committed non-resident SKU specification items pass verification.
- This change does not enable Offer submission. The completion boundary remains repaired draft review plus independent audit.

更新时间：2026-03-24

## 目的

记录项目的重要方向变化、能力演进、验证节点和策略调整。

## 演化时间线

### 2026-07-28

- 停产下架从“整机全局暂停爬虫、所有店铺串行”演进为“按账号共享浏览器锁、最多两店并发起步”。
- 并发边界固定在店铺：同一店铺及同一商品的 SKU 继续串行，避免 Profile、页面状态和批次重试互相覆盖。
- 共享爬虫 Worker 和下架 pipeline 统一使用 `account_key` 锁；活跃爬虫任务只阻塞同一账号。
- 聚水潭仍是共享单会话资源，增加全局锁保持单路执行；管理器继续使用单实例锁防止日批重复启动。
- 开发机全量回归、doctor 和正式数据只读 preview 已通过；执行机双账号真实 Canary 仍是上线前置条件。

### 2026-07-23

- SKU 第二阶段从“仅保留替换字段”演进为独立 `replace` 操作，避免把替换错误建模为下架或整商品操作。
- 配置增加继承机制，替换系统复用下架系统的四店 Profile、店铺映射、安全规则和页面选择器。
- 下架与替换统一采用商品管理“全部”Tab；相同商品 ID 的多个 SKU 统一在一个编辑页内处理并一次提交。
- 聚水潭后置动作按业务截图确定为“手动同步商品 -> 按链接同步”，与原下架的“清除链接”保持两个独立动作和结果账本。
- 保持上线边界：代码和 preview 完成不等于 live 验收，真实替换仍需受控旧新 SKU 映射。

### 2026-03-17

- 项目从泛化“家具铺货工具”收敛到先做 `1688` 最小可落地方案
- 保留未来接入聚水潭与多平台的结构设计
- 确定基础技术栈为 `Python + Selenium`
- 引入 SQL Server 作为长期结果沉淀和知识库方向

### 2026-03-18

- 完成基础脚手架
- 完成本地 bootstrap、环境体检、数据库预检
- 增加离线单元测试
- 增加 selector probe 工具

### 2026-03-20

- 明确第一阶段不追求无人值守生产
- 先做“人工登录 + 自动填表 + 人工复核”的 MVP

### 2026-03-23

- 1688 发布页 live selector 采集完成
- 主图上传切到页面 React bridge，稳定性明显提升
- 详情图上传通过同一桥接链路完成
- `title / price / quantity / main_image / detail_images / description` 完成真实 smoke

### 2026-03-24

- 类目路径自动化完成并通过 live 验证
- 成功结果提取支持 `current_url / body_text / page_source`
- 可选属性在当前类目不存在时可自动跳过
- 增加标准 smoke 模板
- 完成一次 `1688_direct --limit 1 --skip-login` 的真实干跑闭环
- 发布最终动作改为 3 模式：`manual / draft / submit`
- 增加草稿模式支持，但默认仍然保持保守的人工复核
- 建立 `D:\script_files\1688` 作为新的 1688 项目统一入口
- 在 `furniture-uploader` 内新增 `1688_sku_offline` 第一阶段代码骨架
- 把 1688 项目从“只做上架”扩展到“上架 + 下架共用一套 Selenium 底座”

### 2026-07-24

- 下架和 SKU 替换从“只复用现有登录态，失效后人工登录”调整为“先复用 Profile，失效后调用共享 1688 账号级登录一次”。
- 当时自动登录仍不处理滑块；该历史边界已由 2026-07-28 的受限滑块恢复和 `member_id` 双重核验取代。
- 该策略只适用于停产下架和 SKU 替换执行器，不改变发布主线的人工/草稿/提交边界。

## 当前阶段

- 阶段：pre-go-live
- 重点：把“真实草稿”和“真实提交成功”两条最终动作补齐

## 下一次必须记录的触发点

- 抓到真实草稿按钮并验证成功
- 完成一次真实提交
- 成功页结果提取闭环打通
- 自动提交进入可上线状态
- 完成一次真实 SKU 下架联调
## 2026-07-28 Draft Save Policy Tightening

- A successful draft HTTP response is no longer treated as proof that required page state persisted.
- Required listing fields are now checked before the save click and checked again after save plus refresh.
- SKU color and size use direct text entry with Tab; suggestion-list matching is advisory UI only and is not part of the data contract.
- Forced confirmation of a modal warning that specifications will be cleared is prohibited.

## 2026-07-28 Exact Specification Acceptance

- Saved-draft verification evolved from presence checks to exact business-value checks for required specifications.
- This prevents a stale platform value such as `红色100` from being accepted when the payload requires `胡桃色`.
- The same rule applies to size, including the CTG0286 value `48/40/50`.

## 2026-07-28 Live Specification Commit Correction

- Live probing showed that Enter can leave an apparently populated resident input without creating a SKU specification item.
- Specification entry now uses Tab and verifies the non-resident committed item before continuing.
- Listing regression increased to `375/375` after adding the commit-state coverage.

## 2026-07-29 Portable Source-Gate Execution

- The listing source lifecycle check moved from an executor artifact into a version-controlled read-only script.
- SQL Server driver selection now follows the installed-driver preference used by the rest of the project.
- Source eligibility remains a fail-closed browser prerequisite and is kept separate from draft, submission, and business acceptance.

## 2026-08-04 Stop-Sale Hidden Validation Isolation

- Explicit 1688 submit validation text is now a first-class `system_prompt` outcome instead of the ambiguous `submit_blocked_before_request` fallback.
- A system prompt fails only the current product ID/SKU, preserves the original platform message, and allows the remaining store batch to continue.
- Retry governance, daily-manager classification, Chinese reporting, and regression coverage were updated together; related tests pass `126/126`.
# 2026-08-05 中断恢复从数量核对升级为身份集合门禁

- 恢复授权不再依赖人工观察数量或 `run_id + limit`，改为文件 SHA-256、四字段身份集合 SHA-256 和 operation_key 精确集合。
- 文件锁释放、恢复清单生成和 Outbox 领取均 fail closed，任何并发或证据漂移都保留现状并停止。

## 2026-08-06 中断恢复扩展到 Item/Saga 终态

- 旧恢复流程只结束 Run 并释放锁，可能遗留 `Item=pending`、`Saga=prepared`。
- 新流程在同一受控恢复中把精确 Item 标为技术失败，并把 Saga 标为 `failed_terminal`；`finished_at` 对失败终态同样必填。
- 1688 未完成时禁止创建 Outbox，聚水潭阶段明确为不适用，而不是成功或待处理。

## 2026-08-07 Runtime Recovery Moves From Manual Lock Handling To Exact CAS

- Expired Stop-Sale runtime cleanup is no longer modeled as deleting a lock or directly updating lease rows. Recovery is an explicit Preview/Apply workflow over one request identity and its exact account/browser resources.
- A versioned, fingerprinted version 4 snapshot is now the handoff contract, including eligibility, PID-liveness evidence, and an explicit recovery mode. An empty resource set can use request-only CAS; a partial resource set is invalid. Apply must reproduce the same fresh snapshot and verify related leases are zero inside the transaction before request takeover; complete-resource recovery still releases browser slot before account lease.
- The recovery owner is immediately completed as `cancelled` after the stale owner is reconciled; a locked pre-commit post-state gate proves `cancelled + completed_at + leases=0`, and a separate read after commit verifies the persisted state.
- This architecture preserves same-account exclusion and browser capacity while allowing unrelated queued Crawler work to remain outside the recovery gate.
- Local contract verification passed the full `furniture-uploader` suite (`734/734`), the C-line Stop-Sale/replacement focus (`324/324`), the dedicated recovery suite (`29/29`), and the exact system-prompt plus `combination_sku` contract (`6/6`); production Preview, Apply, executor deployment, and real-page Canary remain separately verified production stages.

## 2026-08-07 Store Mapping Becomes Roster-Derived

- ERP store mappings are no longer extended from remembered aliases. A deterministic dry-run compares external accounts, the task roster, member/profile identity, and the source-store Preview before producing a binding.
- The configuration expands only when each identity is unique. Shared member/source identities and missing Jushuitan names are explicit blockers, not fallback guesses.
- SKU Replace inherits the same mapping set from Stop-Sale, preventing the two flows from drifting to different account ownership.

## 2026-08-07 Windows Launcher Marker Atomicity

- A production-style executor regression exposed a read/write race in the saved-draft inspector marker: `Move-Item -Force` could leave a reader with `PermissionError` during replacement.
- Marker updates now use same-volume `System.IO.File.Replace` with a temporary backup when a marker already exists, preserving the existing fail-closed launcher and idempotent Draft/Offer contract.
- Focused regression and repeated stress validation pass; the fix is not production-accepted until the guarded executor deployment and post-deploy tests pass.

## 2026-08-07 Failed-Terminal Replay Becomes an Exact Transactional Transition

- A normal pipeline replay is no longer considered sufficient for interrupted `failed_terminal` operations: skipping a completed Saga does not prevent the CSV subprocess from repeating a page action.
- The recovery authorization now binds both immutable input identity and runtime identity: approval SHA, CSV SHA, source run, target run, account, fixed interrupted contract, count, and exact operation-key set.
- All keys are validated before any Saga is changed. SERIALIZABLE locks and compare-and-set updates make scope drift, a newly created Outbox, or historical 1688 success abort the entire transition.
- Recovery resource acquisition is intentionally fail-fast. A busy account, browser slot, crawler, account lock, or Jushuitan lock is a stop condition for a fresh review, not permission to wait or broaden the scope.
