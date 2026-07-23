/*
  1688 SKU替换执行审计表
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

IF OBJECT_ID(N'app.ali1688_sku_replace_run', N'U') IS NULL
BEGIN
    CREATE TABLE app.ali1688_sku_replace_run (
        run_id                      NVARCHAR(64) NOT NULL CONSTRAINT PK_ali1688_sku_replace_run PRIMARY KEY,
        mode                        NVARCHAR(20) NOT NULL,
        source_database             NVARCHAR(128) NOT NULL,
        source_table                NVARCHAR(256) NOT NULL,
        input_file                  NVARCHAR(1000) NOT NULL,
        status                      NVARCHAR(40) NOT NULL,
        total_count                 INT NOT NULL CONSTRAINT DF_ali1688_sku_replace_run_total DEFAULT (0),
        replace_target_count        INT NOT NULL CONSTRAINT DF_ali1688_sku_replace_run_target DEFAULT (0),
        jushuitan_target_count      INT NOT NULL CONSTRAINT DF_ali1688_sku_replace_run_jst_target DEFAULT (0),
        replace_success_count       INT NOT NULL CONSTRAINT DF_ali1688_sku_replace_run_success DEFAULT (0),
        replace_already_count       INT NOT NULL CONSTRAINT DF_ali1688_sku_replace_run_already DEFAULT (0),
        replace_failed_count        INT NOT NULL CONSTRAINT DF_ali1688_sku_replace_run_failed DEFAULT (0),
        not_attempted_count         INT NOT NULL CONSTRAINT DF_ali1688_sku_replace_run_not_attempted DEFAULT (0),
        jushuitan_success_count     INT NOT NULL CONSTRAINT DF_ali1688_sku_replace_run_jst_success DEFAULT (0),
        jushuitan_already_count     INT NOT NULL CONSTRAINT DF_ali1688_sku_replace_run_jst_already DEFAULT (0),
        jushuitan_failed_count      INT NOT NULL CONSTRAINT DF_ali1688_sku_replace_run_jst_failed DEFAULT (0),
        replace_report_path         NVARCHAR(1000) NULL,
        jushuitan_report_path       NVARCHAR(1000) NULL,
        summary_path                NVARCHAR(1000) NULL,
        error_message               NVARCHAR(2000) NULL,
        started_at                  DATETIME2 NOT NULL,
        finished_at                 DATETIME2 NULL,
        created_at                  DATETIME2 NOT NULL,
        updated_at                  DATETIME2 NOT NULL
    );
END;

IF OBJECT_ID(N'app.ali1688_sku_replace_item', N'U') IS NULL
BEGIN
    CREATE TABLE app.ali1688_sku_replace_item (
        id                          BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_ali1688_sku_replace_item PRIMARY KEY,
        run_id                      NVARCHAR(64) NOT NULL,
        task_key                    CHAR(64) NOT NULL,
        store_name                  NVARCHAR(200) NOT NULL,
        platform                    NVARCHAR(40) NOT NULL,
        product_id                  NVARCHAR(100) NOT NULL,
        platform_store_item_code    NVARCHAR(100) NULL,
        online_sku                  NVARCHAR(200) NOT NULL,
        replacement_sku             NVARCHAR(200) NOT NULL,
        handling                    NVARCHAR(100) NOT NULL,
        metric_date                 DATE NULL,
        source_row_number           INT NULL,
        replace_status              NVARCHAR(40) NOT NULL,
        attempts                    INT NOT NULL CONSTRAINT DF_ali1688_sku_replace_item_attempts DEFAULT (0),
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
        CONSTRAINT FK_ali1688_sku_replace_item_run
            FOREIGN KEY (run_id) REFERENCES app.ali1688_sku_replace_run(run_id),
        CONSTRAINT UQ_ali1688_sku_replace_item_run_task UNIQUE (run_id, task_key)
    );
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'app.ali1688_sku_replace_item')
      AND name = N'IX_ali1688_sku_replace_item_identity'
)
BEGIN
    CREATE INDEX IX_ali1688_sku_replace_item_identity
        ON app.ali1688_sku_replace_item (store_name, product_id)
        INCLUDE (online_sku, replacement_sku, replace_status);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'app.ali1688_sku_replace_run')
      AND name = N'IX_ali1688_sku_replace_run_started'
)
BEGIN
    CREATE INDEX IX_ali1688_sku_replace_run_started
        ON app.ali1688_sku_replace_run (started_at DESC);
END;

GRANT SELECT, INSERT, UPDATE, DELETE ON app.ali1688_sku_replace_run TO [app_writer];
GRANT SELECT, INSERT, UPDATE, DELETE ON app.ali1688_sku_replace_item TO [app_writer];
