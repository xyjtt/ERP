-- 中文注释：表与字段 MS_Description 扩展属性
IF OBJECT_ID(N'[dbo].[usp_set_ms_description]', N'P') IS NOT NULL
    DROP PROCEDURE [dbo].[usp_set_ms_description];
GO
CREATE PROCEDURE [dbo].[usp_set_ms_description]
    @SchemaName SYSNAME,
    @TableName SYSNAME,
    @ColumnName SYSNAME = NULL,
    @Description NVARCHAR(4000)
AS
BEGIN
    SET NOCOUNT ON;

    IF @ColumnName IS NULL
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM sys.extended_properties ep
            INNER JOIN sys.tables t ON ep.major_id = t.object_id AND ep.minor_id = 0
            INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
            WHERE ep.name = N'MS_Description'
              AND s.name = @SchemaName
              AND t.name = @TableName
        )
            EXEC sys.sp_updateextendedproperty
                @name = N'MS_Description',
                @value = @Description,
                @level0type = N'SCHEMA', @level0name = @SchemaName,
                @level1type = N'TABLE',  @level1name = @TableName;
        ELSE
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = @Description,
                @level0type = N'SCHEMA', @level0name = @SchemaName,
                @level1type = N'TABLE',  @level1name = @TableName;
    END
    ELSE
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM sys.extended_properties ep
            INNER JOIN sys.tables t ON ep.major_id = t.object_id
            INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
            INNER JOIN sys.columns c ON c.object_id = t.object_id AND c.column_id = ep.minor_id
            WHERE ep.name = N'MS_Description'
              AND s.name = @SchemaName
              AND t.name = @TableName
              AND c.name = @ColumnName
        )
            EXEC sys.sp_updateextendedproperty
                @name = N'MS_Description',
                @value = @Description,
                @level0type = N'SCHEMA', @level0name = @SchemaName,
                @level1type = N'TABLE',  @level1name = @TableName,
                @level2type = N'COLUMN', @level2name = @ColumnName;
        ELSE
            EXEC sys.sp_addextendedproperty
                @name = N'MS_Description',
                @value = @Description,
                @level0type = N'SCHEMA', @level0name = @SchemaName,
                @level1type = N'TABLE',  @level1name = @TableName,
                @level2type = N'COLUMN', @level2name = @ColumnName;
    END
END;
GO

EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_adapter', @Description = N'平台适配器表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_adapter', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_adapter', @ColumnName = N'adapter_code', @Description = N'稳定业务键，如 1688_adapter';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_adapter', @ColumnName = N'adapter_name', @Description = N'适配器名称';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_adapter', @ColumnName = N'platform', @Description = N'平台类型：1688, taobao, douyin等';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_adapter', @ColumnName = N'executor_type', @Description = N'执行器类型：api, saas, rpa';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_adapter', @ColumnName = N'version', @Description = N'适配器版本';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_adapter', @ColumnName = N'capability_json', @Description = N'能力矩阵（JSON格式）';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_adapter', @ColumnName = N'config_json', @Description = N'适配器配置（JSON格式）';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_adapter', @ColumnName = N'status', @Description = N'状态：1-启用，0-禁用';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_adapter', @ColumnName = N'created_at', @Description = N'创建时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_adapter', @ColumnName = N'updated_at', @Description = N'更新时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_category_mapping', @Description = N'平台类目映射表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_category_mapping', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_category_mapping', @ColumnName = N'company_category_id', @Description = N'公司内部类目ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_category_mapping', @ColumnName = N'company_category_name', @Description = N'公司内部类目名称';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_category_mapping', @ColumnName = N'platform', @Description = N'平台类型：1688, taobao, douyin等';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_category_mapping', @ColumnName = N'platform_category_id', @Description = N'平台类目ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_category_mapping', @ColumnName = N'platform_category_name', @Description = N'平台类目名称';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_category_mapping', @ColumnName = N'status', @Description = N'状态：1-启用，0-禁用';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_category_mapping', @ColumnName = N'created_at', @Description = N'创建时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'platform_category_mapping', @ColumnName = N'updated_at', @Description = N'更新时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_version', @Description = N'映射版本表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_version', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_version', @ColumnName = N'mapping_code', @Description = N'稳定业务键，如 1688_listing';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_version', @ColumnName = N'platform', @Description = N'平台类型';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_version', @ColumnName = N'version', @Description = N'版本号';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_version', @ColumnName = N'standard_model_version', @Description = N'标准商品模型版本';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_version', @ColumnName = N'source_mode', @Description = N'git_managed / db_managed';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_version', @ColumnName = N'mapping_json', @Description = N'映射配置快照（JSON格式）';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_version', @ColumnName = N'status', @Description = N'状态：1-启用，0-禁用';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_version', @ColumnName = N'created_by', @Description = N'创建人';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_version', @ColumnName = N'created_at', @Description = N'创建时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_version', @ColumnName = N'updated_at', @Description = N'更新时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_rule', @Description = N'映射规则表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_rule', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_rule', @ColumnName = N'mapping_version_id', @Description = N'关联 mapping_version.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_rule', @ColumnName = N'rule_name', @Description = N'规则名称';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_rule', @ColumnName = N'rule_type', @Description = N'direct / parse / convert / default / generate';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_rule', @ColumnName = N'sort_order', @Description = N'执行顺序';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_rule', @ColumnName = N'rule_json', @Description = N'规则配置（JSON格式）';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_rule', @ColumnName = N'status', @Description = N'状态：1-启用，0-禁用';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_rule', @ColumnName = N'created_at', @Description = N'创建时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'mapping_rule', @ColumnName = N'updated_at', @Description = N'更新时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_node', @Description = N'工作节点表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_node', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_node', @ColumnName = N'node_id', @Description = N'节点ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_node', @ColumnName = N'node_name', @Description = N'节点名称';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_node', @ColumnName = N'executor_type', @Description = N'执行器类型';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_node', @ColumnName = N'platform', @Description = N'默认承载平台';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_node', @ColumnName = N'machine_name', @Description = N'所在机器';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_node', @ColumnName = N'session_key', @Description = N'登录会话标识';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_node', @ColumnName = N'status', @Description = N'节点状态：online, offline, busy, idle';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_node', @ColumnName = N'capability_json', @Description = N'节点能力（JSON格式）';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_node', @ColumnName = N'last_heartbeat', @Description = N'最后心跳时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_node', @ColumnName = N'created_at', @Description = N'创建时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_node', @ColumnName = N'updated_at', @Description = N'更新时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @Description = N'任务表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'task_id', @Description = N'任务ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'task_type', @Description = N'任务类型：listing, delisting';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'platform', @Description = N'平台类型';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'shop_id', @Description = N'店铺ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'status', @Description = N'任务状态：created, queued, running, success, failed, partial_success, cancelled';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'total_count', @Description = N'总商品数';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'success_count', @Description = N'成功数';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'failed_count', @Description = N'失败数';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'scheduled_at', @Description = N'调度时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'priority', @Description = N'优先级';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'next_retry_at', @Description = N'下次重试时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'idempotency_key', @Description = N'幂等键';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'executor_type', @Description = N'执行器类型';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'adapter_code', @Description = N'适配器业务键';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'mapping_version', @Description = N'映射版本';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'payload_json', @Description = N'任务级输入快照';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'created_by', @Description = N'创建人';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'created_at', @Description = N'创建时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'started_at', @Description = N'开始执行时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'completed_at', @Description = N'完成时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task', @ColumnName = N'remark', @Description = N'备注';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @Description = N'任务商品表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'task_id', @Description = N'关联 task.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'sku_id', @Description = N'商品SKU ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'platform_sku_id', @Description = N'平台SKU ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'platform_item_id', @Description = N'平台商品ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'status', @Description = N'状态：created, queued, running, success, failed, cancelled';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'source_snapshot_id', @Description = N'关联 source_snapshot.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'payload_json', @Description = N'执行 payload（JSON格式）';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'mapping_version', @Description = N'映射版本';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'source_version', @Description = N'源数据版本';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'attempt_count', @Description = N'尝试次数';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'result_json', @Description = N'标准化执行结果';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'verification_status', @Description = N'校验状态';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'verification_message', @Description = N'校验信息';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'error_message', @Description = N'错误信息';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'started_at', @Description = N'开始执行时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_item', @ColumnName = N'completed_at', @Description = N'完成时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'source_snapshot', @Description = N'源数据快照表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'source_snapshot', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'source_snapshot', @ColumnName = N'snapshot_id', @Description = N'快照ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'source_snapshot', @ColumnName = N'task_id', @Description = N'关联 task.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'source_snapshot', @ColumnName = N'task_item_id', @Description = N'关联 task_item.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'source_snapshot', @ColumnName = N'sku_id', @Description = N'商品SKU ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'source_snapshot', @ColumnName = N'source_type', @Description = N'jst_sku / api / excel 等';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'source_snapshot', @ColumnName = N'source_record_key', @Description = N'源记录业务键';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'source_snapshot', @ColumnName = N'snapshot_json', @Description = N'快照数据（JSON格式）';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'source_snapshot', @ColumnName = N'version', @Description = N'快照版本';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'source_snapshot', @ColumnName = N'created_at', @Description = N'创建时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @Description = N'任务执行尝试表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'attempt_id', @Description = N'尝试ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'task_id', @Description = N'关联 task.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'task_item_id', @Description = N'关联 task_item.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'attempt_no', @Description = N'第几次尝试';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'adapter_code', @Description = N'适配器业务键';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'adapter_version', @Description = N'适配器版本';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'mapping_version', @Description = N'映射版本';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'source_snapshot_id', @Description = N'关联 source_snapshot.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'source_version', @Description = N'源数据版本';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'worker_node_id', @Description = N'关联 worker_node.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'status', @Description = N'尝试状态：queued, running, success, failed, cancelled';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'error_type', @Description = N'错误类型';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'error_message', @Description = N'错误信息';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'retry_reason', @Description = N'重试原因';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'raw_result_json', @Description = N'原始执行结果';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'started_at', @Description = N'开始时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'completed_at', @Description = N'完成时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_attempt', @ColumnName = N'duration_ms', @Description = N'耗时';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_artifact', @Description = N'任务工件表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_artifact', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_artifact', @ColumnName = N'artifact_id', @Description = N'工件ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_artifact', @ColumnName = N'task_attempt_id', @Description = N'关联 task_attempt.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_artifact', @ColumnName = N'task_id', @Description = N'关联 task.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_artifact', @ColumnName = N'task_item_id', @Description = N'关联 task_item.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_artifact', @ColumnName = N'artifact_type', @Description = N'工件类型：run_report, screenshot, html, log';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_artifact', @ColumnName = N'artifact_path', @Description = N'工件路径';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_artifact', @ColumnName = N'metadata_json', @Description = N'元数据（JSON格式）';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'task_artifact', @ColumnName = N'created_at', @Description = N'创建时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_lock', @Description = N'工作锁表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_lock', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_lock', @ColumnName = N'lock_id', @Description = N'锁ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_lock', @ColumnName = N'resource_type', @Description = N'shop / account / session';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_lock', @ColumnName = N'resource_key', @Description = N'资源键';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_lock', @ColumnName = N'task_id', @Description = N'关联 task.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_lock', @ColumnName = N'task_item_id', @Description = N'关联 task_item.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_lock', @ColumnName = N'worker_node_id', @Description = N'关联 worker_node.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_lock', @ColumnName = N'status', @Description = N'active / released / expired';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_lock', @ColumnName = N'lease_until', @Description = N'租约到期时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_lock', @ColumnName = N'created_at', @Description = N'创建时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'worker_lock', @ColumnName = N'updated_at', @Description = N'更新时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'operation_log', @Description = N'操作日志表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'operation_log', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'operation_log', @ColumnName = N'operation_type', @Description = N'操作类型：任务创建，任务执行，任务失败等';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'operation_log', @ColumnName = N'operator', @Description = N'操作人';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'operation_log', @ColumnName = N'platform', @Description = N'平台类型';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'operation_log', @ColumnName = N'shop_id', @Description = N'店铺ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'operation_log', @ColumnName = N'sku_id', @Description = N'商品SKU ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'operation_log', @ColumnName = N'content', @Description = N'操作内容';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'operation_log', @ColumnName = N'result', @Description = N'操作结果：成功，失败';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'operation_log', @ColumnName = N'error_message', @Description = N'错误信息';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'operation_log', @ColumnName = N'created_at', @Description = N'操作时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'knowledge_base', @Description = N'知识库表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'knowledge_base', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'knowledge_base', @ColumnName = N'title', @Description = N'标题';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'knowledge_base', @ColumnName = N'content', @Description = N'内容';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'knowledge_base', @ColumnName = N'platform', @Description = N'平台类型';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'knowledge_base', @ColumnName = N'category', @Description = N'分类：问题，解决方案，经验等';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'knowledge_base', @ColumnName = N'status', @Description = N'状态：1-启用，0-禁用';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'knowledge_base', @ColumnName = N'created_by', @Description = N'创建人';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'knowledge_base', @ColumnName = N'created_at', @Description = N'创建时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'knowledge_base', @ColumnName = N'updated_at', @Description = N'更新时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'system_config', @Description = N'系统配置表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'system_config', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'system_config', @ColumnName = N'config_key', @Description = N'配置键';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'system_config', @ColumnName = N'config_value', @Description = N'配置值';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'system_config', @ColumnName = N'description', @Description = N'描述';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'system_config', @ColumnName = N'status', @Description = N'状态：1-启用，0-禁用';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'system_config', @ColumnName = N'updated_by', @Description = N'更新人';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'system_config', @ColumnName = N'updated_at', @Description = N'更新时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'sync_log', @Description = N'数据同步日志表';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'sync_log', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'sync_log', @ColumnName = N'sync_type', @Description = N'同步类型：platform_data, shop_data等';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'sync_log', @ColumnName = N'platform', @Description = N'平台类型';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'sync_log', @ColumnName = N'shop_id', @Description = N'店铺ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'sync_log', @ColumnName = N'status', @Description = N'同步状态：成功，失败';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'sync_log', @ColumnName = N'sync_count', @Description = N'同步数量';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'sync_log', @ColumnName = N'error_count', @Description = N'错误数量';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'sync_log', @ColumnName = N'error_message', @Description = N'错误信息';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'sync_log', @ColumnName = N'started_at', @Description = N'开始时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'sync_log', @ColumnName = N'completed_at', @Description = N'完成时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @Description = N'数据回流事件表（outbox）';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'event_id', @Description = N'事件ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'event_type', @Description = N'事件类型：item_listed, item_updated, item_delisted';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'platform', @Description = N'平台类型';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'shop_id', @Description = N'店铺ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'task_id', @Description = N'关联 task.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'task_item_id', @Description = N'关联 task_item.id';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'sku_id', @Description = N'商品SKU ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'platform_item_id', @Description = N'平台商品ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'platform_sku_id', @Description = N'平台SKU ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'idempotency_key', @Description = N'幂等键';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'payload_json', @Description = N'事件数据（JSON格式）';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'status', @Description = N'事件状态：pending, processing, success, failed';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'retry_count', @Description = N'重试次数';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'next_retry_at', @Description = N'下次重试时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'error_message', @Description = N'错误信息';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'created_at', @Description = N'创建时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'reflow_event', @ColumnName = N'updated_at', @Description = N'更新时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'user', @Description = N'人员表（与钉钉集成）';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'user', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'user', @ColumnName = N'user_id', @Description = N'钉钉用户ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'user', @ColumnName = N'name', @Description = N'姓名';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'user', @ColumnName = N'email', @Description = N'邮箱';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'user', @ColumnName = N'phone', @Description = N'电话';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'user', @ColumnName = N'department', @Description = N'部门';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'user', @ColumnName = N'status', @Description = N'状态：1-启用，0-禁用';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'user', @ColumnName = N'created_at', @Description = N'创建时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'user', @ColumnName = N'updated_at', @Description = N'更新时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @Description = N'店铺配置表（扩展）';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @ColumnName = N'id', @Description = N'主键ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @ColumnName = N'shop_id', @Description = N'店铺ID';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @ColumnName = N'platform', @Description = N'平台类型';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @ColumnName = N'shop_name', @Description = N'店铺名称';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @ColumnName = N'credential_ref', @Description = N'外部凭证引用';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @ColumnName = N'app_key_encrypted', @Description = N'加密的应用密钥';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @ColumnName = N'app_secret_encrypted', @Description = N'加密的应用密钥';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @ColumnName = N'access_token_encrypted', @Description = N'加密的访问令牌';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @ColumnName = N'runtime_config_json', @Description = N'运行时配置';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @ColumnName = N'token_expire_time', @Description = N'令牌过期时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @ColumnName = N'status', @Description = N'状态：1-启用，0-禁用';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @ColumnName = N'created_at', @Description = N'创建时间';
EXEC [dbo].[usp_set_ms_description] @SchemaName = N'dbo', @TableName = N'shop_config', @ColumnName = N'updated_at', @Description = N'更新时间';
GO
DROP PROCEDURE [dbo].[usp_set_ms_description];
GO
