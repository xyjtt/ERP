# 决策记录

## 基本信息

- 决策标题：SQL Server 2012 接入基线与 DBA 前置条件
- 决策日期：2026-04-08
- 决策人：Codex 整理
- 相关需求：平台上架下架任务与数据中台

## 背景

平台中台第一阶段已经完成本地 `sqlalchemy` 仓储与 SQLite 验证，下一步需要接入真实 SQL Server 环境。

当前已拿到 `JSDataMiddlePlatform` 的数据库账号，并完成首次真实连通性检查。

## 实测结果

- 数据库类型：SQL Server 2012
- 数据库名：`JSDataMiddlePlatform`
- 当前可用 ODBC 驱动：`SQL Server Native Client 10.0`
- 应用账号可以成功连接数据库
- 目标库中当前还没有平台中台第一阶段所需表
- 当前应用账号不具备 `CREATE TABLE` 权限

## 结论

当前数据库接入已经具备“联通”和“后续读写”的前提，但还不具备直接建表的前提。

因此第一阶段数据库推进方式明确为：

1. 由 DBA 或具备 DDL 权限的账号执行项目建表脚本
2. 在脚本中把运行账号设置为实际应用账号
3. 建表完成后，再使用应用账号继续做仓储层联调

## 执行脚本

- 建表与授权脚本：
  - `D:\script_files\ERP\platform-mediation-platform\sql\platform_mediation_ddl_all_tables_with_permissions.sql`
- 本地检查脚本：
  - `D:\script_files\ERP\platform-mediation-platform\scripts\check_sqlserver_access.py`

## 后续动作

- 动作 1：请 DBA 在目标库执行建表与授权脚本
- 动作 2：把脚本中的运行账号改为实际应用数据库用户
- 动作 3：建表完成后继续跑真实 SQL Server 仓储闭环测试

