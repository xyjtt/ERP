# 1688 Stop-Sale S4U Deployment Handoff

Date: 2026-08-06
Owner: ERP Stop-Sale line
Base: `68b9e31` (Listing S4U fallback)

## Scope

This change is limited to the Stop-Sale scheduled-task wrapper. It does not
modify Crawler code, the Listing launcher, database business rows, Saga rows,
or Outbox rows.

## Historical Jushuitan classification

Read-only artifact:

`furniture-uploader/artifacts/c-line-jushuitan-20260806/jushuitan_terminal_failures_20260803.json`

The query covered `daily_20260803_123001_830715` and required exactly 11
`failed_terminal` Outbox rows. The gate passed: 11 of 11 rows were returned.
Every row has `last_error_code=task_not_found`; the original message says that
no exact Store/Product/SKU/platform-code row matched. The historical evidence
also records `ali1688_status=already_offline`. These are business terminals,
not transient Jushuitan process failures, and must not be retried or sent to a
new Outbox operation.

The individual operation keys, business keys, attempt counts, and raw error
messages are in the JSON artifact. One row has no current `stop_sale_item`
Jushuitan fields because it was terminalized from the exact historical Outbox
record; the Outbox and Saga CAS evidence remains authoritative for that row.

## Scheduler contract

`YYDD-1688-Stop-Sale-Daily` is installed with:

- Principal: the current Administrator account
- LogonType: `S4U`
- RunLevel: `Highest`
- Daily trigger: the supplied `DailyAt` value (production remains 12:30)
- Action: the existing `manage_1688_stop_sale_daily_task.ps1 -Action run`

`YYDD-1688-Stop-Sale-Daily-Launcher` is an on-demand SYSTEM task with no
independent daily trigger. It calls the same wrapper with `-Action launch`.

The wrapper's `start`/`launch` operation is guarded against duplicate starts:

1. A Running formal task returns `already_running`.
2. An S4U formal task uses `Start-ScheduledTask` and verifies a changed run
   observation.
3. An older Interactive formal task uses the existing `Schedule.Service`
   `RunEx(..., flags=4, sessionId, ...)` path and requires exactly one matching
   Administrator interactive session.

The launcher does not create a second batch, draft, Outbox row, or browser
session. It only invokes the formal task through the same guarded wrapper.

## Deployment gate and verification

Before replacing the executor wrapper, pause new task acquisition and capture
fresh `active`/`in-flight` evidence for the affected account and Stop-Sale
runtime. Stop the formal Worker only after the fresh gate is clear. Deploy with
`git merge --ff-only`; preserve all pre-existing tracked and untracked executor
files. Restore the Worker and queue state after the file switch.

Post-deployment evidence must keep these classes separate:

- task Principal, Action, trigger, LastRunTime, and LastTaskResult;
- preview/Canary run and latest summary;
- 1688 business result and page/system-prompt classification;
- Saga state and error fields;
- Outbox state and Jushuitan terminal result;
- lock and account lease release.

`LastTaskResult=0` or a successful preview is not a full business acceptance.
