# Platform Mediation Docs

这里是 `platform-mediation-platform` 项目的正式文档真源目录。

当前文档分层约定：

- 草案层：`D:\script_files\.trae\specs\platform-mediation-platform\`
- 真源层：`D:\script_files\ERP\platform-mediation-platform\docs\`

## 首次接手先读

如果是第一次接手这个项目，建议优先按下面顺序阅读：

1. `docs/handoff/PLATFORM_MEDIATION_HANDOFF_2026-04-14.md`
2. `docs/handoff/PLATFORM_MEDIATION_QUICKSTART_FOR_NEXT_DEV.md`
3. `docs/handoff/PLATFORM_MEDIATION_VERIFICATION_RUNBOOK.md`
4. `docs/requirements/PLATFORM_MEDIATION_REQUIREMENT_INTAKE.md`
5. `docs/plans/PLATFORM_MEDIATION_TECH_PLAN.md`
6. `docs/architecture/PLATFORM_MEDIATION_ARCHITECTURE.md`
7. `docs/plans/PLATFORM_MEDIATION_PHASE1_WORKPACKAGES.md`
8. `docs/plans/PLATFORM_MEDIATION_API_CONTRACT_V1.md`
9. `docs/checklists/PLATFORM_MEDIATION_PHASE1_START_CHECKLIST.md`

## 目录说明

- `handoff/`：交接入口、快速接手指南、验证运行手册
- `requirements/`：需求输入与范围口径
- `plans/`：技术方案、工作包、API 契约
- `architecture/`：架构设计与数据模型
- `decisions/`：关键决策、进度快照、阶段基线
- `roadmap/`：阶段路线图
- `checklists/`：启动与验收检查单

## 当前交接结论

- 当前阶段仍是第一阶段：`1688 中台 v1`
- 已完成文档真源收口、SQL Server 基线打通、`1688 validate` 真实桥接
- 当前最适合继续推进的方向是：
  - `1688 adapter v1` 的受控 `execute` 桥接
  - 后台调度 worker / 回流处理器
  - 映射版本装载与执行
  - 简版运营入口

## 维护约定

- 需求讨论先进入草案层，再同步到真源层
- 每完成一个阶段性能力，至少补一份 dated 决策或进度记录
- 涉及接手方式、验证方式、运行入口变化时，优先更新 `handoff/`
