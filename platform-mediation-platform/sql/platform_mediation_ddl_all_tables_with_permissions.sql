/*
平台上架下架任务与数据中台 - 建表与运行权限脚本

用途：
1. 使用具备建表权限的 DBA / 管理账号执行本脚本
2. 本脚本会创建第一阶段所需数据表、约束、索引
3. 本脚本会给运行账号授予表级权限
4. 不会给运行账号授予 CREATE TABLE / ALTER / CREATE INDEX 等建库权限

执行前请先修改：
- [YourDatabaseName] -> 实际数据库名
- N'platform_mediation_app' -> 实际运行账号数据库用户

推荐方式：
- DBA 先执行本脚本完成建表与授权
- 应用运行账号只负责后续读写，不负责建表
*/

USE [YourDatabaseName];
GO

-- 全平台上架下架任务与数据中台 - 数据库表结构

-- 1. 平台适配器表
CREATE TABLE [dbo].[platform_adapter] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [adapter_code] NVARCHAR(100) NOT NULL, -- 稳定业务键，如 1688_adapter
    [adapter_name] NVARCHAR(100) NOT NULL, -- 适配器名称
    [platform] NVARCHAR(50) NOT NULL, -- 平台类型：1688, taobao, douyin等
    [executor_type] NVARCHAR(50) NOT NULL, -- 执行器类型：api, saas, rpa
    [version] NVARCHAR(50) NOT NULL, -- 适配器版本
    [capability_json] NVARCHAR(MAX) NOT NULL, -- 能力矩阵（JSON格式）
    [config_json] NVARCHAR(MAX) NULL, -- 适配器配置（JSON格式）
    [status] INT DEFAULT 1, -- 状态：1-启用，0-禁用
    [created_at] DATETIME DEFAULT GETDATE(), -- 创建时间
    [updated_at] DATETIME DEFAULT GETDATE() -- 更新时间
);

-- 2. 平台类目映射表
CREATE TABLE [dbo].[platform_category_mapping] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [company_category_id] NVARCHAR(100) NOT NULL, -- 公司内部类目ID
    [company_category_name] NVARCHAR(255) NOT NULL, -- 公司内部类目名称
    [platform] NVARCHAR(50) NOT NULL, -- 平台类型：1688, taobao, douyin等
    [platform_category_id] NVARCHAR(100) NOT NULL, -- 平台类目ID
    [platform_category_name] NVARCHAR(255) NOT NULL, -- 平台类目名称
    [status] INT DEFAULT 1, -- 状态：1-启用，0-禁用
    [created_at] DATETIME DEFAULT GETDATE(), -- 创建时间
    [updated_at] DATETIME DEFAULT GETDATE() -- 更新时间
);

-- 3. 映射版本表
CREATE TABLE [dbo].[mapping_version] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [mapping_code] NVARCHAR(100) NOT NULL, -- 稳定业务键，如 1688_listing
    [platform] NVARCHAR(50) NOT NULL, -- 平台类型
    [version] NVARCHAR(50) NOT NULL, -- 版本号
    [standard_model_version] NVARCHAR(50) NULL, -- 标准商品模型版本
    [source_mode] NVARCHAR(50) NOT NULL DEFAULT 'git_managed', -- git_managed / db_managed
    [mapping_json] NVARCHAR(MAX) NOT NULL, -- 映射配置快照（JSON格式）
    [status] INT DEFAULT 1, -- 状态：1-启用，0-禁用
    [created_by] NVARCHAR(100) NOT NULL, -- 创建人
    [created_at] DATETIME DEFAULT GETDATE(), -- 创建时间
    [updated_at] DATETIME DEFAULT GETDATE() -- 更新时间
);

-- 4. 映射规则表
CREATE TABLE [dbo].[mapping_rule] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [mapping_version_id] INT NOT NULL, -- 关联 mapping_version.id
    [rule_name] NVARCHAR(100) NOT NULL, -- 规则名称
    [rule_type] NVARCHAR(50) NOT NULL, -- direct / parse / convert / default / generate
    [sort_order] INT DEFAULT 0, -- 执行顺序
    [rule_json] NVARCHAR(MAX) NOT NULL, -- 规则配置（JSON格式）
    [status] INT DEFAULT 1, -- 状态：1-启用，0-禁用
    [created_at] DATETIME DEFAULT GETDATE(), -- 创建时间
    [updated_at] DATETIME DEFAULT GETDATE(), -- 更新时间
    FOREIGN KEY ([mapping_version_id]) REFERENCES [dbo].[mapping_version]([id])
);

