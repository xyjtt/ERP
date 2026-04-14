# Platform Mediation Platform

`platform-mediation-platform` 是“平台上架下架任务与数据中台”的第一阶段代码骨架。

当前目标只覆盖 `1688 中台 v1`：

- 保留现有 `D:\script_files\ERP\furniture-uploader` 执行层
- 新建控制面骨架，用于承接任务中心、状态机、适配器注册、快照、重试和回流
- 第一阶段默认使用内存仓储，便于先把 API、服务边界和适配器契约跑通

## 当前范围

- `FastAPI` 控制面入口
- 任务中心骨架
- `1688 adapter v1` 包装接口
- 内存仓储与调度服务
- 第一阶段 API 契约实现
- SQL Server 建库草案同步副本

## 文档真源

项目正式文档位于 `docs/`：

- `docs/requirements/`
- `docs/plans/`
- `docs/architecture/`
- `docs/decisions/`
- `docs/roadmap/`
- `docs/checklists/`
- `docs/handoff/`

如果是首次接手，优先从下面 3 份开始：

- `docs/handoff/PLATFORM_MEDIATION_HANDOFF_2026-04-14.md`
- `docs/handoff/PLATFORM_MEDIATION_QUICKSTART_FOR_NEXT_DEV.md`
- `docs/handoff/PLATFORM_MEDIATION_VERIFICATION_RUNBOOK.md`

## 目录结构

```text
platform-mediation-platform/
  src/platform_mediation/
    api/                    FastAPI 路由
    application/services/   任务中心、调度、锁服务
    adapters/               平台适配器接口与 1688 包装
    domain/                 核心实体与状态枚举
    repositories/           仓储抽象与内存实现
    schemas/                API 请求/响应模型
    utils/                  公共工具
  scripts/                  本地运行脚本
  sql/                      建库 SQL
  tests/                    第一阶段骨架测试
```

## 快速开始

1. 创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. 启动本地服务：

```powershell
.\scripts\run_dev.ps1
```

3. 打开接口文档：

- `http://127.0.0.1:8000/docs`

如果要切到 `sqlalchemy` 仓储做本地持久化，可先执行：

```powershell
$env:PYTHONPATH="D:\script_files\ERP\platform-mediation-platform\src"
python .\scripts\init_sqlalchemy_db.py
```

如果要检查真实 SQL Server 联通性、现有表和建表权限，可执行：

```powershell
$env:PYTHONPATH="D:\script_files\ERP\platform-mediation-platform\src"
python .\scripts\check_sqlserver_access.py --server 218.93.191.16 --database JSDataMiddlePlatform --username <db_user> --password <db_password>
```

如果要验证真实 SQL Server 仓储的写入、读取和清理闭环，可执行：

```powershell
$env:PYTHONPATH="D:\script_files\ERP\platform-mediation-platform\src"
python .\scripts\smoke_test_sqlserver_repository.py --server 218.93.191.16 --database JSDataMiddlePlatform --username <db_user> --password <db_password>
```

如果要验证“控制面任务 -> SQL Server -> 1688 validate 桥接 -> 工件落库 -> 自动清理”的端到端闭环，可执行：

```powershell
$env:PYTHONPATH="D:\script_files\ERP\platform-mediation-platform\src"
python .\scripts\smoke_test_sqlserver_validate_dispatch.py --server 218.93.191.16 --database JSDataMiddlePlatform --username <db_user> --password <db_password>
```

## 环境变量

参考 `.env.example`：

- `PLATFORM_MEDIATION_ENV`
- `PLATFORM_MEDIATION_ADAPTER_MODE`
- `PLATFORM_MEDIATION_REPOSITORY`
- `PLATFORM_MEDIATION_DATABASE_URL`
- `PLATFORM_MEDIATION_AUTO_CREATE_SCHEMA`
- `PLATFORM_MEDIATION_ENABLE_REAL_1688_EXECUTION`
- `PLATFORM_MEDIATION_FURNITURE_UPLOADER_ROOT`

`PLATFORM_MEDIATION_ADAPTER_MODE` 当前支持：

- `mock`：返回模拟结果，用于服务层和 API 骨架验证
- `preview`：生成真实 variant 模板并预览将执行的 `furniture-uploader` 命令
- `validate`：真实调用 `furniture-uploader --validate-only`
- `execute`：真实调用 `furniture-uploader` 执行链路，需要同时打开 `PLATFORM_MEDIATION_ENABLE_REAL_1688_EXECUTION=1`

默认情况下，`1688 adapter v1` 运行在 `mock` 模式，只返回标准化结果，不直接调用真实执行器。
默认仓储是 `memory`，也可以切到 `sqlalchemy`。

## 当前说明

- `sql/platform_mediation_001_init_schema.sql` 是平台中台项目专用的第一阶段建库副本
- `sql/platform_mediation_002_extended_properties.sql` 是平台中台项目专用的中文注释脚本，可给已建好的表补充 `MS_Description` 表注释和字段注释
- `sql/platform_mediation_ddl_all_tables_with_permissions.sql` 是平台中台项目专用的“建表 + 运行账号授权”脚本，当前已预填数据库 `JSDataMiddlePlatform` 和运行账号 `shaoyou`，并内置中文注释写入
- 已验证真实环境可通过 `SQL Server Native Client 10.0` 连接 SQL Server 2012
- 已跑通真实 SQL Server 仓储烟雾测试，覆盖 `task -> task_item -> source_snapshot -> task_attempt -> task_artifact -> reflow_event`
- 已跑通 `1688 adapter v1 -> furniture-uploader --validate-only` 真实桥接
- 已跑通“控制面任务 -> SQL Server -> validate 桥接 -> 工件落库 -> 自动清理”的端到端烟雾测试
- 第一阶段真实数据库仓储、调度器后台 worker 和回流同步器后续继续接入
- 真实 `1688` 执行链路后续通过 `Alibaba1688Adapter` 与 `furniture-uploader` 对接
