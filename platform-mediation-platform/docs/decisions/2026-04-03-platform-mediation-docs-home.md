# 决策记录

## 基本信息

- 决策标题：平台中台项目文档真源迁入 ERP 同仓
- 决策日期：2026-04-03
- 决策人：项目团队整理
- 相关需求：平台上架下架任务与数据中台

## 背景

平台中台项目的正式文档最初整理在 `D:\script_files\docs\` 下，但当前 Gitee 账号并不存在独立的 `docs` 远端仓库，因此该路径只能作为本地整理结果，无法成为稳定的远端真源。

与此同时，平台中台第一阶段代码已经在 `D:\script_files\ERP\platform-mediation-platform\` 建立并推送到现有 `ERP` 仓。为了避免文档和代码分离、远端不可落地、后续协作时口径不一致，文档需要迁入同一项目仓。

## 备选方案

- 方案 A：继续把 `D:\script_files\docs\` 作为唯一真源
- 方案 B：把项目文档放入 `team-playbook`
- 方案 C：把项目正式文档迁入 `ERP/platform-mediation-platform/docs/`

## 最终结论

采用方案 C：

- `.trae/specs/platform-mediation-platform/` 继续作为草案层
- `D:\script_files\ERP\platform-mediation-platform\docs\` 作为正式真源层
- `D:\script_files\docs\` 保留为历史整理副本，不再作为该项目唯一远端真源

## 决策理由

- 理由 1：现有 `ERP` 仓已经存在且可正常推送到 Gitee
- 理由 2：项目文档与项目代码同仓，最容易保持同步
- 理由 3：`team-playbook` 更适合沉淀通用规则，不适合承接项目专属真源

## 后续动作

- 动作 1：后续所有平台中台正式文档优先更新到 `ERP/platform-mediation-platform/docs/`
- 动作 2：新的关键决策继续写入 `ERP/platform-mediation-platform/docs/decisions/`
- 动作 3：待项目稳定后，再把可复用经验抽取沉淀到 `team-playbook`

