# 决策记录

## 基本信息

- 决策标题：1688 适配器 validate 桥接基线
- 决策日期：2026-04-08
- 决策人：Codex 整理
- 相关需求：平台上架下架任务与数据中台

## 背景

第一阶段前半段已经完成：

- 控制面骨架
- SQL Server 仓储接入
- 任务、快照、尝试、工件、回流事件的落库能力

下一步的核心不是继续抽象，而是把 `1688 adapter v1` 真正接到现有 `ERP/furniture-uploader` 执行层上。

## 本轮结论

- `1688 adapter v1` 现已支持 4 种模式：
  - `mock`
  - `preview`
  - `validate`
  - `execute`
- 当前最稳的受控桥接基线是：
  - 使用 `variant json` 作为中台到 `furniture-uploader` 的输入桥接格式
  - 使用 `python rpa/main.py --system 1688_direct --platform 1688 --file <temp-json> --input-mode variant`
  - 在 `validate` 模式下追加 `--validate-only`
- 已实测跑通：
  - `Alibaba1688Adapter -> furniture-uploader --validate-only`
  - `控制面任务 -> SQL Server -> validate bridge -> task_attempt / task_artifact 落库 -> 自动清理`

## 已落地能力

- 适配器会自动生成临时 `variant_input.json`
- 适配器会落地桥接日志：
  - `stdout.log`
  - `stderr.log`
- 适配器会在可用时回收：
  - `run_report`
  - `summary`
- 适配器会把桥接输入、命令、stdout/stderr 路径写入 `raw_result`

## 行为约束

- `preview` 与 `validate` 只代表桥接链路成功，不代表真实上架成功
- 因此控制面调度层已显式约束：
  - `preview/validate` 成功时不生成 `reflow_event`
  - `task_item.verification_status` 记录当前桥接模式

## 新增脚本

- 真实 SQL Server 仓储烟雾脚本：
  - `D:\script_files\ERP\platform-mediation-platform\scripts\smoke_test_sqlserver_repository.py`
- 真实控制面 validate 桥接烟雾脚本：
  - `D:\script_files\ERP\platform-mediation-platform\scripts\smoke_test_sqlserver_validate_dispatch.py`

## 当前限制

- `execute` 模式虽然已具备代码路径，但仍需要：
  - `PLATFORM_MEDIATION_ENABLE_REAL_1688_EXECUTION=1`
  - 更完整的 live 输入数据
  - 已登录且可复用的浏览器会话治理
- 当前桥接仍优先面向 `listing`
- `task_item` 与 `source_snapshot` 的双向外键仍需后续评估是否收口

## 后续动作

- 动作 1：继续把 `validate` 推进到受控 `execute` 小批量验证
- 动作 2：把 `execute` 成功结果与 `furniture-uploader` run report 字段做更细粒度映射
- 动作 3：在后台 worker 接入时，把 `preview/validate/execute` 模式切换纳入任务级配置
