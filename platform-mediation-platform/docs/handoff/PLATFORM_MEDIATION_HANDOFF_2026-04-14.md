# 平台中台项目交接文档

## 文档目的

把 `platform-mediation-platform` 当前已经完成的工作、真实可运行的链路、剩余风险和后续开发顺序整理成一份接手文档，降低人和 AI 的交接成本。

## 项目定位

- 项目名称：`platform-mediation-platform`
- 当前阶段：第一阶段
- 当前目标：`1688 中台 v1`
- 当前策略：保留现有 `D:\script_files\ERP\furniture-uploader` 执行层，先补控制面

## 当前硬边界

- 不重写现有 `furniture-uploader` Selenium 主链路
- 不在第一阶段内追求全平台覆盖
- 不把复杂运营台作为第一阶段前置条件
- 不绕开 `source_snapshot / mapping_version / adapter_version` 做不可追溯执行

## 当前已完成

### 1. 文档真源已收口

- 项目正式文档已经统一放在：`D:\script_files\ERP\platform-mediation-platform\docs\`
- 草案层继续保留在：`D:\script_files\.trae\specs\platform-mediation-platform\`
- 当前接手应以真源层为准，草案层仅作讨论参考

### 2. 控制面代码骨架已完成

- `FastAPI` 控制面入口已可运行
- 任务中心、调度服务、锁服务、适配器注册和仓储抽象已建立
- 已支持 `memory` 与 `sqlalchemy` 两种仓储后端

关键目录：

- `src/platform_mediation/api/`
- `src/platform_mediation/application/services/`
- `src/platform_mediation/adapters/`
- `src/platform_mediation/repositories/`

### 3. SQL Server 基线已跑通

- 第一阶段所需表结构已经建好
- 中文表注释和字段注释脚本已经补齐
- 真实数据库仓储烟雾测试已经通过
- 已兼容 `SQL Server 2012` 的时间字段限制

关键文件：

- `sql/platform_mediation_001_init_schema.sql`
- `sql/platform_mediation_002_extended_properties.sql`
- `sql/platform_mediation_ddl_all_tables_with_permissions.sql`
- `scripts/check_sqlserver_access.py`
- `scripts/smoke_test_sqlserver_repository.py`

### 4. 1688 validate 真实桥接已跑通

- `Alibaba1688Adapter` 已支持 `mock / preview / validate / execute`
- 当前已真实调用 `D:\script_files\ERP\furniture-uploader\rpa\main.py --validate-only`
- 控制面端到端 validate 闭环已验证成功
- `preview / validate` 不会错误产生 `reflow_event`

关键文件：

- `src/platform_mediation/adapters/alibaba_1688_adapter.py`
- `src/platform_mediation/application/services/dispatch_service.py`
- `scripts/smoke_test_sqlserver_validate_dispatch.py`

### 5. 当前基线测试已通过

- 单元测试：`9 passed`
- SQL Server 连接检查：已通过
- SQL Server 仓储烟雾测试：已通过
- 控制面 validate 桥接烟雾测试：已通过

## 当前未完成

### P0 未完成

- `1688 adapter v1` 的受控 `execute` 真实桥接
- 后台调度 worker
- 回流事件处理器
- `mapping_version / mapping_rule` 的实际装载与执行

### P1 未完成

- 简版运营入口
- 更完整的锁租约治理
- 第二平台适配器验证

## 当前推荐开发顺序

1. 先把 `validate` 推进到受控 `execute` 桥接，只做小批量、可回溯执行。
2. 再把调度逻辑从同步调用推进到后台 worker。
3. 然后补 `reflow_event` 的后台处理、重试和状态推进。
4. 再实现 `mapping_version / mapping_rule` 的装载与转换执行。
5. 最后补简版运营入口。

## 当前关键验证入口

### 单元测试

```powershell
$env:PYTHONPATH="D:\script_files\ERP\platform-mediation-platform\src"
pytest D:\script_files\ERP\platform-mediation-platform\tests
```

### SQL Server 连接与权限检查

```powershell
$env:PYTHONPATH="D:\script_files\ERP\platform-mediation-platform\src"
python D:\script_files\ERP\platform-mediation-platform\scripts\check_sqlserver_access.py --server <server> --database <database> --username <username> --password <password>
```

### SQL Server 仓储烟雾测试

```powershell
$env:PYTHONPATH="D:\script_files\ERP\platform-mediation-platform\src"
python D:\script_files\ERP\platform-mediation-platform\scripts\smoke_test_sqlserver_repository.py --server <server> --database <database> --username <username> --password <password>
```

### 控制面 validate 桥接烟雾测试

```powershell
$env:PYTHONPATH="D:\script_files\ERP\platform-mediation-platform\src"
python D:\script_files\ERP\platform-mediation-platform\scripts\smoke_test_sqlserver_validate_dispatch.py --server <server> --database <database> --username <username> --password <password>
```

## 当前已知风险

- `task_item` 与 `source_snapshot` 之间存在双向关联，清理测试数据时要先断开 `source_snapshot_id`
- `execute` 模式一旦放开真实执行，会涉及店铺登录态、草稿箱容量和会话锁治理
- 当前后端闭环已可验证，但还没有独立后台 worker，长任务仍不适合直接依赖同步 API 路径

## 接手建议

- 如果接手目标是继续开发，先读 `docs/handoff/PLATFORM_MEDIATION_QUICKSTART_FOR_NEXT_DEV.md`
- 如果接手目标是确认现状是否可跑，先读 `docs/handoff/PLATFORM_MEDIATION_VERIFICATION_RUNBOOK.md`
- 如果接手目标是继续扩展能力，先从 `WP-01 / WP-02 / WP-06 / WP-07` 当前实现与缺口开始

## 交接结论

这个项目已经不是“方案阶段”，而是“可持续开发的后端控制面阶段”。当前最重要的不是重新设计，而是沿着既有真源文档，把真实执行链路、后台 worker 和回流处理器继续往前推。
