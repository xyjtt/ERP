# Project Memory

Updated: 2026-03-18

## Project Positioning

- Project: `furniture-uploader`
- Current operating entry: 聚水潭 Web
- First target platform: 1688
- Current execution engine: Python + Selenium
- Future execution engine: OpenClaw
- Publish strategy: manual review by default, with support for either save-draft or direct submit

## What Is Already Done

- Core runtime scaffold completed
- SQL Server logging and knowledge-base persistence completed
- `publish_task / publish_result / publish_error_log / match_candidate_history / category_mapping_history / store_default_template` implemented
- Emoji sanitization before publish implemented
- Match-history retry skeleton implemented
- Local config override merge implemented
- Local bootstrap script implemented
- Environment preflight implemented
- Database preflight implemented
- Selector probe tool implemented
- Offline unit tests implemented and passing

## Current Verified State

- Python dependencies are installed
- `--check-env` passes
- unit tests pass
- local bootstrap script runs successfully
- SQL driver auto-detection works on this machine
- DB connectivity is blocked only by real credential availability

## Remaining Real Blockers

- Real selectors are still missing in:
  - `config/systems/jushuitan.local.json`
  - `config/platforms/1688.local.json`
- Real SQL Server password still needs to be provided
- First live end-to-end browser walkthrough still needs to be completed
- Final success-page link ID / URL extraction still needs live validation

## 2026-03-24 Refresh

- `config/platforms/1688.json` is now a live-tested baseline for the current 1688 publish page
- Category automation is live-validated for `家装建材 > 客厅家具 > 角几/边几`
- A no-submit end-to-end dry run for `1688_direct` now completes successfully with one smoke product
- The remaining 1688-specific blocker is no longer "fill the page", but "validate the real post-submit success result on a genuine submitted item"

## Recommended Next Action

1. Set the real DB password and rerun `--check-db`
2. Use `rpa/selector_probe.py` to capture the four critical pages
3. Fill local selector configs
4. Run `--doctor`
5. Run `--validate-only`
6. Run a first live dry run with `--limit 1`

## Important Local Files

- `config/database.local.json`
- `config/operator_config.local.json`
- `config/systems/jushuitan.local.json`
- `config/platforms/1688.local.json`
- `docs/GO_LIVE_CHECKLIST.md`
- `docs/SELECTOR_PROBE_GUIDE.md`

## Permanent Note

If a future session does not remember this project automatically, start by reading this file and `docs/GO_LIVE_CHECKLIST.md`.
