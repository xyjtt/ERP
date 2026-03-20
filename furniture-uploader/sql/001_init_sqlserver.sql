IF OBJECT_ID('dbo.publish_task', 'U') IS NOT NULL DROP TABLE dbo.publish_task;
IF OBJECT_ID('dbo.publish_result', 'U') IS NOT NULL DROP TABLE dbo.publish_result;
IF OBJECT_ID('dbo.publish_error_log', 'U') IS NOT NULL DROP TABLE dbo.publish_error_log;
IF OBJECT_ID('dbo.match_candidate_history', 'U') IS NOT NULL DROP TABLE dbo.match_candidate_history;
IF OBJECT_ID('dbo.category_mapping_history', 'U') IS NOT NULL DROP TABLE dbo.category_mapping_history;
IF OBJECT_ID('dbo.store_default_template', 'U') IS NOT NULL DROP TABLE dbo.store_default_template;
GO

CREATE TABLE dbo.publish_task (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    task_id NVARCHAR(64) NOT NULL,
    source_type NVARCHAR(32) NOT NULL DEFAULT 'excel',
    source_record_id NVARCHAR(128) NULL,
    channel NVARCHAR(32) NOT NULL,
    store_name NVARCHAR(128) NOT NULL,
    store_label NVARCHAR(256) NULL,
    outer_sku NVARCHAR(128) NOT NULL,
    title NVARCHAR(500) NOT NULL,
    category_hint NVARCHAR(500) NULL,
    brand NVARCHAR(128) NULL,
    material NVARCHAR(128) NULL,
    color NVARCHAR(128) NULL,
    size NVARCHAR(128) NULL,
    price DECIMAL(18,2) NULL,
    quantity INT NULL,
    main_image NVARCHAR(1000) NULL,
    detail_images NVARCHAR(MAX) NULL,
    description NVARCHAR(MAX) NULL,
    ship_from_template NVARCHAR(128) NULL,
    freight_template NVARCHAR(128) NULL,
    ship_time_template NVARCHAR(128) NULL,
    length_cm DECIMAL(18,2) NULL,
    width_cm DECIMAL(18,2) NULL,
    height_cm DECIMAL(18,2) NULL,
    weight_g DECIMAL(18,2) NULL,
    link_owner NVARCHAR(128) NULL,
    operator_name NVARCHAR(128) NULL,
    status NVARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at DATETIME NOT NULL DEFAULT GETDATE(),
    updated_at DATETIME NOT NULL DEFAULT GETDATE()
);
GO

CREATE TABLE dbo.publish_result (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    task_id NVARCHAR(64) NOT NULL,
    channel NVARCHAR(32) NOT NULL,
    store_name NVARCHAR(128) NOT NULL,
    outer_sku NVARCHAR(128) NOT NULL,
    platform_link_id NVARCHAR(128) NULL,
    platform_link_url NVARCHAR(1000) NULL,
    platform_item_title NVARCHAR(500) NULL,
    link_owner NVARCHAR(128) NULL,
    operator_name NVARCHAR(128) NULL,
    published_at DATETIME NOT NULL DEFAULT GETDATE(),
    source_type NVARCHAR(32) NULL,
    source_record_id NVARCHAR(128) NULL,
    match_source_id NVARCHAR(128) NULL,
    category_path NVARCHAR(500) NULL,
    status NVARCHAR(32) NOT NULL DEFAULT 'success'
);
GO

CREATE TABLE dbo.publish_error_log (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    task_id NVARCHAR(64) NOT NULL,
    channel NVARCHAR(32) NOT NULL,
    store_name NVARCHAR(128) NOT NULL,
    outer_sku NVARCHAR(128) NOT NULL,
    step_name NVARCHAR(128) NULL,
    error_type NVARCHAR(128) NOT NULL,
    error_message NVARCHAR(MAX) NULL,
    screenshot_path NVARCHAR(1000) NULL,
    html_snapshot_path NVARCHAR(1000) NULL,
    operator_name NVARCHAR(128) NULL,
    failed_at DATETIME NOT NULL DEFAULT GETDATE(),
    status NVARCHAR(32) NOT NULL DEFAULT 'failed'
);
GO

CREATE TABLE dbo.match_candidate_history (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    task_id NVARCHAR(64) NULL,
    outer_sku NVARCHAR(128) NOT NULL,
    channel NVARCHAR(32) NOT NULL,
    store_name NVARCHAR(128) NOT NULL,
    candidate_source_id NVARCHAR(128) NULL,
    candidate_store_name NVARCHAR(128) NULL,
    candidate_title NVARCHAR(500) NULL,
    candidate_use_result NVARCHAR(64) NOT NULL,
    candidate_error_message NVARCHAR(MAX) NULL,
    candidate_error_type NVARCHAR(128) NULL,
    candidate_attempted_at DATETIME NOT NULL DEFAULT GETDATE()
);
GO

CREATE TABLE dbo.category_mapping_history (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    channel NVARCHAR(32) NOT NULL,
    store_name NVARCHAR(128) NULL,
    keyword_text NVARCHAR(500) NOT NULL,
    category_path NVARCHAR(500) NOT NULL,
    success_count INT NOT NULL DEFAULT 0,
    failure_count INT NOT NULL DEFAULT 0,
    last_success_at DATETIME NULL,
    last_failure_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT GETDATE(),
    updated_at DATETIME NOT NULL DEFAULT GETDATE()
);
GO

CREATE TABLE dbo.store_default_template (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    channel NVARCHAR(32) NOT NULL,
    store_name NVARCHAR(128) NOT NULL,
    ship_from_template NVARCHAR(128) NULL,
    freight_template NVARCHAR(128) NULL,
    ship_time_template NVARCHAR(128) NULL,
    default_length_cm DECIMAL(18,2) NULL,
    default_width_cm DECIMAL(18,2) NULL,
    default_height_cm DECIMAL(18,2) NULL,
    default_weight_g DECIMAL(18,2) NULL,
    updated_at DATETIME NOT NULL DEFAULT GETDATE()
);
GO

CREATE INDEX IX_publish_task_task_id ON dbo.publish_task(task_id);
CREATE INDEX IX_publish_task_store_sku ON dbo.publish_task(store_name, outer_sku);
CREATE UNIQUE INDEX UX_publish_task_task_store_sku ON dbo.publish_task(task_id, channel, store_name, outer_sku);
CREATE INDEX IX_publish_result_task_id ON dbo.publish_result(task_id);
CREATE INDEX IX_match_candidate_history_lookup ON dbo.match_candidate_history(outer_sku, channel, store_name);
CREATE INDEX IX_category_mapping_history_lookup ON dbo.category_mapping_history(channel, keyword_text);
CREATE UNIQUE INDEX UX_store_default_template_store ON dbo.store_default_template(channel, store_name);
GO
