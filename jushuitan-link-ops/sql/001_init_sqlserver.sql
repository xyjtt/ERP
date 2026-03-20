IF OBJECT_ID('dbo.jst_code_change_task', 'U') IS NOT NULL DROP TABLE dbo.jst_code_change_task;
IF OBJECT_ID('dbo.jst_code_change_result', 'U') IS NOT NULL DROP TABLE dbo.jst_code_change_result;
IF OBJECT_ID('dbo.jst_code_change_error_log', 'U') IS NOT NULL DROP TABLE dbo.jst_code_change_error_log;
GO

CREATE TABLE dbo.jst_code_change_task (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    task_id NVARCHAR(64) NOT NULL,
    source_type NVARCHAR(32) NOT NULL,
    source_record_id NVARCHAR(128) NULL,
    platform NVARCHAR(64) NOT NULL,
    shop_name NVARCHAR(128) NOT NULL,
    old_online_sku_code NVARCHAR(128) NOT NULL,
    new_online_sku_code NVARCHAR(128) NOT NULL,
    operator_name NVARCHAR(128) NULL,
    remark NVARCHAR(500) NULL,
    status NVARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at DATETIME NOT NULL DEFAULT GETDATE(),
    updated_at DATETIME NOT NULL DEFAULT GETDATE()
);
GO

CREATE TABLE dbo.jst_code_change_result (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    task_id NVARCHAR(64) NOT NULL,
    platform NVARCHAR(64) NOT NULL,
    shop_name NVARCHAR(128) NOT NULL,
    old_online_sku_code NVARCHAR(128) NOT NULL,
    new_online_sku_code NVARCHAR(128) NOT NULL,
    matched_count_before INT NOT NULL DEFAULT 0,
    remaining_count_after INT NOT NULL DEFAULT 0,
    status NVARCHAR(32) NOT NULL,
    operator_name NVARCHAR(128) NULL,
    finished_at DATETIME NOT NULL DEFAULT GETDATE()
);
GO

CREATE TABLE dbo.jst_code_change_error_log (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    task_id NVARCHAR(64) NOT NULL,
    platform NVARCHAR(64) NULL,
    shop_name NVARCHAR(128) NULL,
    old_online_sku_code NVARCHAR(128) NULL,
    new_online_sku_code NVARCHAR(128) NULL,
    step_name NVARCHAR(128) NULL,
    error_type NVARCHAR(128) NOT NULL,
    error_message NVARCHAR(MAX) NULL,
    screenshot_path NVARCHAR(1000) NULL,
    html_snapshot_path NVARCHAR(1000) NULL,
    current_url NVARCHAR(1000) NULL,
    page_title NVARCHAR(500) NULL,
    created_at DATETIME NOT NULL DEFAULT GETDATE()
);
GO

CREATE INDEX IX_jst_code_change_task_task_id ON dbo.jst_code_change_task(task_id);
CREATE INDEX IX_jst_code_change_result_task_id ON dbo.jst_code_change_result(task_id);
CREATE INDEX IX_jst_code_change_result_old_code ON dbo.jst_code_change_result(old_online_sku_code);
CREATE INDEX IX_jst_code_change_error_task_id ON dbo.jst_code_change_error_log(task_id);
GO
