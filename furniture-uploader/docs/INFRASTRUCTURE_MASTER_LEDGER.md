# 1688/ERP 基础设施总账（真源文档）

> 最后更新：2026-08-19 · 维护分支：`codex/1688-cross-project-lease-erp-20260730`
> 本文档是服务器、数据库、数据表、端点、账号、凭据**位置**的唯一总账。散落在交接文档、代码配置、对话中的基础设施信息以此为准。
>
> **硬约束：本文档只记录凭据的存放位置与引用方式，永远不写明文值**（对应交接文档 §4.4：秘密唯一允许存储位置为 Windows Credential Manager）。

## 1. 机器清单

| 角色 | 主机名 | 说明 |
|---|---|---|
| 开发机 | `QuantPrivate` | 本文档所在机器；ERP 主仓库 + 全部 worktree；git 身份 `QuantPrivate Admin <admin@quantprivate.com>`（多会话共用，不能用来定人） |
| 执行机 | `PC-20210622ARIU` | 跨项目租约协议批准的正式执行机（对齐稿 §4.2 的 browser_slot 主机名）；运行 Crawler Worker、下架/上架/替换、聚水潭；从 Gitee 拉取代码 |

## 2. 代码仓库与工作区拓扑

### 2.1 ERP 主仓库（本仓库）

| 项 | 值 |
|---|---|
| 主仓库 | `D:\script_files\ERP\.git` |
| GitHub | `git@github.com:xyjtt/ERP.git`（remote `origin`） |
| Gitee | `https://gitee.com/xyjtt/erp.git`（remote `gitee`，**部署侧权威**，执行机从此拉取） |
| worktree 数量 | 24+（`D:\script_files\ERP_*`、`D:\script_files_erp_store_20260813`） |
| 当前租约工作区 | `D:\script_files\ERP_cross_project_lease_20260730`（分支 `codex/1688-cross-project-lease-erp-20260730`） |

### 2.2 独立 clone（不在主仓库 worktree 体系内，排查时容易漏）

| 路径 | 用途 |
|---|---|
| `D:\JsWork-1688\erp-reference` | Gitee 独立 clone，**直接在 `deploy/cross-project-lease-20260730` 分支开发**（自动上架多 SKU 会话，2026-08-17/18 的部署分支提交来源） |

### 2.3 关联仓库

| 仓库 | 路径 | 职责 |
|---|---|---|
| 1688 Crawler（爬虫） | `D:\script_1688_cross_project_lease_crawler_20260730`（`script_1688` 项目 worktree） | 跨项目租约共享对象（`app.ali1688_runtime_*` 表 + 存储过程）的**唯一维护方**；共享 DDL：`sql/367_ali1688_cross_project_runtime_lease_full_2026-07-30.sql` |
| 聚水潭批量项目 | `furniture-uploader/jushuitan-sku-offline-batch`（本仓库子目录，TypeScript/Node） | `cleanup:1688` 链接清理、`sync:1688` 按链接同步 |

## 3. 数据库

### 3.1 实例与库

| 项 | 值 | 出处 |
|---|---|---|
| SQL Server | `218.93.9.21` | `rpa/stop_sale_audit.py` `DEFAULT_APP_SERVER` |
| 驱动 | `ODBC Driver 18 for SQL Server`，`TrustServerCertificate=True` | 同上 |
| 应用/审计库 | **`JSReportReplica`**（默认库；下架/替换审计、Saga/Outbox、Crawler 运行时租约、上架任务表都在此） | `DEFAULT_APP_DATABASE` |
| 下架计划源库 | **`JSDataMiddlePlatform`**（环境变量 `STOP_SALE_SOURCE_SQLSERVER_DATABASE` / `STOP_SALE_SQLSERVER_DATABASE` 可覆盖） | `run_1688_stop_sale_pipeline.py` `--source-database` 默认值 |

### 3.2 凭据（只记位置，不记值）

