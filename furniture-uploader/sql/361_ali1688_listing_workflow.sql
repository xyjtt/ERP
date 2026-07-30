IF DB_NAME() <> N'JSReportReplica'
BEGIN
    RAISERROR(N'This DDL must run in JSReportReplica.', 16, 1);
    RETURN;
END;
GO

IF OBJECT_ID(N'app.ali1688_listing_task', N'U') IS NULL
BEGIN
    CREATE TABLE app.ali1688_listing_task (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        task_id NVARCHAR(100) NOT NULL,
        schema_version NVARCHAR(50) NOT NULL,
        platform NVARCHAR(30) NOT NULL,
        shop_name NVARCHAR(200) NOT NULL,
        account_key NVARCHAR(100) NOT NULL,
        company_sku NVARCHAR(150) NOT NULL,
        company_spu NVARCHAR(150) NOT NULL,
        novelty_type NVARCHAR(30) NOT NULL,
        selected_title NVARCHAR(500) NOT NULL,
        workflow_state NVARCHAR(50) NOT NULL,
        approval_status NVARCHAR(50) NOT NULL,
        approved_by NVARCHAR(150) NULL,
        approved_at DATETIME2(0) NULL,
        idempotency_key NVARCHAR(200) NOT NULL,
        payload_json NVARCHAR(MAX) NOT NULL,
        preflight_json NVARCHAR(MAX) NULL,
        claimed_by NVARCHAR(150) NULL,
        claim_until DATETIME2(0) NULL,
        created_at DATETIME2(0) NOT NULL CONSTRAINT DF_ali1688_listing_task_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(0) NOT NULL CONSTRAINT DF_ali1688_listing_task_updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_ali1688_listing_task_id UNIQUE (task_id),
        CONSTRAINT UQ_ali1688_listing_idempotency UNIQUE (idempotency_key)
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = N'IX_ali1688_listing_task_claim'
      AND object_id = OBJECT_ID(N'app.ali1688_listing_task')
)
BEGIN
    CREATE INDEX IX_ali1688_listing_task_claim
        ON app.ali1688_listing_task (workflow_state, approval_status, claim_until, created_at);
END;
GO

IF OBJECT_ID(N'app.ali1688_listing_execution', N'U') IS NULL
BEGIN
    CREATE TABLE app.ali1688_listing_execution (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        task_id NVARCHAR(100) NOT NULL,
        execution_id UNIQUEIDENTIFIER NOT NULL,
        execution_mode NVARCHAR(30) NOT NULL,
        status NVARCHAR(40) NOT NULL,
        offer_id NVARCHAR(100) NULL,
        offer_url NVARCHAR(1000) NULL,
        result_json NVARCHAR(MAX) NULL,
        error_code NVARCHAR(100) NULL,
        error_summary NVARCHAR(1000) NULL,
        started_at DATETIME2(0) NOT NULL,
        finished_at DATETIME2(0) NULL,
        CONSTRAINT UQ_ali1688_listing_execution UNIQUE (execution_id)
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = N'IX_ali1688_listing_execution_task'
      AND object_id = OBJECT_ID(N'app.ali1688_listing_execution')
)
BEGIN
    CREATE INDEX IX_ali1688_listing_execution_task
        ON app.ali1688_listing_execution (task_id, started_at DESC);
END;
GO

IF OBJECT_ID(N'app.ali1688_listing_audit', N'U') IS NULL
BEGIN
    CREATE TABLE app.ali1688_listing_audit (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        task_id NVARCHAR(100) NOT NULL,
        event_type NVARCHAR(60) NOT NULL,
        from_state NVARCHAR(50) NULL,
        to_state NVARCHAR(50) NULL,
        operator_name NVARCHAR(150) NULL,
        evidence_json NVARCHAR(MAX) NULL,
        created_at DATETIME2(0) NOT NULL CONSTRAINT DF_ali1688_listing_audit_created DEFAULT SYSUTCDATETIME()
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = N'IX_ali1688_listing_audit_task'
      AND object_id = OBJECT_ID(N'app.ali1688_listing_audit')
)
BEGIN
    CREATE INDEX IX_ali1688_listing_audit_task
        ON app.ali1688_listing_audit (task_id, created_at DESC);
END;
GO

IF DATABASE_PRINCIPAL_ID(N'app_writer') IS NOT NULL
BEGIN
    GRANT SELECT, INSERT, UPDATE, DELETE ON app.ali1688_listing_task TO [app_writer];
    GRANT SELECT, INSERT, UPDATE, DELETE ON app.ali1688_listing_execution TO [app_writer];
    GRANT SELECT, INSERT, UPDATE, DELETE ON app.ali1688_listing_audit TO [app_writer];
END;
GO
