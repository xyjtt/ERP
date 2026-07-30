IF OBJECT_ID(N'app.ali1688_title_hsds_root_word_snapshot', N'U') IS NULL
BEGIN
    CREATE TABLE app.ali1688_title_hsds_root_word_snapshot (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        stat_date DATE NOT NULL,
        window_start DATE NOT NULL,
        window_end DATE NOT NULL,
        category_level_1 NVARCHAR(200) NULL,
        category_level_2 NVARCHAR(200) NULL,
        category_level_3 NVARCHAR(200) NULL,
        root_word NVARCHAR(200) NOT NULL,
        search_people DECIMAL(18,4) NULL,
        search_people_grow DECIMAL(18,6) NULL,
        source_record_key NVARCHAR(300) NOT NULL,
        source_hash CHAR(64) NOT NULL,
        raw_payload NVARCHAR(MAX) NULL,
        demand_primary_name NVARCHAR(200) NULL,
        is_new_root BIT NULL,
        source_type NVARCHAR(50) NOT NULL CONSTRAINT DF_ali1688_title_hsds_source_type DEFAULT N'hsds_demand_root',
        source_updated_at DATETIME2(0) NULL,
        extracted_at DATETIME2(0) NOT NULL CONSTRAINT DF_ali1688_title_hsds_extracted_at DEFAULT SYSUTCDATETIME(),
        migration_run_id UNIQUEIDENTIFIER NULL,
        loaded_at DATETIME2(0) NOT NULL CONSTRAINT DF_ali1688_title_hsds_loaded_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_ali1688_title_hsds_snapshot UNIQUE (
            stat_date, category_level_1, category_level_2, category_level_3,
            root_word, source_record_key
        )
    );
END;
GO

IF COL_LENGTH(N'app.ali1688_title_hsds_root_word_snapshot', N'demand_primary_name') IS NULL
    ALTER TABLE app.ali1688_title_hsds_root_word_snapshot ADD demand_primary_name NVARCHAR(200) NULL;
IF COL_LENGTH(N'app.ali1688_title_hsds_root_word_snapshot', N'is_new_root') IS NULL
    ALTER TABLE app.ali1688_title_hsds_root_word_snapshot ADD is_new_root BIT NULL;
IF COL_LENGTH(N'app.ali1688_title_hsds_root_word_snapshot', N'source_type') IS NULL
    ALTER TABLE app.ali1688_title_hsds_root_word_snapshot ADD source_type NVARCHAR(50) NOT NULL
        CONSTRAINT DF_ali1688_title_hsds_source_type DEFAULT N'hsds_demand_root';
IF COL_LENGTH(N'app.ali1688_title_hsds_root_word_snapshot', N'extracted_at') IS NULL
    ALTER TABLE app.ali1688_title_hsds_root_word_snapshot ADD extracted_at DATETIME2(0) NOT NULL
        CONSTRAINT DF_ali1688_title_hsds_extracted_at DEFAULT SYSUTCDATETIME();
IF COL_LENGTH(N'app.ali1688_title_hsds_root_word_snapshot', N'migration_run_id') IS NULL
    ALTER TABLE app.ali1688_title_hsds_root_word_snapshot ADD migration_run_id UNIQUEIDENTIFIER NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = N'IX_ali1688_title_hsds_window'
      AND object_id = OBJECT_ID(N'app.ali1688_title_hsds_root_word_snapshot')
)
BEGIN
    CREATE INDEX IX_ali1688_title_hsds_window
        ON app.ali1688_title_hsds_root_word_snapshot (window_end, category_level_3, search_people);
END;
GO

IF OBJECT_ID(N'app.ali1688_title_source_sync_run', N'U') IS NULL
BEGIN
    CREATE TABLE app.ali1688_title_source_sync_run (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        run_id UNIQUEIDENTIFIER NOT NULL,
        source_type NVARCHAR(80) NOT NULL,
        window_start DATE NULL,
        window_end DATE NULL,
        started_at DATETIME2(0) NOT NULL,
        finished_at DATETIME2(0) NULL,
        status NVARCHAR(30) NOT NULL,
        extracted_count INT NOT NULL DEFAULT 0,
        upserted_count INT NOT NULL DEFAULT 0,
        rejected_count INT NOT NULL DEFAULT 0,
        source_max_date DATE NULL,
        target_max_date DATE NULL,
        error_summary NVARCHAR(1000) NULL,
        CONSTRAINT UQ_ali1688_title_source_sync_run UNIQUE (run_id)
    );
END;
GO

IF USER_ID(N'app_writer') IS NOT NULL
BEGIN
    GRANT SELECT, INSERT, UPDATE, DELETE ON app.ali1688_title_hsds_root_word_snapshot TO [app_writer];
    GRANT SELECT, INSERT, UPDATE, DELETE ON app.ali1688_title_source_sync_run TO [app_writer];
END;
GO
