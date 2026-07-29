# 1688 Listing Capacity, Audit, And Deployment Runbook

## Scope

This runbook covers the guarded first-MVP listing flow:

- platform: 1688;
- shop: one account at a time;
- category: cabinet;
- latest complete Yidian image set;
- reviewed title and title score;
- draft save and independent refresh verification;
- explicit approval;
- one submit click for one product;
- offer verification and audit writeback.

Do not run multiple Selenium workers against the same browser profile. Keep one 1688 business tab and process products serially.

## Safety contract

1. A product starts in `draft_pending` and must pass preflight before browser execution.
2. Draft execution requires `ENABLE_1688_LISTING_EXECUTION=1`.
3. Submit requires state `submit_pending`, recorded approval, `ENABLE_1688_LISTING_EXECUTION=1`, and `ENABLE_1688_LISTING_SUBMIT=1`.
4. Submit mode reapplies and reads back the configured send address and exact buyer-protection value `15天发货` with code `swtfh`.
5. Empty addresses, wrong buyer-protection values, or visible required-field warnings block submit.
6. Never retry a failed submit until the success URL, Offer ID, product-management page, and formal audit records have all been checked.
7. `reconcile-submit` is only for a captured 1688 success page that contains a numeric Offer ID and complete required-field evidence.
8. A completed task in `offer_written_back` is immutable operational evidence. Do not rerun draft or submit for it.

## Image capacity gate

The listing worker fails closed when the 1688 image bank cannot accept uploads.

1. Run `scripts/probe_1688_image_picker.py` with two images before a new or resumed product.
2. The probe must return `status=passed`, `uploaded_count=probe_count>=2`, and Alibaba CDN URLs only.
3. If all existing albums are full, the worker creates a new album with `album_access=private`, verified UI label `不公开`, and capacity `500`.
4. The new album is selected by exact name before upload. Failure to verify private access blocks the task.
5. Probe evidence must match the same `pending_draft_id`, be no older than 30 minutes, and confirm that no draft or offer was created by the probe.
6. A failed, partial, stale, mismatched, or non-Alibaba probe cannot resume the task.
7. External Yidian URLs are not a persistence fallback. Detail images must be uploaded to the 1688 image bank.
8. The real picker accepts at most three images per upload batch. Detail upload checkpoints may resume only from contiguous completed batches.

```powershell
python scripts\probe_1688_image_picker.py `
  --payload <payload-json> `
  --count 2 `
  --evidence-output <capacity-evidence-json>

python scripts\run_1688_listing_task.py `
  --shared-runtime-root <shared-runtime-root> `
  --payload <payload-json> `
  --mode resume `
  --capacity-evidence <capacity-evidence-json> `
  --operator <operator> `
  --output <payload-json>
```

## Formal audit database

Production modes `resume`, `draft`, `approve`, `submit`, `reconcile-submit`, and `writeback` use:

- `app.ali1688_listing_task`;
- `app.ali1688_listing_execution`;
- `app.ali1688_listing_audit`.

The target is `JSReportReplica.app`. Resolve its writer credentials through the shared 1688 secret provider or paired environment variables. Never put credentials in this repository or payload files.

```powershell
python scripts\apply_1688_listing_audit_ddl.py `
  --shared-runtime-root <shared-runtime-root>

python scripts\apply_1688_listing_audit_ddl.py `
  --shared-runtime-root <shared-runtime-root> `
  --apply
```

The first command validates and rolls back. Use `--apply` only after validation succeeds.

## Source lifecycle gate

Refresh the source row immediately before any browser action. The command is read-only, resolves the database account from Windows Credential Manager, and selects an installed SQL Server driver when the requested driver is unavailable.

```powershell
python scripts\query_1688_listing_source.py `
  --shared-runtime-root <shared-runtime-root> `
  --sku <company-sku> `
  --output <source-state-json>
```

Require `gate=passed`, a fresh `checked_at`, and the exact current values `enabled=1`, `stock_disabled=0`, `other_5=销售`, and `item_type=成品`. Do not reuse a stale source-state file, and do not treat this database gate as browser, draft, or Offer acceptance.

## Execution machine deployment

Recommended repository path: `E:\1688\ERP-auto-listing`. Project path: `E:\1688\ERP-auto-listing\furniture-uploader`. Shared crawler runtime: `E:\1688\1688-script-new`.

1. Check out the independent auto-listing commit into the project path.
2. Install the Python dependencies from `requirements.txt` and confirm Edge plus a compatible EdgeDriver are available.
3. Confirm the shared runtime contains `src\security\secret_provider.py` and that the external database configuration resolves the `YYDD/1688/database/app-writer` credential reference.
4. Validate the audit DDL and read back its three tables.
5. Configure the dedicated Edge user-data/profile paths outside Git.
6. The account owner logs into 1688 once in that profile and completes any CAPTCHA or MFA manually.
7. Keep the interactive Windows user session logged in. A locked session is acceptable; logout or reboot ends the reusable browser session.
8. Run one Selenium process at a time. Do not open parallel probes, draft workers, or submit workers.

Deployment verification:

```powershell
Set-Location E:\1688\ERP-auto-listing\furniture-uploader
python --version
python -m pip install -r requirements.txt
python -m compileall -q rpa scripts tests
python -m unittest discover -s tests -p "test_*.py"
python scripts\apply_1688_listing_audit_ddl.py `
  --shared-runtime-root E:\1688\1688-script-new
```