-- 5. 工作节点表
CREATE TABLE [dbo].[worker_node] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [node_id] NVARCHAR(100) NOT NULL, -- 节点ID
    [node_name] NVARCHAR(100) NOT NULL, -- 节点名称
    [executor_type] NVARCHAR(50) NOT NULL, -- 执行器类型
    [platform] NVARCHAR(50) NULL, -- 默认承载平台
    [machine_name] NVARCHAR(100) NULL, -- 所在机器
    [session_key] NVARCHAR(255) NULL, -- 登录会话标识
    [status] NVARCHAR(50) NOT NULL, -- 节点状态：online, offline, busy, idle
    [capability_json] NVARCHAR(MAX) NULL, -- 节点能力（JSON格式）
    [last_heartbeat] DATETIME NULL, -- 最后心跳时间
    [created_at] DATETIME DEFAULT GETDATE(), -- 创建时间
    [updated_at] DATETIME DEFAULT GETDATE() -- 更新时间
);

-- 6. 任务表
CREATE TABLE [dbo].[task] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [task_id] NVARCHAR(100) NOT NULL, -- 任务ID
    [task_type] NVARCHAR(50) NOT NULL, -- 任务类型：listing, delisting
    [platform] NVARCHAR(50) NOT NULL, -- 平台类型
    [shop_id] INT NOT NULL, -- 店铺ID
    [status] NVARCHAR(50) NOT NULL, -- 任务状态：created, queued, running, success, failed, partial_success, cancelled
    [total_count] INT DEFAULT 0, -- 总商品数
    [success_count] INT DEFAULT 0, -- 成功数
    [failed_count] INT DEFAULT 0, -- 失败数
    [scheduled_at] DATETIME NULL, -- 调度时间
    [priority] INT DEFAULT 0, -- 优先级
    [next_retry_at] DATETIME NULL, -- 下次重试时间
    [idempotency_key] NVARCHAR(100) NULL, -- 幂等键
    [executor_type] NVARCHAR(50) NULL, -- 执行器类型
    [adapter_code] NVARCHAR(100) NULL, -- 适配器业务键
    [mapping_version] NVARCHAR(50) NULL, -- 映射版本
    [payload_json] NVARCHAR(MAX) NULL, -- 任务级输入快照
    [created_by] NVARCHAR(100) NOT NULL, -- 创建人
    [created_at] DATETIME DEFAULT GETDATE(), -- 创建时间
    [started_at] DATETIME NULL, -- 开始执行时间
    [completed_at] DATETIME NULL, -- 完成时间
    [remark] NVARCHAR(500) NULL -- 备注
);

-- 7. 任务商品表
CREATE TABLE [dbo].[task_item] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [task_id] INT NOT NULL, -- 关联 task.id
    [sku_id] NVARCHAR(100) NOT NULL, -- 商品SKU ID
    [platform_sku_id] NVARCHAR(100) NULL, -- 平台SKU ID
    [platform_item_id] NVARCHAR(100) NULL, -- 平台商品ID
    [status] NVARCHAR(50) NOT NULL, -- 状态：created, queued, running, success, failed, cancelled
    [source_snapshot_id] INT NULL, -- 关联 source_snapshot.id
    [payload_json] NVARCHAR(MAX) NULL, -- 执行 payload（JSON格式）
    [mapping_version] NVARCHAR(50) NULL, -- 映射版本
    [source_version] NVARCHAR(50) NULL, -- 源数据版本
    [attempt_count] INT DEFAULT 0, -- 尝试次数
    [result_json] NVARCHAR(MAX) NULL, -- 标准化执行结果
    [verification_status] NVARCHAR(50) NULL, -- 校验状态
    [verification_message] NVARCHAR(500) NULL, -- 校验信息
    [error_message] NVARCHAR(500) NULL, -- 错误信息
    [started_at] DATETIME NULL, -- 开始执行时间
    [completed_at] DATETIME NULL, -- 完成时间
    FOREIGN KEY ([task_id]) REFERENCES [dbo].[task]([id])
);

