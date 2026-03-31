# AI Continuity Guide

更新时间：2026-03-24

## 目标

让后续 AI 代理在新的会话里也能快速恢复项目上下文、读取当前状态，并继续正确推进工作。

## 核心原则

AI 不会像人一样“天然记住”历史，所以必须把项目记忆外部化到仓库里。

这套外部化记忆至少包括 4 层：

1. `AGENTS.md`
2. 当前事实文档
3. 演化文档
4. 经验知识库

## 本项目推荐的自动学习入口

### 第一层：仓库根目录 `AGENTS.md`

作用：

- 告诉 AI 开工前必须先读哪些文件
- 告诉 AI 要跑哪些验证命令
- 告诉 AI 改完后必须更新哪些文档

这是最关键的一层，因为很多 coding agent 会自动读取它。

### 第二层：`docs/PROJECT_MEMORY.md`

作用：

- 保存“当前事实”
- 让 AI 快速知道现在做到哪一步、还差什么

### 第三层：`docs/PROJECT_EVOLUTION.md`

作用：

- 保存“为什么这么做”
- 记录策略改变和里程碑

### 第四层：`docs/PLATFORM_EXPERIENCE_KB.md`

作用：

- 保存“同平台可复用经验”
- 让后续 AI 不必每次重新踩坑

## 让其他 AI 自动学习的具体方法

### 方法 1：使用 `AGENTS.md`

推荐原因：

- OpenAI Codex 已明确支持通过仓库内的 `AGENTS.md` 指导代理工作
- `AGENTS.md` 社区标准本身就是为多种 AI coding agent 设计的

在本项目里，建议所有新会话都从根目录 `AGENTS.md` 开始。

### 方法 2：固定“接手读取顺序”

无论是人还是 AI，统一先读：

1. `AGENTS.md`
2. `docs/README.md`
3. `docs/PROJECT_MEMORY.md`
4. `docs/DIRECT_1688_PROGRESS.md`
5. `docs/PLATFORM_EXPERIENCE_KB.md`
6. `D:\script_files\1688\README.md`

### 方法 3：把“更新文档”设成流程动作

不是做到最后想起来再补文档，而是规定：

- 修一个平台级问题，就更新 `PLATFORM_EXPERIENCE_KB.md`
- 里程碑变化，就更新 `DIRECT_1688_PROGRESS.md`
- 策略变化，就更新 `PROJECT_EVOLUTION.md`
- 当前状态变化，就更新 `PROJECT_MEMORY.md`

### 方法 4：保留标准 smoke 样本

AI 只读文档还不够，还要有标准回归入口：

- [templates/1688_corner_table_smoke.csv](D:/script_files/ERP/furniture-uploader/templates/1688_corner_table_smoke.csv)
- [templates/1688_sku_offline_sample.csv](D:/script_files/ERP/furniture-uploader/templates/1688_sku_offline_sample.csv)

这样 AI 接手后可以马上复跑，不需要重新找样本。

### 方法 5：把“事实”和“建议”分开写

例如：

- `PROJECT_MEMORY.md` 记录当前事实
- `PROJECT_EVOLUTION.md` 记录演化过程
- `PLATFORM_EXPERIENCE_KB.md` 记录平台经验

这样 AI 不容易把旧建议误当成当前事实。

## 推荐更新协议

每次里程碑完成后，至少同步更新：

- `docs/PROJECT_MEMORY.md`
- `docs/DIRECT_1688_PROGRESS.md`
- `docs/PROJECT_EVOLUTION.md`
- `docs/PLATFORM_EXPERIENCE_KB.md`

如涉及新的 AI 接手规则，再更新：

- `AGENTS.md`
- `docs/AI_CONTINUITY_GUIDE.md`

## 对其他 AI 工具的兼容建议

- 如果工具支持仓库规则文件，优先指向 `AGENTS.md`
- 如果工具支持额外上下文文件，把 `docs/PROJECT_MEMORY.md` 设为必读文件
- 如果是 1688 项目管理或需求会话，把 `D:\script_files\1688\README.md` 也设为必读文件
- 如果工具支持启动脚本或工作流模板，把单测和 `doctor` 命令写进去

## 结论

想让其他 AI “自动学习”，本质不是让它自己记忆，而是：

- 在仓库内提供稳定入口
- 把当前事实结构化
- 把经验沉淀成知识库
- 把更新动作流程化

这样换任何一个新的 AI，会话一开始都能快速恢复上下文。

## 制度化建议

为了让“自动学习”长期有效，建议把文档维护也流程化：

- 制度文件：[DOC_MAINTENANCE_POLICY.md](D:/script_files/ERP/furniture-uploader/docs/DOC_MAINTENANCE_POLICY.md)
- 更新模板：[DOC_UPDATE_TEMPLATES.md](D:/script_files/ERP/furniture-uploader/docs/DOC_UPDATE_TEMPLATES.md)

后续 AI 不应只知道“读哪些文档”，还应知道：

- 什么时候必须改文档
- 应该改哪份文档
- 改文档时用什么模板
