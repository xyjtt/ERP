# Project Evolution

Updated: 2026-03-18

## Purpose

This file is the long-term evolution log for the project.

Use it to record:

- major architecture changes
- confirmed business rule changes
- new blockers
- removed blockers
- go-live decisions
- rollback notes

## Current Stage

- Stage: pre-go-live integration
- Primary blocker: real selectors
- Secondary blocker: real DB credential validation

## Evolution Timeline

### 2026-03-17

- Project direction changed from direct 1688 publishing to 聚水潭 Web as the operational entry
- First platform fixed to 1688
- SQL Server 2012 adopted as shared knowledge base
- Direct publish chosen instead of draft
- Match-history retry logic added
- Emoji sanitization added

### 2026-03-18

- Python dependencies fully installed
- Bootstrap script verified
- Offline tests added and passing
- DB driver auto-detection added
- Selector probe tool added
- Project memory and go-live checklist added

## Next Evolution Trigger

When the first live dry run is completed, update this file with:

- success or failure
- failed step
- selector fixes applied
- final publish result capture status