-- 8. 源数据快照表
CREATE TABLE [dbo].[source_snapshot] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [snapshot_id] NVARCHAR(100) NOT NULL, -- 快照ID
    [task_id] INT NULL, -- 关联 task.id
    [task_item_id] INT NULL, -- 关联 task_item.id
    [sku_id] NVARCHAR(100) NOT NULL, -- 商品SKU ID
    [source_type] NVARCHAR(50) NOT NULL, -- jst_sku / api / excel 等
    [source_record_key] NVARCHAR(255) NOT NULL, -- 源记录业务键
    [snapshot_json] NVARCHAR(MAX) NOT NULL, -- 快照数据（JSON格式）
    [version] NVARCHAR(50) NOT NULL, -- 快照版本
    [created_at] DATETIME DEFAULT GETDATE(), -- 创建时间
    FOREIGN KEY ([task_id]) REFERENCES [dbo].[task]([id]),
    FOREIGN KEY ([task_item_id]) REFERENCES [dbo].[task_item]([id])
);

-- 9. 任务执行尝试表
CREATE TABLE [dbo].[task_attempt] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [attempt_id] NVARCHAR(100) NOT NULL, -- 尝试ID
    [task_id] INT NOT NULL, -- 关联 task.id
    [task_item_id] INT NULL, -- 关联 task_item.id
    [attempt_no] INT NOT NULL, -- 第几次尝试
    [adapter_code] NVARCHAR(100) NOT NULL, -- 适配器业务键
    [adapter_version] NVARCHAR(50) NOT NULL, -- 适配器版本
    [mapping_version] NVARCHAR(50) NOT NULL, -- 映射版本
    [source_snapshot_id] INT NULL, -- 关联 source_snapshot.id
    [source_version] NVARCHAR(50) NOT NULL, -- 源数据版本
    [worker_node_id] INT NULL, -- 关联 worker_node.id
    [status] NVARCHAR(50) NOT NULL, -- 尝试状态：queued, running, success, failed, cancelled
    [error_type] NVARCHAR(50) NULL, -- 错误类型
    [error_message] NVARCHAR(MAX) NULL, -- 错误信息
    [retry_reason] NVARCHAR(255) NULL, -- 重试原因
    [raw_result_json] NVARCHAR(MAX) NULL, -- 原始执行结果
    [started_at] DATETIME DEFAULT GETDATE(), -- 开始时间
    [completed_at] DATETIME NULL, -- 完成时间
    [duration_ms] INT NULL, -- 耗时
    FOREIGN KEY ([task_id]) REFERENCES [dbo].[task]([id]),
    FOREIGN KEY ([task_item_id]) REFERENCES [dbo].[task_item]([id]),
    FOREIGN KEY ([source_snapshot_id]) REFERENCES [dbo].[source_snapshot]([id]),
    FOREIGN KEY ([worker_node_id]) REFERENCES [dbo].[worker_node]([id])
);

-- 10. 任务工件表
CREATE TABLE [dbo].[task_artifact] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [artifact_id] NVARCHAR(100) NOT NULL, -- 工件ID
    [task_attempt_id] INT NOT NULL, -- 关联 task_attempt.id
    [task_id] INT NOT NULL, -- 关联 task.id
    [task_item_id] INT NULL, -- 关联 task_item.id
    [artifact_type] NVARCHAR(50) NOT NULL, -- 工件类型：run_report, screenshot, html, log
    [artifact_path] NVARCHAR(500) NOT NULL, -- 工件路径
    [metadata_json] NVARCHAR(MAX) NULL, -- 元数据（JSON格式）
    [created_at] DATETIME DEFAULT GETDATE(), -- 创建时间
    FOREIGN KEY ([task_attempt_id]) REFERENCES [dbo].[task_attempt]([id]),
    FOREIGN KEY ([task_id]) REFERENCES [dbo].[task]([id]),
    FOREIGN KEY ([task_item_id]) REFERENCES [dbo].[task_item]([id])
);

