# 决策记录

## 基本信息

- 决策标题：平台中台方案的草案层与真源层分离
- 决策日期：2026-04-02
- 决策人：项目团队整理
- 相关需求：平台上架下架任务与数据中台

## 背景

当前关于“平台上架下架任务与数据中台”的讨论，已经在 `.trae/specs/platform-mediation-platform/` 形成 `spec.md`、`tasks.md` 和 `database_schema.sql` 三份草案文件，但这些文件更适合作为 AI 讨论与迭代层，不适合直接视为正式真源。

同时，团队级知识库 `team-playbook` 明确建议把正式项目文档沉淀到结构化真源中，而不是只保留在聊天记录或草案目录里。考虑到当前并不存在独立可推送的 `docs` Gitee 仓，正式项目文档需要和项目代码一起沉淀在现有 `ERP` 仓中。

## 备选方案

- 方案 A：直接把 `.trae/specs/` 作为长期真源
- 方案 B：继续只在聊天里维护方案，不新增正式文档
- 方案 C：保留 `.trae/specs/` 作为草案层，在项目同仓 `docs/` 目录中补正式文档

## 最终结论

采用方案 C：

- `.trae/specs/platform-mediation-platform/` 继续作为草案层
- `D:\script_files\ERP\platform-mediation-platform\docs\requirements/`、`docs/plans/`、`docs/architecture/`、`docs/decisions/` 作为正式真源层
- 当前已新增正式文档：
  - `PLATFORM_MEDIATION_REQUIREMENT_INTAKE.md`
  - `PLATFORM_MEDIATION_TECH_PLAN.md`
  - `PLATFORM_MEDIATION_ARCHITECTURE.md`

## 决策理由

- 理由 1：符合 `team-playbook` 推荐的文档组织方式
- 理由 2：保留草案灵活性，同时让正式方案可长期维护
- 理由 3：便于后续 AI、业务和技术协作时明确“哪个版本可执行、哪个版本可讨论”

## 影响评估

- 对业务的影响：
  - 方案入口更清晰，后续讨论不会只停留在草案层
- 对技术的影响：
  - 后续开发和数据库收口有了正式真源可对齐
- 对进度的影响：
  - 需要一轮额外文档整理，但能减少后续反复解释成本
- 对成本的影响：
  - 文档维护成本略有增加，但长期协作成本下降

## 后续动作

- 动作 1：继续把 `spec/tasks/database_schema` 草案和真源文档对齐
- 动作 2：在进入正式开发时继续补更细的阶段决策记录、实施变更和交接结论

## 复盘条件

当平台中台进入正式开发主线，或团队决定全面迁移到 `team-playbook` 的完整项目结构时，重新审视本次文档分层决策。
