# Project Evolution

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