-- 11. 工作锁表
CREATE TABLE [dbo].[worker_lock] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [lock_id] NVARCHAR(100) NOT NULL, -- 锁ID
    [resource_type] NVARCHAR(50) NOT NULL, -- shop / account / session
    [resource_key] NVARCHAR(255) NOT NULL, -- 资源键
    [task_id] INT NULL, -- 关联 task.id
    [task_item_id] INT NULL, -- 关联 task_item.id
    [worker_node_id] INT NULL, -- 关联 worker_node.id
    [status] NVARCHAR(50) NOT NULL, -- active / released / expired
    [lease_until] DATETIME NOT NULL, -- 租约到期时间
    [created_at] DATETIME DEFAULT GETDATE(), -- 创建时间
    [updated_at] DATETIME DEFAULT GETDATE(), -- 更新时间
    FOREIGN KEY ([task_id]) REFERENCES [dbo].[task]([id]),
    FOREIGN KEY ([task_item_id]) REFERENCES [dbo].[task_item]([id]),
    FOREIGN KEY ([worker_node_id]) REFERENCES [dbo].[worker_node]([id])
);

-- 12. 操作日志表
CREATE TABLE [dbo].[operation_log] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [operation_type] NVARCHAR(50) NOT NULL, -- 操作类型：任务创建，任务执行，任务失败等
    [operator] NVARCHAR(100) NOT NULL, -- 操作人
    [platform] NVARCHAR(50) NULL, -- 平台类型
    [shop_id] INT NULL, -- 店铺ID
    [sku_id] NVARCHAR(100) NULL, -- 商品SKU ID
    [content] NVARCHAR(1000) NOT NULL, -- 操作内容
    [result] NVARCHAR(50) NOT NULL, -- 操作结果：成功，失败
    [error_message] NVARCHAR(500) NULL, -- 错误信息
    [created_at] DATETIME DEFAULT GETDATE() -- 操作时间
);

-- 13. 知识库表
CREATE TABLE [dbo].[knowledge_base] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [title] NVARCHAR(255) NOT NULL, -- 标题
    [content] NVARCHAR(MAX) NOT NULL, -- 内容
    [platform] NVARCHAR(50) NULL, -- 平台类型
    [category] NVARCHAR(100) NOT NULL, -- 分类：问题，解决方案，经验等
    [status] INT DEFAULT 1, -- 状态：1-启用，0-禁用
    [created_by] NVARCHAR(100) NOT NULL, -- 创建人
    [created_at] DATETIME DEFAULT GETDATE(), -- 创建时间
    [updated_at] DATETIME DEFAULT GETDATE() -- 更新时间
);

-- 14. 系统配置表
CREATE TABLE [dbo].[system_config] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [config_key] NVARCHAR(100) NOT NULL, -- 配置键
    [config_value] NVARCHAR(MAX) NOT NULL, -- 配置值
    [description] NVARCHAR(255) NULL, -- 描述
    [status] INT DEFAULT 1, -- 状态：1-启用，0-禁用
    [updated_by] NVARCHAR(100) NOT NULL, -- 更新人
    [updated_at] DATETIME DEFAULT GETDATE() -- 更新时间
);

-- 15. 数据同步日志表
CREATE TABLE [dbo].[sync_log] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [sync_type] NVARCHAR(50) NOT NULL, -- 同步类型：platform_data, shop_data等
    [platform] NVARCHAR(50) NULL, -- 平台类型
    [shop_id] INT NULL, -- 店铺ID
    [status] NVARCHAR(50) NOT NULL, -- 同步状态：成功，失败
    [sync_count] INT DEFAULT 0, -- 同步数量
    [error_count] INT DEFAULT 0, -- 错误数量
    [error_message] NVARCHAR(500) NULL, -- 错误信息
    [started_at] DATETIME DEFAULT GETDATE(), -- 开始时间
    [completed_at] DATETIME NULL -- 完成时间
);

