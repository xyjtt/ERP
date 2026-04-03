# 平台上架下架任务与数据中台 - API 与适配器契约 v1

## 文档目的

用于明确第一阶段 `1688 中台 v1` 的最小接口契约，避免后端、适配器包装和运营入口并行开发时口径不一致。

## 适用范围

- 第一阶段：`1688 中台 v1`
- 执行方式：以现有 `1688/T-005` 包装为 `1688 adapter v1`
- 目标：定义最小可实现的控制面 API 和适配器输入输出结构

## 控制面最小 API

### 1. 创建任务

- 方法：`POST /api/v1/tasks`
- 用途：创建一个平台任务，并生成 `task / task_item / source_snapshot`

请求体建议：

```json
{
  "task_type": "listing",
  "platform": "1688",
  "shop_id": 10001,
  "executor_type": "rpa",
  "priority": 10,
  "mapping_version": "1688.v1",
  "items": [
    {
      "sku_id": "SKU001"
    },
    {
      "sku_id": "SKU002"
    }
  ],
  "created_by": "system"
}
```

响应体建议：

```json
{
  "task_id": "TASK-20260402-0001",
  "status": "created",
  "item_count": 2
}
```

### 2. 查询任务列表

- 方法：`GET /api/v1/tasks`
- 用途：查看任务列表和汇总状态

查询参数建议：

- `platform`
- `shop_id`
- `status`
- `task_type`
- `page`
- `page_size`

### 3. 查询任务详情

- 方法：`GET /api/v1/tasks/{task_id}`
- 用途：查看单个任务的状态、统计和版本绑定信息

响应体建议至少包含：

- `task_id`
- `task_type`
- `platform`
- `shop_id`
- `status`
- `total_count`
- `success_count`
- `failed_count`
- `mapping_version`
- `executor_type`
- `created_at`

### 4. 查询任务项

- 方法：`GET /api/v1/tasks/{task_id}/items`
- 用途：查看任务项执行状态与错误信息

### 5. 查询任务尝试

- 方法：`GET /api/v1/tasks/{task_id}/attempts`
- 用途：查看执行尝试、重试轨迹和错误分类

### 6. 查询任务工件

- 方法：`GET /api/v1/tasks/{task_id}/artifacts`
- 用途：查看 run report、截图、HTML、日志等工件路径

### 7. 取消任务

- 方法：`POST /api/v1/tasks/{task_id}/cancel`
- 用途：取消还未完成的任务

### 8. 重试失败任务项

- 方法：`POST /api/v1/tasks/{task_id}/retry`
- 用途：按快照和版本重试失败任务项

## 适配器契约

### 适配器输入

适配器不直接接收松散业务参数，建议统一接收标准化的执行请求：

```json
{
  "task_id": "TASK-20260402-0001",
  "task_item_id": 101,
  "platform": "1688",
  "action": "listing",
  "executor_type": "rpa",
  "shop_id": 10001,
  "mapping_version": "1688.v1",
  "source_snapshot_id": "SS-20260402-0001",
  "payload": {
    "sku_id": "SKU001",
    "title": "示例商品",
    "price": 199.0
  }
}
```

### 适配器输出

建议统一为以下结构：

```json
{
  "status": "success",
  "error_type": null,
  "error_message": null,
  "platform_item_id": "1234567890",
  "platform_sku_id": "SKU001-1688",
  "result_payload": {
    "draft_id": "draft-001"
  },
  "artifacts": [
    {
      "artifact_type": "run_report",
      "artifact_path": "logs/run_reports/20260402_120000.summary.json"
    }
  ],
  "raw_result": {}
}
```

失败结构建议：

```json
{
  "status": "failed",
  "error_type": "platform_validation_error",
  "error_message": "buyer protection missing",
  "platform_item_id": null,
  "platform_sku_id": null,
  "result_payload": {},
  "artifacts": [],
  "raw_result": {}
}
```

## 错误分类建议

第一阶段至少统一这些错误类型：

- `source_data_error`
- `mapping_error`
- `validation_error`
- `platform_validation_error`
- `platform_runtime_error`
- `session_lock_conflict`
- `worker_unavailable`
- `timeout`
- `unknown_error`

## 状态更新规则

### task 更新规则

- 所有 `task_item` 成功：`task -> success`
- 部分成功部分失败：`task -> partial_success`
- 全部失败：`task -> failed`
- 主动取消：`task -> cancelled`

### task_item 更新规则

- 创建后：`created`
- 入队后：`queued`
- 开始执行：`running`
- 成功：`success`
- 失败：`failed`
- 被取消：`cancelled`

## 工件约定

第一阶段建议支持：

- `run_report`
- `screenshot`
- `html`
- `log`

工件要能回查到：

- `task_id`
- `task_item_id`
- `task_attempt_id`

## 回流事件建议

第一阶段只要求支持“出箱 + 处理 + 重试”三步，不要求复杂工作流。

事件类型建议：

- `item_listed`
- `item_updated`
- `item_delisted`

## 第一阶段不做的内容

- 不做完整 OpenAPI 对外公开文档
- 不做复杂分页和高级筛选
- 不做多平台统一前端大盘
- 不做在线规则可视化编辑器

## 结论

只要控制面 API、任务状态、适配器输入输出和错误分类按这份契约统一，第一阶段就已经具备并行开发条件：

- 后端可以先建任务中心和查询接口
- 适配器包装可以独立实现
- 简版运营入口可以按固定接口开发