## Three-product canary

Use three new cabinet SKUs that have not already been drafted or published. Complete every phase for one SKU before starting the next. Candidate selection must fail closed unless the current `JSDataMiddlePlatform.dbo.jst_sku` row has `enabled=1`, `stock_disabled=0`, and `other_5=销售`; a prior probe or payload cannot override a later lifecycle change.

For each SKU:

1. Produce a unique `listing_task_payload_v1` JSON with source eligibility evidence, duplicate-check evidence, the selected scored title, price, inventory `999`, latest complete Yidian images, company SKU names, and logistics values.
2. Run preflight and require `status=passed`.
3. Run the two-image capacity probe. If the payload is blocked from a previous capacity failure, record `resume` with fresh matching evidence.
4. Run draft mode with `--skip-login` against the owner-established session.
5. Run `inspect_1688_saved_draft.py` in a separate browser refresh and require all reviewed fields, square main image, full detail image count, SKU specs, price, inventory, and logistics to match.
6. Review the draft manually, then record approval.
7. Enable submit for this command only and perform one submit attempt.
8. Verify the Offer ID and URL before writeback. If tracking failed after navigation, inspect the captured success evidence before considering `reconcile-submit`.
9. Record writeback and inspect the formal audit rows.
10. Close or reuse the same business tab before proceeding to the next SKU.

```powershell
$env:ENABLE_1688_LISTING_EXECUTION = "1"

python scripts\run_1688_listing_task.py `
  --payload <payload-json> `
  --mode preflight `
  --output <preflight-json>

python scripts\run_1688_listing_task.py `
  --shared-runtime-root E:\1688\1688-script-new `
  --payload <payload-json> `
  --mode draft `
  --skip-login `
  --operator <operator> `
  --output <draft-json>

python scripts\inspect_1688_saved_draft.py `
  --payload <draft-json> `
  --output <independent-refresh-json>

python scripts\run_1688_listing_task.py `
  --shared-runtime-root E:\1688\1688-script-new `
  --payload <draft-json> `
  --mode approve `
  --operator <approver> `
  --output <approved-json>

$env:ENABLE_1688_LISTING_SUBMIT = "1"
python scripts\run_1688_listing_task.py `
  --shared-runtime-root E:\1688\1688-script-new `
  --payload <approved-json> `
  --mode submit `
  --skip-login `
  --operator <operator> `
  --output <submitted-json>
Remove-Item Env:ENABLE_1688_LISTING_SUBMIT

python scripts\run_1688_listing_task.py `
  --shared-runtime-root E:\1688\1688-script-new `
  --payload <submitted-json> `
  --mode writeback `
  --offer-url <verified-offer-url> `
  --operator <operator> `
  --output <complete-json>

python scripts\inspect_1688_listing_audit.py `
  --task-id <task-id> `
  --shared-runtime-root E:\1688\1688-script-new
```

Remove `ENABLE_1688_LISTING_EXECUTION` when the canary session ends. Expand to the 20-link pilot only after all three products reach `offer_written_back` with matching independent-refresh and formal audit evidence.

## Accepted reference product

The reference product was accepted with real account, real page, real image API, and formal audit data on 2026-07-23:

- task: `1688-listing-CTG029342N1021V01`;
- draft: `6a605674e4b09b97d533e992`;
- Offer ID: `1068081966540`;
- Offer URL: `https://detail.1688.com/offer/1068081966540.html`;
- final state: `offer_written_back`;
- main image: square, `1067x1067`;
- detail images: `50/50`;
- price: `325`;
- inventory: `999`;
- logistics: `55 x 45 x 55 cm`, `17000 g`;
- submit-time required fields: non-empty address and `15天发货 / swtfh`.

Do not rerun draft or submit for this task. Its original submit execution row is `failed` because the old tracker lost the response after successful navigation; immutable `submit_succeeded` and `offer_written_back` events reconcile the actual successful publication without a second click.