-- 16. 数据回流事件表（outbox）
CREATE TABLE [dbo].[reflow_event] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [event_id] NVARCHAR(100) NOT NULL, -- 事件ID
    [event_type] NVARCHAR(50) NOT NULL, -- 事件类型：item_listed, item_updated, item_delisted
    [platform] NVARCHAR(50) NOT NULL, -- 平台类型
    [shop_id] INT NOT NULL, -- 店铺ID
    [task_id] INT NULL, -- 关联 task.id
    [task_item_id] INT NULL, -- 关联 task_item.id
    [sku_id] NVARCHAR(100) NOT NULL, -- 商品SKU ID
    [platform_item_id] NVARCHAR(100) NULL, -- 平台商品ID
    [platform_sku_id] NVARCHAR(100) NULL, -- 平台SKU ID
    [idempotency_key] NVARCHAR(150) NULL, -- 幂等键
    [payload_json] NVARCHAR(MAX) NOT NULL, -- 事件数据（JSON格式）
    [status] NVARCHAR(50) NOT NULL, -- 事件状态：pending, processing, success, failed
    [retry_count] INT DEFAULT 0, -- 重试次数
    [next_retry_at] DATETIME NULL, -- 下次重试时间
    [error_message] NVARCHAR(500) NULL, -- 错误信息
    [created_at] DATETIME DEFAULT GETDATE(), -- 创建时间
    [updated_at] DATETIME DEFAULT GETDATE(), -- 更新时间
    FOREIGN KEY ([task_id]) REFERENCES [dbo].[task]([id]),
    FOREIGN KEY ([task_item_id]) REFERENCES [dbo].[task_item]([id])
);

-- 17. 人员表（与钉钉集成）
CREATE TABLE [dbo].[user] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [user_id] NVARCHAR(100) NOT NULL, -- 钉钉用户ID
    [name] NVARCHAR(100) NOT NULL, -- 姓名
    [email] NVARCHAR(100) NULL, -- 邮箱
    [phone] NVARCHAR(20) NULL, -- 电话
    [department] NVARCHAR(100) NULL, -- 部门
    [status] INT DEFAULT 1, -- 状态：1-启用，0-禁用
    [created_at] DATETIME DEFAULT GETDATE(), -- 创建时间
    [updated_at] DATETIME DEFAULT GETDATE() -- 更新时间
);

-- 18. 店铺配置表（扩展）
CREATE TABLE [dbo].[shop_config] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [shop_id] INT NOT NULL, -- 店铺ID
    [platform] NVARCHAR(50) NOT NULL, -- 平台类型
    [shop_name] NVARCHAR(255) NOT NULL, -- 店铺名称
    [credential_ref] NVARCHAR(255) NULL, -- 外部凭证引用
    [app_key_encrypted] NVARCHAR(500) NULL, -- 加密的应用密钥
    [app_secret_encrypted] NVARCHAR(500) NULL, -- 加密的应用密钥
    [access_token_encrypted] NVARCHAR(500) NULL, -- 加密的访问令牌
    [runtime_config_json] NVARCHAR(MAX) NULL, -- 运行时配置
    [token_expire_time] DATETIME NULL, -- 令牌过期时间
    [status] INT DEFAULT 1, -- 状态：1-启用，0-禁用
    [created_at] DATETIME DEFAULT GETDATE(), -- 创建时间
    [updated_at] DATETIME DEFAULT GETDATE() -- 更新时间
);

