# 平台中台项目验证运行手册

## 目的

用于快速确认当前项目是不是处于“可继续开发”的健康状态，而不是只停留在文档正确或代码存在的状态。

## 前置条件

- 本地已有 Python 虚拟环境并安装依赖
- `D:\script_files\ERP\furniture-uploader` 存在且可访问
- 目标 SQL Server 数据库可访问
- 已持有可写运行账号

## 验证顺序

### 1. 跑单元测试

```powershell
$env:PYTHONPATH="D:\script_files\ERP\platform-mediation-platform\src"
pytest D:\script_files\ERP\platform-mediation-platform\tests
```

期望结果：

- 所有测试通过
- 当前基线应为：`9 passed`

### 2. 检查 SQL Server 联通性与表状态

```powershell
$env:PYTHONPATH="D:\script_files\ERP\platform-mediation-platform\src"
python D:\script_files\ERP\platform-mediation-platform\scripts\check_sqlserver_access.py --server <server> --database <database> --username <username> --password <password>
```

期望结果：

- `connection=ok`
- `missing_tables=<none>`
- `create_table_permission=False`

说明：

- 运行账号没有建表权限是预期行为
- 如果 `missing_tables` 非空，说明建表脚本没有完整执行

### 3. 跑 SQL Server 仓储烟雾测试

```powershell
$env:PYTHONPATH="D:\script_files\ERP\platform-mediation-platform\src"
python D:\script_files\ERP\platform-mediation-platform\scripts\smoke_test_sqlserver_repository.py --server <server> --database <database> --username <username> --password <password>
```

期望结果：

- `sqlalchemy_smoke=ok`
- `loaded_task=<task_id>`
- `attempt_count=1`
- `artifact_count=1`
- `reflow_count=1`

说明：

- 这个脚本会写入一组测试数据，再自动清理
- 如果清理时报外键相关问题，优先检查 `source_snapshot_id` 解绑步骤

### 4. 跑控制面 validate 桥接烟雾测试

```powershell
$env:PYTHONPATH="D:\script_files\ERP\platform-mediation-platform\src"
python D:\script_files\ERP\platform-mediation-platform\scripts\smoke_test_sqlserver_validate_dispatch.py --server <server> --database <database> --username <username> --password <password>
```

期望结果：

- `validate_dispatch_smoke=ok`
- `task_status=success`
- `attempt_count=1`
- `artifact_count=3`
- `reflow_count=0`
- `verification_status=validate`

说明：

- `reflow_count=0` 是刻意设计结果，因为 `preview / validate` 不应产生回流事件
- 这个脚本也会自动清理测试数据

## 常见异常与处理建议

### 数据库可连但 `missing_tables` 不为空

优先检查：

- 是否执行了 `sql/platform_mediation_ddl_all_tables_with_permissions.sql`
- 是否执行在正确数据库
- 是否执行在 `dbo` 架构

### SQL Server 烟雾测试报时间类型错误

优先检查：

- 当前代码是否仍保留 SQL Server 2012 兼容修复
- 仓储模型是否误改回 `timezone=True`

### validate 桥接失败

优先检查：

- `D:\script_files\ERP\furniture-uploader` 是否仍在约定路径
- payload 文件是否存在
- `furniture-uploader` 自身依赖是否已安装

### 测试数据清理失败

优先检查：

- 是否先把 `task_item.source_snapshot_id` 置空
- 是否有外部脚本同时占用同一批测试数据

## 验证结论判断

如果以上 4 步都通过，可以认为：

- 真源文档口径与代码状态一致
- 数据库基线正常
- 控制面最小闭环正常
- 当前项目适合继续向 `execute`、后台 worker 和回流处理器推进
