# ERP 集成 No-Op 证据（2026-08-07）

## 范围

- 权威基线：`codex/erp-production-closeout-20260807@8a604f5cfa81471bd9ab266d1596472d8704333f`
- 待核对全店配置：`1595fb8324c5eefb81fa21f404f8d8eb4ea62a01`
- 待核对 Listing Inspector：`b45b4eabd72ab19c3588658cd36a85cc22a479d5`
- 集成分支：`codex/erp-production-integration-20260807`
- 本证据不修改 `D:\script_1688`，不部署执行机，不启动浏览器，不创建任务，不写生产数据库。

## 结论

`8a604f5` 已经包含两个功能提交的等价内容，因此本次是 **no-op 集成**，没有缺失代码需要移植，也没有执行整提交 cherry-pick。

| 源提交 | 基线中等价提交 | 源 patch-id | 基线 patch-id | 结论 |
|---|---|---|---|---|
| `1595fb8` | `eb69091` | `0e196557b6ad7c239eeac7909db783fe1e94c7c5` | `0e196557b6ad7c239eeac7909db783fe1e94c7c5` | 已等价存在 |
| `b45b4ea` | `a094b3c` | `87c32f549f29d32d6b00cc5ed4830949d97e39a5` | `87c32f549f29d32d6b00cc5ed4830949d97e39a5` | 已等价存在 |

`git merge-base --is-ancestor eb69091 8a604f5` 和
`git merge-base --is-ancestor a094b3c 8a604f5` 均返回成功；
`git cherry -v 8a604f5` 对两个源提交均返回 `-`（patch 已应用）。

## 1595 全店变更逐项核对

以下 12 个文件全部在 `8a604f5` 中存在，且与 `eb69091` 相对各自共同父提交的 patch-id 逐项一致：

- `config/systems/1688_sku_offline.json`
- `config/systems/1688_store_account_roster_policy.json`
- `docs/DIRECT_1688_PROGRESS.md`
- `docs/PLATFORM_EXPERIENCE_KB.md`
- `docs/PROJECT_EVOLUTION.md`
- `docs/PROJECT_MEMORY.md`
- `docs/operations/1688_ALL_SHOP_STORE_ACCOUNT_CONFIGURATION_2026-08-07.json`
- `docs/operations/1688_ALL_SHOP_STORE_ACCOUNT_CONFIGURATION_2026-08-07.md`
- `docs/operations/README.md`
- `scripts/sync_1688_store_accounts_from_roster.py`
- `tests/test_sku_offline_browser.py`
- `tests/test_sync_1688_store_accounts_from_roster.py`

因此保留了 8a 的全店配置、同步器、policy、测试和验收文档，不从 `1595fb8`
重新覆盖任何文件。

## 禁止回退的差异

`1595fb8` 与 `eb69091` 的树差异包含以下 8 个 Listing Inspector/Auth 文件：

- `rpa/listing_browser_session.py`
- `rpa/sku_offline_auth.py`
- `scripts/inspect_1688_saved_draft.py`
- `scripts/run_1688_saved_draft_inspector.ps1`
- `tests/test_inspect_1688_saved_draft.py`
- `tests/test_listing_browser_session.py`
- `tests/test_run_1688_saved_draft_inspector_wrapper.py`
- `tests/test_sku_offline_auth.py`

这些文件保留 `8a604f5` 当前版本；同时保留其后的 `876d1fa` Inspector marker 原子替换、
`883503a` 下架恢复证据和 `8a604f5` 时间戳修复。

## 验证与部署边界

- Python 全量：`750/750`
- 聚水潭 TypeScript：`npm test` `28/28`、`npm run check`、`npm run build`
- Python `compileall`、PowerShell parse、`git diff --check`：通过
- 高置信度 tracked-secret scan：通过
- 本分支相对 `8a604f5` 的运行时代码差异：`0`
- 本次未部署执行机；若执行机仍为 `8a604f5`，无需为这两个已存在功能重复切换代码。

## 后续部署顺序

1. 在执行机只读确认 HEAD、工作树现场和相关任务状态。
2. 需要发布分支时只允许 `ff-only`，不得 clean/reset/stash 或覆盖现场文件。
3. 先做 Listing Inspector 只读启动验证，不创建新草稿或 Offer。
4. 再逐店执行账号/member/Profile/auth preflight；`pingcan`、`pingcan_rpa` 和 `xinbaiguang_shanzhu` 继续 blocked。
5. 通过单店 Canary 后，才进入下架/替换受控生产批次。