-- 添加唯一约束
ALTER TABLE [dbo].[platform_adapter] ADD CONSTRAINT [UQ_platform_adapter_code_version] UNIQUE ([adapter_code], [version]);
ALTER TABLE [dbo].[platform_category_mapping] ADD CONSTRAINT [UQ_platform_category_mapping_internal_platform] UNIQUE ([company_category_id], [platform], [platform_category_id]);
ALTER TABLE [dbo].[mapping_version] ADD CONSTRAINT [UQ_mapping_version_code_version] UNIQUE ([mapping_code], [version]);
ALTER TABLE [dbo].[task] ADD CONSTRAINT [UQ_task_task_id] UNIQUE ([task_id]);
ALTER TABLE [dbo].[source_snapshot] ADD CONSTRAINT [UQ_source_snapshot_snapshot_id] UNIQUE ([snapshot_id]);
ALTER TABLE [dbo].[task_attempt] ADD CONSTRAINT [UQ_task_attempt_attempt_id] UNIQUE ([attempt_id]);
ALTER TABLE [dbo].[task_artifact] ADD CONSTRAINT [UQ_task_artifact_artifact_id] UNIQUE ([artifact_id]);
ALTER TABLE [dbo].[worker_node] ADD CONSTRAINT [UQ_worker_node_node_id] UNIQUE ([node_id]);
ALTER TABLE [dbo].[worker_lock] ADD CONSTRAINT [UQ_worker_lock_lock_id] UNIQUE ([lock_id]);
ALTER TABLE [dbo].[reflow_event] ADD CONSTRAINT [UQ_reflow_event_event_id] UNIQUE ([event_id]);
ALTER TABLE [dbo].[shop_config] ADD CONSTRAINT [UQ_shop_config_shop_id_platform] UNIQUE ([shop_id], [platform]);
ALTER TABLE [dbo].[user] ADD CONSTRAINT [UQ_user_user_id] UNIQUE ([user_id]);
ALTER TABLE [dbo].[task_item]
ADD CONSTRAINT [FK_task_item_source_snapshot]
FOREIGN KEY ([source_snapshot_id]) REFERENCES [dbo].[source_snapshot]([id]);

-- 创建索引
CREATE INDEX [IX_platform_adapter_platform] ON [dbo].[platform_adapter]([platform]);
CREATE INDEX [IX_platform_adapter_executor_type] ON [dbo].[platform_adapter]([executor_type]);
CREATE INDEX [IX_platform_category_mapping_company_category_id] ON [dbo].[platform_category_mapping]([company_category_id]);
CREATE INDEX [IX_platform_category_mapping_platform] ON [dbo].[platform_category_mapping]([platform]);
CREATE INDEX [IX_mapping_version_platform] ON [dbo].[mapping_version]([platform]);
CREATE INDEX [IX_mapping_version_status] ON [dbo].[mapping_version]([status]);
CREATE INDEX [IX_mapping_rule_mapping_version_id] ON [dbo].[mapping_rule]([mapping_version_id]);
CREATE INDEX [IX_task_task_id] ON [dbo].[task]([task_id]);
CREATE INDEX [IX_task_status] ON [dbo].[task]([status]);
CREATE INDEX [IX_task_scheduled_at] ON [dbo].[task]([scheduled_at]);
CREATE INDEX [IX_task_next_retry_at] ON [dbo].[task]([next_retry_at]);
CREATE INDEX [IX_task_item_task_id] ON [dbo].[task_item]([task_id]);
CREATE INDEX [IX_task_item_sku_id] ON [dbo].[task_item]([sku_id]);
CREATE INDEX [IX_task_item_status] ON [dbo].[task_item]([status]);
CREATE INDEX [IX_task_item_source_snapshot_id] ON [dbo].[task_item]([source_snapshot_id]);
CREATE INDEX [IX_source_snapshot_snapshot_id] ON [dbo].[source_snapshot]([snapshot_id]);
CREATE INDEX [IX_source_snapshot_task_item_id] ON [dbo].[source_snapshot]([task_item_id]);
CREATE INDEX [IX_source_snapshot_sku_id] ON [dbo].[source_snapshot]([sku_id]);
CREATE INDEX [IX_source_snapshot_source_record_key] ON [dbo].[source_snapshot]([source_record_key]);
CREATE INDEX [IX_task_attempt_task_id] ON [dbo].[task_attempt]([task_id]);
CREATE INDEX [IX_task_attempt_task_item_id] ON [dbo].[task_attempt]([task_item_id]);
CREATE INDEX [IX_task_attempt_worker_node_id] ON [dbo].[task_attempt]([worker_node_id]);
CREATE INDEX [IX_task_attempt_status] ON [dbo].[task_attempt]([status]);
CREATE INDEX [IX_task_artifact_task_attempt_id] ON [dbo].[task_artifact]([task_attempt_id]);
CREATE INDEX [IX_task_artifact_task_id] ON [dbo].[task_artifact]([task_id]);
CREATE INDEX [IX_task_artifact_task_item_id] ON [dbo].[task_artifact]([task_item_id]);
CREATE INDEX [IX_worker_node_status] ON [dbo].[worker_node]([status]);
CREATE INDEX [IX_worker_node_executor_type] ON [dbo].[worker_node]([executor_type]);
CREATE INDEX [IX_worker_lock_resource] ON [dbo].[worker_lock]([resource_type], [resource_key]);
CREATE INDEX [IX_worker_lock_status] ON [dbo].[worker_lock]([status]);
CREATE INDEX [IX_operation_log_operator] ON [dbo].[operation_log]([operator]);
CREATE INDEX [IX_operation_log_platform] ON [dbo].[operation_log]([platform]);
CREATE INDEX [IX_knowledge_base_platform] ON [dbo].[knowledge_base]([platform]);
CREATE INDEX [IX_knowledge_base_category] ON [dbo].[knowledge_base]([category]);
CREATE INDEX [IX_system_config_config_key] ON [dbo].[system_config]([config_key]);
CREATE INDEX [IX_sync_log_sync_type] ON [dbo].[sync_log]([sync_type]);
CREATE INDEX [IX_sync_log_platform] ON [dbo].[sync_log]([platform]);
CREATE INDEX [IX_reflow_event_status] ON [dbo].[reflow_event]([status]);
CREATE INDEX [IX_reflow_event_platform] ON [dbo].[reflow_event]([platform]);
CREATE INDEX [IX_reflow_event_shop_id] ON [dbo].[reflow_event]([shop_id]);
CREATE INDEX [IX_reflow_event_task_id] ON [dbo].[reflow_event]([task_id]);
CREATE INDEX [IX_reflow_event_task_item_id] ON [dbo].[reflow_event]([task_item_id]);
CREATE INDEX [IX_user_user_id] ON [dbo].[user]([user_id]);
CREATE INDEX [IX_shop_config_shop_id] ON [dbo].[shop_config]([shop_id]);
CREATE INDEX [IX_shop_config_platform] ON [dbo].[shop_config]([platform]);

