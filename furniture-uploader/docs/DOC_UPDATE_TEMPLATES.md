# Documentation Update Templates

更新时间：2026-03-24

这份文档提供标准更新模板，方便人和 AI 快速补文档。

## 模板 1：里程碑完成

适用场景：

- 完成一个主要功能
- 打通一个主流程节点

推荐同步更新：

- `PROJECT_MEMORY.md`
- `DIRECT_1688_PROGRESS.md`
- `PROJECT_EVOLUTION.md`

可复制模板：

```md
## YYYY-MM-DD Update

- Completed:
- Verified:
- Remaining blocker:
- Recommended next step:
```

## 模板 2：平台经验沉淀

适用场景：

- 抓到 live selector
- 找到更稳定的实现方式
- 修掉一个会反复出现的平台坑

推荐同步更新：

- `PLATFORM_EXPERIENCE_KB.md`

可复制模板：

```md
## Experience: <short title>

### Context

-

### Verified finding

-

### Reusable code

-

### Pitfall

-
```

## 模板 3：交接文档补充

适用场景：

- 代码进入新阶段
- 需要让新同事或新 AI 快速接手

推荐同步更新：

- `DELIVERY_HANDOVER_2026-03-24.md`

可复制模板：

```md
## New Handover Note

- What changed:
- What is now reliable:
- What is still not verified:
- What the next owner should do first:
```

## 模板 4：AI 接手规则变化

适用场景：

- 文档入口改变
- AI 启动顺序改变
- 新增必须读取文件

推荐同步更新：

- `AGENTS.md`
- `AI_CONTINUITY_GUIDE.md`

可复制模板：

```md
## AI Workflow Update

- Required read order:
- Required validation:
- Required docs to update after code changes:
```

## 模板 5：上线边界变化

适用场景：

- 从 manual review 切到 draft
- 从 draft 切到 submit
- 新增 production risk

推荐同步更新：

- `GO_LIVE_CHECKLIST.md`
- `PROJECT_MEMORY.md`
- `PROJECT_EVOLUTION.md`

可复制模板：

```md
## Go-Live Boundary Update

- Previous boundary:
- New boundary:
- Verified evidence:
- Risk not yet closed:
```

## 模板 6：会话收尾摘要

适用场景：

- 一次较长开发会话结束时
- 方便下一个 AI 无缝接手

推荐同步更新：

- `PROJECT_MEMORY.md`
- 必要时 `DELIVERY_HANDOVER_2026-03-24.md`

可复制模板：

```md
## Session Summary

- Finished in this session:
- Files changed:
- Verified by:
- Still open:
```