| 用途 | 存放位置 |
|---|---|
| 应用库读写（app-writer） | Windows Credential Manager：`YYDD/1688/database/app-writer` |
| 下架源库账号 | Windows Credential Manager：`YYDD/1688/database/stop-sale-source`；或环境变量 `STOP_SALE_SOURCE_SQLSERVER_USER/PASSWORD`（兼容 `STOP_SALE_SQLSERVER_*`） |
| 钉钉通知 | 环境变量 `DINGTALK_WEBHOOK` + `DINGTALK_SECRET`（配置可指定 `webhook_env`/`secret_env`），`hydrate_dingtalk_credentials` 可从凭据管理器补全 |
| 1688 账号密码/Cookie | 仅 Windows Credential Manager（交接文档 §4.4） |

### 3.3 数据表总账（schema `app` 为主）

**跨项目运行时协议（Crawler 仓库独占维护，ERP 只调用）** — DDL：Crawler `sql/367`

| 表 | 用途 |
|---|---|
| `app.ali1688_runtime_protocol` | 协议名/版本/enforcement/容量（当前 capacity≤3） |
| `app.ali1688_runtime_lease` | 账号/浏览器槽位/聚水潭租约（TTL 120s，fencing token） |
| `app.ali1688_runtime_request` | 高优先级写 request（priority：写=100、crawler=10） |
| `app.ali1688_executor_account_binding` | 执行机账号绑定（hostname/account/profile/CDP/config hash） |
| `app.crawler_account_shop` | 账号/店铺身份权威名册 |
| `app.crawler_task` | Crawler 任务/attempt（活动任务检查用） |

相关存储过程：`usp_ali1688_runtime_{protocol_assert, request_register, request_heartbeat, request_complete, lease_acquire, lease_heartbeat, lease_release}`、`usp_ali1688_executor_binding_assert`（ERP 客户端调用矩阵见交接文档附录 B）。

**ERP 独占** — DDL：本仓库 `sql/`（编号 001/360/361/362，生产 apply 需显式 `--apply` 并经审批）

| 表 | DDL 文件 | 用途 |
|---|---|---|
| `app.ali1688_operation_saga` | `362_ali1688_operation_saga_outbox.sql` | 上架/下架/替换操作 Saga 状态机（7 态，fencing 约束） |
| `app.ali1688_operation_outbox` | 同上 | 聚水潭后置任务 Outbox（pending/claimed/retryable/terminal，CAS claim） |
| 停产下架审计表组 | `360_ali1688_stop_sale_audit.sql` | 下架 run/item 审计 |
| SKU 替换审计表组 | `361_ali1688_sku_replace_audit.sql` | 替换 run/item 审计 |
| `app.ali1688_listing_task` / `app.ali1688_listing_execution` / `app.ali1688_listing_audit` | `361_ali1688_listing_workflow.sql` | 自动上架任务/执行/审计（含 2026-08 新增原子 claim、旧 payload 防护、参数化身份） |

**业务数据源表（只读/取数）**

| 表 | 库 | 用途 |
|---|---|---|
| `dbo.op_stop_sale` | `JSDataMiddlePlatform`（源库） | 停产下架计划取数（`build_1688_stop_sale_preview.py` `--table` 默认；店铺/商品/SKU/条码列见 `config/systems/1688_sku_offline.json` `input.columns`） |

## 4. 外部站点与页面端点

| 端点 | URL | 用途 |
|---|---|---|
| 1688 登录 | `https://login.1688.com/` | 上架/下架/替换登录 |
| 1688 商品管理（全部 Tab） | `https://work.1688.com/?_path_=sellerPro/2017sellerbase_offer/shasngpinguanlinew&tab=all&...` | 下架/替换强制入口（`tab=all` + 页面 active 双证据） |
| 聚水潭登录 | `https://www.erp321.com/login.aspx`（`JST_LOGIN_URL`） | 聚水潭 Worker |
| 聚水潭商品 | `https://www.erp321.com/epaas`（`JST_PRODUCT_URL`） | 链接清理/按链接同步 |
| 钉钉 webhook | `https://oapi.dingtalk.com`（加签） | 业务通知 |

