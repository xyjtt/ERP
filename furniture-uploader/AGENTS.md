# AGENTS.md

This repository is an AI-friendly working repo for `furniture-uploader`.

## Start Here

Before making changes, read these files in order:

1. `docs/README.md`
2. `docs/PROJECT_MEMORY.md`
3. `docs/DIRECT_1688_PROGRESS.md`
4. `docs/DELIVERY_HANDOVER_2026-03-24.md`
5. `docs/AI_CONTINUITY_GUIDE.md`
6. `docs/DOC_MAINTENANCE_POLICY.md`

## Project Scope

- Current mainline is `1688_direct`
- The current goal is stable 1688 publish automation
- Default final behavior is conservative manual review
- Code supports `manual`, `draft`, and `submit`, but live validation is still required before enabling automated final actions

## Required Validation

Run these after code or documentation changes when relevant:

```bash
python -m unittest discover -s tests -p "test_*.py"
python rpa/main.py --system 1688_direct --platform 1688 --file templates/1688_corner_table_smoke.csv --doctor
```

If you are working on the live 1688 flow, also prefer:

```bash
python rpa/main.py --system 1688_direct --platform 1688 --file templates/1688_corner_table_smoke.csv --limit 1 --skip-login
```

## Documentation Update Rules

If you complete a milestone or fix a platform-level issue, update:

- `docs/PROJECT_MEMORY.md`
- `docs/DIRECT_1688_PROGRESS.md`
- `docs/PROJECT_EVOLUTION.md`
- `docs/PLATFORM_EXPERIENCE_KB.md`

If you change AI onboarding or handoff conventions, also update:

- `docs/AI_CONTINUITY_GUIDE.md`
- `docs/DELIVERY_HANDOVER_2026-03-24.md`

When making documentation updates, prefer reusing:

- `docs/DOC_MAINTENANCE_POLICY.md`
- `docs/DOC_UPDATE_TEMPLATES.md`

## Working Rules

- Prefer the current effective docs over historical archive docs
- Keep smoke templates stable
- Do not enable automated final submit without explicit live validation
- When selector behavior changes, record the live finding in the knowledge base
- Do not finish a milestone without updating the corresponding docs
