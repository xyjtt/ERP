/*
  1688 停售执行审计表
  目标数据库: JSReportReplica
  目标 schema: app
  兼容 SQL Server 2012，可重复执行。
*/

IF DB_NAME() <> N'JSReportReplica'
BEGIN
    RAISERROR(N'数据库上下文错误：本文件只能在 JSReportReplica 执行。', 16, 1);
    RETURN;
END;

IF SCHEMA_ID(N'app') IS NULL
BEGIN
    RAISERROR(N'目标 schema app 不存在。', 16, 1);
    RETURN;
END;

IF OBJECT_ID(N'app.ali1688_stop_sale_run', N'U') IS NULL
BEGIN
    CREATE TABLE app.ali1688_stop_sale_run (
        run_id                      NVARCHAR(64) NOT NULL CONSTRAINT PK_ali1688_stop_sale_run PRIMARY KEY,
        mode                        NVARCHAR(20) NOT NULL,
        source_database             NVARCHAR(128) NOT NULL,
        source_table                NVARCHAR(256) NOT NULL,
        input_file                  NVARCHAR(1000) NOT NULL,
        status                      NVARCHAR(40) NOT NULL,
        total_count                 INT NOT NULL CONSTRAINT DF_ali1688_stop_sale_run_total DEFAULT (0),
        offline_target_count        INT NOT NULL CONSTRAINT DF_ali1688_stop_sale_run_offline_target DEFAULT (0),
        jushuitan_target_count      INT NOT NULL CONSTRAINT DF_ali1688_stop_sale_run_jst_target DEFAULT (0),
        offline_success_count       INT NOT NULL CONSTRAINT DF_ali1688_stop_sale_run_success DEFAULT (0),
        offline_already_count       INT NOT NULL CONSTRAINT DF_ali1688_stop_sale_run_already DEFAULT (0),
        offline_failed_count        INT NOT NULL CONSTRAINT DF_ali1688_stop_sale_run_failed DEFAULT (0),
        not_attempted_count         INT NOT NULL CONSTRAINT DF_ali1688_stop_sale_run_not_attempted DEFAULT (0),
        jushuitan_success_count     INT NOT NULL CONSTRAINT DF_ali1688_stop_sale_run_jst_success DEFAULT (0),
        jushuitan_already_count     INT NOT NULL CONSTRAINT DF_ali1688_stop_sale_run_jst_already DEFAULT (0),
        jushuitan_failed_count      INT NOT NULL CONSTRAINT DF_ali1688_stop_sale_run_jst_failed DEFAULT (0),
        offline_report_path         NVARCHAR(1000) NULL,
        jushuitan_report_path       NVARCHAR(1000) NULL,
        summary_path                NVARCHAR(1000) NULL,
        error_message               NVARCHAR(2000) NULL,
        started_at                  DATETIME2 NOT NULL,
        finished_at                 DATETIME2 NULL,
        created_at                  DATETIME2 NOT NULL,
        updated_at                  DATETIME2 NOT NULL
    );
END;

IF OBJECT_ID(N'app.ali1688_stop_sale_item', N'U') IS NULL
BEGIN
    CREATE TABLE app.ali1688_stop_sale_item (
        id                          BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_ali1688_stop_sale_item PRIMARY KEY,
        run_id                      NVARCHAR(64) NOT NULL,
        task_key                    CHAR(64) NOT NULL,
        offline_task_key            CHAR(64) NOT NULL,
        store_name                  NVARCHAR(200) NOT NULL,
        platform                    NVARCHAR(40) NOT NULL,
        product_id                  NVARCHAR(100) NOT NULL,
        platform_store_item_code    NVARCHAR(100) NULL,
        online_sku                  NVARCHAR(200) NOT NULL,
        handling                    NVARCHAR(100) NOT NULL,
        metric_date                 DATE NULL,
        source_row_number           INT NULL,
        offline_status              NVARCHAR(40) NOT NULL,
        attempts                    INT NOT NULL CONSTRAINT DF_ali1688_stop_sale_item_attempts DEFAULT (0),
        error_category              NVARCHAR(100) NULL,
        error_message               NVARCHAR(2000) NULL,
        screenshot_path             NVARCHAR(1000) NULL,
        html_snapshot_path          NVARCHAR(1000) NULL,
        jushuitan_status            NVARCHAR(40) NULL,
        jushuitan_category          NVARCHAR(100) NULL,
        jushuitan_message           NVARCHAR(2000) NULL,
        jushuitan_evidence_path     NVARCHAR(1000) NULL,
        created_at                  DATETIME2 NOT NULL,
        updated_at                  DATETIME2 NOT NULL,
        CONSTRAINT FK_ali1688_stop_sale_item_run
            FOREIGN KEY (run_id) REFERENCES app.ali1688_stop_sale_run(run_id),
        CONSTRAINT UQ_ali1688_stop_sale_item_run_task UNIQUE (run_id, task_key)
    );
END;

IF COL_LENGTH(N'app.ali1688_stop_sale_run', N'offline_target_count') IS NULL
BEGIN
    ALTER TABLE app.ali1688_stop_sale_run
        ADD offline_target_count INT NOT NULL
            CONSTRAINT DF_ali1688_stop_sale_run_offline_target DEFAULT (0);
END;

IF COL_LENGTH(N'app.ali1688_stop_sale_run', N'jushuitan_target_count') IS NULL
BEGIN
    ALTER TABLE app.ali1688_stop_sale_run
        ADD jushuitan_target_count INT NOT NULL
            CONSTRAINT DF_ali1688_stop_sale_run_jst_target DEFAULT (0);
END;

IF COL_LENGTH(N'app.ali1688_stop_sale_item', N'offline_task_key') IS NULL
BEGIN
    ALTER TABLE app.ali1688_stop_sale_item ADD offline_task_key CHAR(64) NULL;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'app.ali1688_stop_sale_item')
      AND name = N'IX_ali1688_stop_sale_item_identity'
)
BEGIN
    CREATE INDEX IX_ali1688_stop_sale_item_identity
        ON app.ali1688_stop_sale_item (store_name, product_id, online_sku);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'app.ali1688_stop_sale_item')
      AND name = N'IX_ali1688_stop_sale_item_offline_task'
)
BEGIN
    CREATE INDEX IX_ali1688_stop_sale_item_offline_task
        ON app.ali1688_stop_sale_item (run_id, offline_task_key);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'app.ali1688_stop_sale_run')
      AND name = N'IX_ali1688_stop_sale_run_started'
)
BEGIN
    CREATE INDEX IX_ali1688_stop_sale_run_started
        ON app.ali1688_stop_sale_run (started_at DESC);
END;

GRANT SELECT, INSERT, UPDATE, DELETE ON app.ali1688_stop_sale_run TO [app_writer];
GRANT SELECT, INSERT, UPDATE, DELETE ON app.ali1688_stop_sale_item TO [app_writer];