-- 过滤唯一索引：确保同一资源同一时刻只有一个 active 锁
CREATE UNIQUE INDEX [UX_worker_lock_active_resource]
ON [dbo].[worker_lock]([resource_type], [resource_key])
WHERE [status] = 'active';

-- 过滤唯一索引：同一任务项的 attempt_no 不重复
CREATE UNIQUE INDEX [UX_task_attempt_task_item_attempt_no]
ON [dbo].[task_attempt]([task_item_id], [attempt_no])
WHERE [task_item_id] IS NOT NULL;

-- 过滤唯一索引：确保幂等键不重复出箱
CREATE UNIQUE INDEX [UX_reflow_event_idempotency_key]
ON [dbo].[reflow_event]([idempotency_key])
WHERE [idempotency_key] IS NOT NULL;
GO

/*
权限说明：
- 配置/参考数据表：只给 SELECT
- 运行时业务表：给 SELECT, INSERT, UPDATE, DELETE
- 不给运行账号 CREATE TABLE / ALTER / CREATE INDEX 权限
*/
DECLARE @RuntimeUser SYSNAME = N'platform_mediation_app';

IF USER_ID(@RuntimeUser) IS NULL
BEGIN
    RAISERROR(N'数据库用户 %s 不存在，请先创建数据库用户或修改 @RuntimeUser。', 16, 1, @RuntimeUser);
    RETURN;
END;

DECLARE @sql NVARCHAR(MAX) = N'';

-- 配置/参考数据：只读
SET @sql += N'GRANT SELECT ON dbo.platform_adapter TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT ON dbo.platform_category_mapping TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT ON dbo.mapping_version TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT ON dbo.mapping_rule TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT ON dbo.knowledge_base TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT ON dbo.system_config TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT ON dbo.[user] TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT ON dbo.shop_config TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);

-- 运行时业务数据：读写
SET @sql += N'GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.worker_node TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.task TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.task_item TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.source_snapshot TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.task_attempt TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.task_artifact TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.worker_lock TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.operation_log TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.sync_log TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);
SET @sql += N'GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.reflow_event TO ' + QUOTENAME(@RuntimeUser) + N';' + CHAR(13) + CHAR(10);

EXEC sp_executesql @sql;
GO
