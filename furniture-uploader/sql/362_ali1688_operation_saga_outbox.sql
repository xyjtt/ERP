SET XACT_ABORT ON;
SET NOCOUNT ON;

IF SCHEMA_ID(N'app') IS NULL
    EXEC(N'CREATE SCHEMA app');
GO

IF OBJECT_ID(N'app.ali1688_operation_saga', N'U') IS NULL
BEGIN
    CREATE TABLE app.ali1688_operation_saga (
        operation_key                 CHAR(64) NOT NULL,
        run_id                        NVARCHAR(100) NOT NULL,
        task_type                     NVARCHAR(40) NOT NULL,
        account_key                   NVARCHAR(100) NOT NULL,
        business_key                  NVARCHAR(500) NOT NULL,
        state                         NVARCHAR(40) NOT NULL,
        owner_token_hash              CHAR(64) NOT NULL,
        account_fencing_token         BIGINT NOT NULL,
        browser_slot_key              NVARCHAR(200) NOT NULL,
        browser_slot_fencing_token    BIGINT NOT NULL,
        ali1688_status                NVARCHAR(60) NULL,
        jushuitan_status              NVARCHAR(60) NULL,
        payload_json                  NVARCHAR(MAX) NOT NULL,
        evidence_json                 NVARCHAR(MAX) NULL,
        error_code                    NVARCHAR(120) NULL,
        error_summary                 NVARCHAR(2000) NULL,
        prepared_at                   DATETIME2(3) NOT NULL CONSTRAINT DF_ali1688_operation_saga_prepared DEFAULT SYSUTCDATETIME(),
        ali1688_finished_at            DATETIME2(3) NULL,
        finished_at                   DATETIME2(3) NULL,
        updated_at                    DATETIME2(3) NOT NULL CONSTRAINT DF_ali1688_operation_saga_updated DEFAULT SYSUTCDATETIME(),
        row_version                   ROWVERSION NOT NULL,
        CONSTRAINT PK_ali1688_operation_saga PRIMARY KEY (operation_key),
        CONSTRAINT CK_ali1688_operation_saga_state CHECK (
            state IN (
                'prepared', 'ali1688_success', 'jushuitan_pending',
                'completed', 'failed_retryable', 'failed_terminal',
                'reconcile_required'
            )
        ),
        CONSTRAINT CK_ali1688_operation_saga_fencing CHECK (
            account_fencing_token > 0 AND browser_slot_fencing_token > 0
        ),
        CONSTRAINT CK_ali1688_operation_saga_payload_json CHECK (ISJSON(payload_json) = 1),
        CONSTRAINT CK_ali1688_operation_saga_evidence_json CHECK (
            evidence_json IS NULL OR ISJSON(evidence_json) = 1
        )
    );
END;
GO

IF OBJECT_ID(N'app.ali1688_operation_outbox', N'U') IS NULL
BEGIN
    CREATE TABLE app.ali1688_operation_outbox (
        outbox_id                     BIGINT IDENTITY(1,1) NOT NULL,
        operation_key                 CHAR(64) NOT NULL,
        topic                         NVARCHAR(100) NOT NULL,
        status                        NVARCHAR(40) NOT NULL,
        payload_json                  NVARCHAR(MAX) NOT NULL,
        attempt_count                 INT NOT NULL CONSTRAINT DF_ali1688_operation_outbox_attempt DEFAULT (0),
        available_at                  DATETIME2(3) NOT NULL CONSTRAINT DF_ali1688_operation_outbox_available DEFAULT SYSUTCDATETIME(),
        claim_owner                   NVARCHAR(100) NULL,
        claim_token                   CHAR(32) NULL,
        claim_until                   DATETIME2(3) NULL,
        last_error_code               NVARCHAR(120) NULL,
        last_error_summary            NVARCHAR(2000) NULL,
        created_at                    DATETIME2(3) NOT NULL CONSTRAINT DF_ali1688_operation_outbox_created DEFAULT SYSUTCDATETIME(),
        updated_at                    DATETIME2(3) NOT NULL CONSTRAINT DF_ali1688_operation_outbox_updated DEFAULT SYSUTCDATETIME(),
        completed_at                  DATETIME2(3) NULL,
        row_version                   ROWVERSION NOT NULL,
        CONSTRAINT PK_ali1688_operation_outbox PRIMARY KEY (outbox_id),
        CONSTRAINT UQ_ali1688_operation_outbox_operation UNIQUE (operation_key),
        CONSTRAINT FK_ali1688_operation_outbox_saga FOREIGN KEY (operation_key)
            REFERENCES app.ali1688_operation_saga(operation_key),
        CONSTRAINT CK_ali1688_operation_outbox_status CHECK (
            status IN ('pending', 'claimed', 'failed_retryable', 'succeeded', 'failed_terminal')
        ),
        CONSTRAINT CK_ali1688_operation_outbox_payload_json CHECK (ISJSON(payload_json) = 1)
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'app.ali1688_operation_saga')
      AND name = N'IX_ali1688_operation_saga_run_state'
)
    CREATE INDEX IX_ali1688_operation_saga_run_state
        ON app.ali1688_operation_saga(run_id, state, updated_at)
        INCLUDE(task_type, account_key, account_fencing_token);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'app.ali1688_operation_outbox')
      AND name = N'IX_ali1688_operation_outbox_claim'
)
    CREATE INDEX IX_ali1688_operation_outbox_claim
        ON app.ali1688_operation_outbox(status, available_at, outbox_id)
        INCLUDE(operation_key, topic, claim_until);
GO

GRANT SELECT, INSERT, UPDATE ON app.ali1688_operation_saga TO [app_writer];
GRANT SELECT, INSERT, UPDATE ON app.ali1688_operation_outbox TO [app_writer];
GO
