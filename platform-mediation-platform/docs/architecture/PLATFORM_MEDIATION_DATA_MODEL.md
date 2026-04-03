# 平台上架下架任务与数据中台 - 数据模型说明

## 文档目的

用于把第一阶段中台开发需要的核心实体、状态和版本绑定关系明确下来，减少“数据库有表但开发口径不一致”的问题。

## 核心实体

### 1. task

表示一次业务任务，例如“1688 上架任务”。

关键字段建议：

- `task_id`
- `task_type`
- `platform`
- `shop_id`
- `status`
- `priority`
- `scheduled_at`
- `next_retry_at`
- `idempotency_key`
- `executor_type`

### 2. task_item

表示任务中的单个商品处理单元。

关键字段建议：

- `task_id`
- `sku_id`
- `status`
- `payload_json`
- `mapping_version`
- `source_version`
- `attempt_count`

### 3. source_snapshot

表示任务创建时冻结的输入数据。

当前正式口径应包含：

- `snapshot_id`
- `task_id`
- `task_item_id`
- `source_type`
- `source_record_key`
- `snapshot_json`
- `version`

### 4. task_attempt

表示某个任务或任务项的一次执行尝试。

当前正式口径应包含：

- `attempt_id`
- `task_id`
- `task_item_id`
- `attempt_no`
- `adapter_code`
- `adapter_version`
- `mapping_version`
- `source_snapshot_id`
- `source_version`
- `worker_node_id`
- `status`
- `error_type`
- `error_message`
- `retry_reason`
- `duration_ms`

### 5. task_artifact

表示某次执行产生的工件。

当前正式口径应包含：

- `artifact_id`
- `task_attempt_id`
- `task_id`
- `task_item_id`
- `artifact_type`
- `artifact_path`
- `metadata_json`

### 6. reflow_event

表示需要异步回流到外部系统的事件。

当前正式口径应包含：

- `event_id`
- `event_type`
- `task_id`
- `task_item_id`
- `payload_json`
- `status`
- `retry_count`
- `idempotency_key`

## 状态模型

### task 状态

- `created`
- `queued`
- `running`
- `success`
- `partial_success`
- `failed`
- `cancelled`

### task_item 状态

建议统一为：

- `created`
- `queued`
- `running`
- `success`
- `failed`
- `cancelled`

不要再混用“待执行/执行中/成功/失败”与英文状态。

### reflow_event 状态

- `pending`
- `processing`
- `success`
- `failed`

## 版本绑定

每次任务执行应同时绑定以下三个版本：

- `source_snapshot`
- `mapping_version`
- `adapter_version`

这是第一阶段最关键的可追踪性约束之一。

## 锁与租约模型

第一阶段正式采用独立的锁模型，不把锁逻辑散落在任务表中。

建议实体：

- `worker_lock`

关键字段：

- `lock_id`
- `resource_type`
- `resource_key`
- `task_id`
- `task_item_id`
- `worker_node_id`
- `status`
- `lease_until`

关键约束：

- 同一资源在同一时刻只能存在一个 `active` 锁

## 标准商品模型

建议第一阶段至少统一这些字段：

- `sku_id`
- `shop_id`
- `platform`
- `title`
- `subtitle`
- `brand`
- `material`
- `spec_text`
- `attributes`
- `variants`
- `price`
- `quantity`
- `weight_g`
- `length_cm`
- `width_cm`
- `height_cm`
- `main_images`
- `detail_images`
- `description_html`
- `category_code`
- `shipping_profile`
- `operator_name`
- `source_record_id`

## 当前仍需在实现层确认的点

当前 schema 口径已经可以支持第一阶段开发，但实现层仍需继续明确：

- `mapping_version` 的实际生成与发布流程
- `worker_lock` 的租约续期与过期回收策略
- `reflow_event` 的消费器重试与死信处理策略
- `shop_config` 中 `credential_ref` 的具体接入方式

## 结论

第一阶段不要求把所有治理能力一次性做满，但必须先把：

- 状态模型
- 版本绑定
- 快照口径
- 工件归档
- 回流事件

这 5 个基础模型统一下来，否则后续实现会边做边改。
