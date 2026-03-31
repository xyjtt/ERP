# 1688 Buyer Protection Persistence - 2026-03-26

## Scope

Fix the new 1688 publish-page rule where `买家保障发货时间` must persist in draft save and survive refresh.

## Verified Result

- Live run passed: `logs/run_reports/20260326_143357.jsonl`
- Refresh verification passed for stepped ship-time schedule
- Persisted schedule:
  - `sstfh` / `30天发货` for quantity `1-2`
  - `sswtfh` / `45天发货` for quantity `3+`

## Root Cause

The stepped buyer-protection schedule was being saved under stock-only supply type:

- `supplyType=[1]`

On the new publish flow, that caused 1688 to drop the stepped ship-time rows after refresh and restore the field to an invalid placeholder state.

## Fix

Implemented in [browser_rpa.py](D:/script_files/ERP/furniture-uploader/rpa/browser_rpa.py):

- When buyer protection uses a stepped schedule, force `supplyType=[1,2]`
- Sync `global.renderData.cbuSupplyType`
- Sync `buyerProtection.selectedServices.dsc`
- Sync `buyerProtection.selectedServices.jgdz`
- Sync `buyerProtection.spsCode`
- Keep draft-save verification on refreshed page state

## Notes

- This was verified on the real 1688 new publish page, not only by unit tests.
- The worktree contains many unrelated in-progress changes, so this fix was not isolated into a clean commit in this round.
