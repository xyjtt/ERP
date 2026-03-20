# Solution

Updated: 2026-03-18

## Final Direction

本项目不再走 API 修改线上商品编码。

最终方案：

- 入口系统：聚水潭 Web
- 执行方式：Web RPA 脚本
- 核心动作：批量修改线上商品编码

## Business Goal

把指定平台 / 店铺 / 商品编码命中的记录，批量替换为新的线上商品编码。

第一阶段主要用于：

- 把正常线上商品编码改成停用编码
- 停止后续按旧编码继续维护
- 为人工或后续脚本留出统一的停用规则

## Why Not API

你已与聚水潭官方确认：

- `/open/webapi/itemapi/itemsku/itemskubatchupload` 不支持修改页面中的线上商品编码

因此本项目不再尝试 API 改编码，直接使用页面脚本。

## Data Sources

### 1. Excel

适合运营批量处理。

### 2. API

适合业务系统下发任务。

### 3. Manual Input

适合临时处理单个平台 / 单个店铺 / 单个编码。

## Unified Task Model

无论数据源是什么，统一转成以下任务字段：

- task_id
- source_type
- source_record_id
- platform
- shop_name
- old_online_sku_code
- new_online_sku_code
- operator_name
- remark

## Script Workflow

页面操作方案见：

- [WORKFLOW.md](./WORKFLOW.md)

## Post-Action Verification

每次修改完成后，必须重新查询“历史编码”的数量。

定义如下：

- 历史编码：`old_online_sku_code`
- 回查目标：再次搜索旧编码
- 预期结果：
  - 理想状态：数量为 `0`
  - 若大于 `0`：说明仍有未成功修改记录，任务状态记为 `partial_success` 或 `failed`

## Exception Strategy

至少定义这些异常：

- `PlatformSelectionError`
- `ShopSelectionError`
- `SearchResultEmptyError`
- `SearchResultMismatchError`
- `BatchDialogOpenError`
- `OldCodeInputError`
- `RowEditError`
- `SubmitConfirmError`
- `PostCheckFailedError`
- `PageChangedError`

统一异常动作：

- 截图
- 保存 HTML
- 记录当前步骤
- 记录检索条件
- 记录页面提示文本
- 写入 SQL 日志

## Suggested Execution Result Types

- `success`
- `partial_success`
- `failed`
- `skipped`

## MVP Scope

第一阶段只做：

1. Excel / API / 手动输入 三种数据入口
2. 聚水潭页面批量修改线上商品编码
3. 修改后重新查询旧编码数量
4. SQL Server 日志
5. 截图与 HTML 快照

## Key Risk

这不是“真实下架平台商品”的方案。

这是“修改聚水潭中的线上商品编码”的方案。

业务风险包括：

- 影响后续库存同步
- 影响聚水潭与店铺链接映射
- 影响依赖旧编码的其他脚本或报表

所以必须保留：

- 原编码
- 新编码
- 回滚记录

## Rollback Strategy

回滚方式就是把：

- `new_online_sku_code` 改回 `old_online_sku_code`

因此日志表必须记录新旧编码映射。
