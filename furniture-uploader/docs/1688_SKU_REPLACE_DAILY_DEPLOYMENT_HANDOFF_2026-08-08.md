# 1688 SKU Replace Daily Deployment Handoff (2026-08-08)

## Scope

- Repository worktree: `D:\script_files\ERP_stop_sale_targeted_recovery_20260807`
- Branch: `codex/erp-stop-sale-replace-scheduler-20260808`
- Production target: `PC-20210622ARIU / E:\1688\ERP-final-75368f8`
- Crawler repository and OK dashboard are out of scope.
- Existing successful 1688 Stop-Sale operations must not be replayed.

## Authoritative Identity

| account_key | business store_name | login/member | Jushuitan selector | scheduling state |
|---|---|---|---|---|
| `pingcan` | `阿里巴巴-常州平灿家居有限公司` | `平灿家居` / `b2b-2221733989984a3731` | same as business name | enabled canonical owner |
| `pingcan_rpa` | historical only | shared member | not applicable | disabled, superseded by `pingcan` |
| `xinbaiguang_shanzhu` | `新佰广1688` | `b2b-2207895056366baaa8` | `阿里巴巴-新佰广` | enabled |

The Xinbaiguang mapping is backed by a real read-only executor Probe. The picker accepted `阿里巴巴-新佰广`; returned rows retained `新佰广1688`. Checked-in evidence:

- `docs/operations/1688_XINBAIGUANG_JUSHUITAN_IDENTITY_EVIDENCE_2026-08-08.json`
- Evidence file SHA-256: `484835ef897f83bf65959d06ac16771b005bc0b14199fd3d4e6ec931d7d6f23d`
- Raw exact-row query evidence SHA-256: `05a6094863d10c865bee417d9372f1bd71d2b30f39b8515c5c4577d2399d43e4`

## Configuration Sync Evidence

Fresh inputs are under `artifacts\stop_sale_replace_readonly_closeout_20260808` outside the tracked product tree.

- accounts revision/hash: `20260808-1` / `6a3a65f82bdb75b1ebf94f3d40f0f1a95410369383ece88563c584d94243ba8f`
- accounts file SHA-256: `19aec40572c64d1de1fc0a2c5384b7935e5048d0165c087250df38ac718e2780`
- Targeted dry-run: `erp_store_binding_targeted_dry_run_20260808.json`
- Targeted Apply: `erp_store_binding_targeted_apply_20260808.json`
- Apply result: 18 -> 19 bindings, added only `xinbaiguang_shanzhu`, final policy count matched, `pingcan_rpa` absent.

Full sync is intentionally still fail closed on the unrelated `muke_lixiang` roster/Preview punctuation drift. Do not normalize or suppress that error in this deployment.

## Runtime Contracts

- Business `store_name` remains unchanged in task, Saga, operation key, task id, audit and Jushuitan row matching.
- `jushuitan_store_name` is used only for the Jushuitan store picker and is carried as separate evidence.
- Missing exact mapping fails before a 1688 browser action and records `account_mapping` for the affected store.
- `运营自行组合替换` is `business_skipped` with `异常原因=组合货号`; no lease, Saga, browser or Jushuitan action is started.
- `system_prompt` stores the platform text, fails only the current SKU, continues the store, is non-retryable, and is never counted as success.

## Windows Schedule

- Manager: `scripts\manage_1688_sku_replace_daily.py`
- Installer/launcher: `scripts\manage_1688_sku_replace_daily_task.ps1`
- Daily task: `YYDD-1688-Replace-Daily`, default 14:00, S4U, Highest, `IgnoreNew`.
- Launcher task: `YYDD-1688-Replace-Daily-Launcher`, SYSTEM, no trigger.
- Source: `JSReportReplica.app.op_stop_sale`, handling `全渠道替换`.
- Manager delegates all writes to the existing Replace pipeline and preserves account lease, browser-slot lease, Saga/Outbox and Jushuitan global-lock contracts.

## Deployment

After the branch commit is integrated into the executor's authoritative ERP branch:

```powershell
PowerShell.exe -NoProfile -ExecutionPolicy Bypass `
  -File E:\1688\ERP-final-75368f8\furniture-uploader\scripts\manage_1688_sku_replace_daily_task.ps1 `
  -Action install `
  -DailyAt 14:00 `
  -ProjectRoot E:\1688\ERP-final-75368f8\furniture-uploader `
  -SharedRuntimeRoot E:\1688\1688-script-new `
  -JushuitanRoot E:\1688\ERP-final-75368f8\jushuitan-sku-offline-batch
```

Then run `-Action status` and a read-only `-Action preview`. Confirm task Action, Principal, trigger, next run, Preview identity and candidate counts before any write Canary.

## Acceptance Boundary

Accepted now:

- canonical Pingcan ownership and disabled historical `pingcan_rpa` contract;
- real read-only Xinbaiguang Jushuitan identity Probe;
- local 19-binding targeted Apply with hashed inputs;
- code/tests for identity separation, fail-closed mapping, business skips, system prompts and daily idempotency.

Not yet accepted in this document:

- executor deployment of this branch;
- creation and natural/equivalent start of both Replace tasks;
- one-item real replacement write and independent page refresh;
- matching Replace Run/Item, Saga, Outbox and Jushuitan terminal evidence.

Do not claim production completion until the four items above are evidenced. Do not use a successful Stop-Sale record as Replace Canary evidence.
