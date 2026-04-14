# 平台中台项目快速接手指南

## 适用场景

适用于新的开发同学、AI 协作者，或者隔了一段时间后重新回到项目的人。目标是在最短时间内重新建立上下文，并避免从错误入口开始。

## 15 分钟接手路径

### 第一步：先读这 4 份

1. `docs/handoff/PLATFORM_MEDIATION_HANDOFF_2026-04-14.md`
2. `docs/requirements/PLATFORM_MEDIATION_REQUIREMENT_INTAKE.md`
3. `docs/plans/PLATFORM_MEDIATION_TECH_PLAN.md`
4. `docs/plans/PLATFORM_MEDIATION_PHASE1_WORKPACKAGES.md`

### 第二步：确认当前工程边界

要先记住这 4 条：

- 当前只做第一阶段：`1688 中台 v1`
- 当前只是在现有 `furniture-uploader` 外包控制面，不重写执行器
- 当前真实跑通的是 `validate`，不是完整 `execute`
- 当前真源在项目内 `docs/`，不是 `.trae` 草案目录

### 第三步：确认关键代码入口

- 控制面应用入口：`src/platform_mediation/main.py`
- 任务中心：`src/platform_mediation/application/services/task_service.py`
- 调度服务：`src/platform_mediation/application/services/dispatch_service.py`
- 1688 适配器：`src/platform_mediation/adapters/alibaba_1688_adapter.py`
- SQLAlchemy 仓储：`src/platform_mediation/repositories/sqlalchemy_repo.py`

### 第四步：跑一次基线验证

按 `docs/handoff/PLATFORM_MEDIATION_VERIFICATION_RUNBOOK.md` 的顺序做：

1. 跑单元测试
2. 跑数据库连接检查
3. 跑 SQL Server 仓储烟雾测试
4. 跑控制面 validate 桥接烟雾测试

## 当前最值得继续推进的代码点

### 方向 1：受控 execute 桥接

优先原因：

- 它是 validate 之后最自然的下一步
- 可以直接验证适配器包装对真实执行链路的承接能力
- 做完后能更清楚暴露锁、回流、草稿箱等真实问题

建议改动重点：

- `src/platform_mediation/adapters/alibaba_1688_adapter.py`
- `src/platform_mediation/application/services/dispatch_service.py`
- `src/platform_mediation/domain/models.py`

### 方向 2：后台 worker 与回流处理器

优先原因：

- 同步 API 路径不适合长执行任务
- 当前缺少真正的后台消费和重试治理

建议改动重点：

- `src/platform_mediation/application/services/dispatch_service.py`
- 新增后台 worker 入口脚本
- 新增回流事件消费器

### 方向 3：映射版本装载与执行

优先原因：

- 当前 `mapping_version` 主要停留在记录层
- 后续如果不补映射装载，就很难进入真正的平台输入构造

建议改动重点：

- `src/platform_mediation/repositories/`
- `src/platform_mediation/application/services/`
- 新增映射装载与转换模块

## 当前不建议优先做的事

- 不要先做完整前端运营台
- 不要先接第二个平台
- 不要先重写 `furniture-uploader`
- 不要跳过版本绑定和快照，直接做“实时读源数据执行”

## 交接时最容易踩的坑

- 把 `.trae` 草案层误当成真源
- 以为数据库还没打通，其实真实 SQL Server 基线已经完成
- 以为当前已经能大规模 `execute`，实际上现在真实稳定基线是 `validate`
- 在清理测试数据时忘记先断开 `task_item.source_snapshot_id`

## 一句话接手建议

先按运行手册确认当前基线是真的可跑，再从 `execute` 桥接和后台 worker 两条线继续往前推，不要回头重做方案层。