## 5. 1688 账号、店铺与浏览器 Profile

### 5.1 ERP 写操作店铺映射（`config/systems/1688_sku_offline.json` `execution.store_accounts`）

| account_key | 店铺 | 别名关键词 | Profile 目录 |
|---|---|---|---|
| `muke_lixiang` | 阿里巴巴-广州淘淘家居有限公司 | 木刻理想 | `D:/script_1688/.local/browser_profiles/1688_profile_muke_lixiang` |
| `guangzhou_wolai` | 阿里巴巴-广州沃来贸易有限公司 | 广州沃来家具 | `.../1688_profile_guangzhou_wolai` |
| `gonglai` | 阿里巴巴-常州工莱家具 | — | `.../1688_profile_gonglai` |
| `lechang` | 阿里巴巴-常州乐畅家居有限公司 | — | `.../1688_profile_lechang` |

自动登录身份校验需要 `expected_member_id` 等（在下架/替换配置 + accounts.json）。

### 5.2 Crawler 账号 Profile 池

`D:/script_1688/.local/browser_profiles/1688_profile_*`（account_a、banbanshun、beimiao、fanshe、feitan_shaoyou、gonglai 等，数量多于 ERP 写账号）。

### 5.3 执行机账号配置（协议权威输入）

- 路径（执行机）：`C:\ProgramData\YYDD\1688-crawler\config\accounts.json`（本开发机无此文件，开发走 config fallback）
- 顶层必须含 `config_revision` / `config_hash`（64 位小写 SHA-256，规则见交接文档 §7.2）/ `target_hostname`
- 每账号：`account_key`、`profile_ref|profile_key|browser_profile_dir`、正整数 `cdp_port`、`enabled`
- 哈希不匹配 → `runtime_config_hash_mismatch` fail closed，不得启动浏览器

## 6. 跨项目运行时协议常量（速查）

| 常量 | 值 |
|---|---|
| 协议 | `ali1688-cross-project-lease` v1 |
| priority | 写操作 100 / crawler 10 |
| 租约 TTL / 心跳 | 120s / 20s（连续失败 2 次或失联 40s 停机） |
| 浏览器槽位 | `browser_slot` 资源键 `<hostname>:1..3`，全机共享总量 3 |
| 聚水潭单路 | `jushuitan/global` |
| 租约获取顺序 | request → account lease → browser slot → 本地锁 → Browser |

## 7. Windows 计划任务（执行机）

| 任务 | 状态 |
|---|---|
| `YYDD-1688-Crawler-Worker` | Crawler Worker（协议强制时由租约层门禁，不再整机暂停） |
| `YYDD-1688-Stop-Sale-Daily` | 每日下架；**保持禁用**，容量 1 联合验收通过前不得启用 |

## 8. 真源文档索引（详细信息按此下钻）

1. `docs/handoff/1688_CROSS_PROJECT_RUNTIME_LEASE_ERP_HANDOFF_2026-08-12.md` — 租约/Saga/Outbox 全量交接（含协议常量、部署、验收）
2. `docs/PROJECT_MEMORY.md` / `DIRECT_1688_PROGRESS.md` — 项目演进
3. Crawler 契约：`D:\script_1688_cross_project_lease_crawler_20260730\docs\{366,368}_*_2026-07-30.md` + `sql/367`
4. 配置真源：`config/platforms/1688.json`、`config/systems/1688_{sku_offline,sku_replace,direct}.json`（支持本地 override）

## 9. 变更记录

- 2026-08-19：初版，由散落在交接文档/代码配置/会话结论中的信息汇总（ERP HEAD `abb4c78`，618/618 测试通过基线）。
