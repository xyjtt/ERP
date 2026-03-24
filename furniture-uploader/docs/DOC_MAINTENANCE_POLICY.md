# Documentation Maintenance Policy

更新时间：2026-03-24

## 目的

把项目文档维护从“想起来再补”变成“开发流程的一部分”。

这份制度同时约束：

- 人类开发者
- AI coding agent

## 核心原则

### 1. 文档不是收尾动作，而是交付物的一部分

如果代码行为、项目阶段、平台经验或接手方式发生变化，就必须同步更新文档。

### 2. 当前事实与历史演化分开维护

- 当前事实写进 `PROJECT_MEMORY.md`
- 项目进度写进 `DIRECT_1688_PROGRESS.md`
- 策略变化写进 `PROJECT_EVOLUTION.md`
- 平台经验写进 `PLATFORM_EXPERIENCE_KB.md`

### 3. 能被复用的经验必须沉淀，不允许只留在会话里

只要某个问题未来还有可能再次出现，就要写入知识库。

## 文档分工

### `docs/PROJECT_MEMORY.md`

用于记录：

- 当前项目定位
- 当前已验证事实
- 当前 blocker
- 当前建议下一步

### `docs/DIRECT_1688_PROGRESS.md`

用于记录：

- 里程碑状态
- 当前推进节点
- 最近提交
- 下一步计划

### `docs/PROJECT_EVOLUTION.md`

用于记录：

- 方向变化
- 策略变化
- 重大架构或执行方式变化

### `docs/PLATFORM_EXPERIENCE_KB.md`

用于记录：

- 平台 live selector
- 已验证的稳定实现
- 可复用代码
- 平台级踩坑

### `docs/DELIVERY_HANDOVER_2026-03-24.md`

用于记录：

- 适合直接交给别人或 AI 的完整交接材料

### `docs/AI_CONTINUITY_GUIDE.md`

用于记录：

- 如何让后续 AI 自动接手
- 如何自动读取、更新、延续上下文

## 更新触发规则

### 触发 1：完成一个里程碑

至少更新：

- `PROJECT_MEMORY.md`
- `DIRECT_1688_PROGRESS.md`
- `PROJECT_EVOLUTION.md`

### 触发 2：修掉一个平台级问题

至少更新：

- `PLATFORM_EXPERIENCE_KB.md`
- 如状态变化明显，再更新 `PROJECT_MEMORY.md`

### 触发 3：新增可复用能力

至少更新：

- `PLATFORM_EXPERIENCE_KB.md`
- `DELIVERY_HANDOVER_2026-03-24.md`

### 触发 4：接手方式或 AI 工作方式改变

至少更新：

- `AGENTS.md`
- `AI_CONTINUITY_GUIDE.md`

### 触发 5：上线边界变化

至少更新：

- `GO_LIVE_CHECKLIST.md`
- `PROJECT_MEMORY.md`
- `PROJECT_EVOLUTION.md`

## 提交前最小检查

每次准备提交前，至少确认：

1. 当前变更是否改变了项目事实
2. 当前变更是否引入了新经验
3. 当前变更是否改变了接手方式
4. 对应文档是否已经更新

## AI 执行要求

如果 AI 完成了以下任一工作，就不应只改代码不改文档：

- 修复 1688 核心流程 bug
- 补齐选择器
- 打通新类目
- 改变默认执行策略
- 新增 smoke 样本
- 新增发布模式

## 推荐提交流程

1. 先改代码
2. 再补文档
3. 跑单测和 `doctor`
4. 单独提交一条文档或里程碑 commit

## 禁止事项

- 不允许让“当前有效文档”和代码长期不一致
- 不允许把平台级经验只留在聊天记录中
- 不允许新增关键能力却没有交接说明
