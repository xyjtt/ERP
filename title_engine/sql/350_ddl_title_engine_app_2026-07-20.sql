USE [JSReportReplica];
GO

SET ANSI_NULLS ON;
GO
SET QUOTED_IDENTIFIER ON;
GO

IF SCHEMA_ID(N'app') IS NULL
BEGIN
    EXEC(N'CREATE SCHEMA [app]');
END;
GO

IF OBJECT_ID(N'app.ali1688_title_keyword', N'U') IS NULL
BEGIN
    CREATE TABLE [app].[ali1688_title_keyword] (
        [id] BIGINT IDENTITY(1,1) NOT NULL,
        [keyword] NVARCHAR(100) NOT NULL,
        [category] NVARCHAR(100) NOT NULL,
        [category_alias] NVARCHAR(100) NULL,
        [source] NVARCHAR(50) NOT NULL CONSTRAINT [DF_ali1688_title_keyword_source] DEFAULT (N'manual'),
        [search_popularity] DECIMAL(18,4) NULL,
        [transaction_index] DECIMAL(18,4) NOT NULL CONSTRAINT [DF_ali1688_title_keyword_transaction_index] DEFAULT ((0)),
        [competition] DECIMAL(18,4) NULL,
        [relevance] DECIMAL(18,4) NULL,
        [product_fit] DECIMAL(18,4) NULL,
        [source_stat_date] DATE NULL,
        [source_record_key] NVARCHAR(500) NULL,
        [raw_payload] NVARCHAR(MAX) NULL,
        [created_by] NVARCHAR(100) NULL,
        [created_at] DATETIME2(0) NOT NULL CONSTRAINT [DF_ali1688_title_keyword_created_at] DEFAULT (SYSUTCDATETIME()),
        [updated_at] DATETIME2(0) NOT NULL CONSTRAINT [DF_ali1688_title_keyword_updated_at] DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT [PK_ali1688_title_keyword] PRIMARY KEY CLUSTERED ([id] ASC),
        CONSTRAINT [UQ_ali1688_title_keyword_category_word_source] UNIQUE ([category], [keyword], [source])
    );
END;
GO

ALTER TABLE [app].[ali1688_title_keyword] ALTER COLUMN [search_popularity] DECIMAL(18,4) NULL;
ALTER TABLE [app].[ali1688_title_keyword] ALTER COLUMN [competition] DECIMAL(18,4) NULL;
ALTER TABLE [app].[ali1688_title_keyword] ALTER COLUMN [relevance] DECIMAL(18,4) NULL;
GO

IF COL_LENGTH(N'app.ali1688_title_keyword', N'product_fit') IS NULL
BEGIN
    ALTER TABLE [app].[ali1688_title_keyword] ADD [product_fit] DECIMAL(18,4) NULL;
END;
GO

IF COL_LENGTH(N'app.ali1688_title_keyword', N'source_stat_date') IS NULL
BEGIN
    ALTER TABLE [app].[ali1688_title_keyword] ADD [source_stat_date] DATE NULL;
END;
GO

IF COL_LENGTH(N'app.ali1688_title_keyword', N'source_record_key') IS NULL
BEGIN
    ALTER TABLE [app].[ali1688_title_keyword] ADD [source_record_key] NVARCHAR(500) NULL;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = N'IX_ali1688_title_keyword_category_score'
      AND object_id = OBJECT_ID(N'app.ali1688_title_keyword', N'U')
)
BEGIN
    CREATE NONCLUSTERED INDEX [IX_ali1688_title_keyword_category_score]
    ON [app].[ali1688_title_keyword] ([category], [search_popularity] DESC, [transaction_index] DESC)
    INCLUDE ([keyword], [competition], [relevance], [product_fit], [source], [source_stat_date], [updated_at]);
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = N'IX_ali1688_title_keyword_word'
      AND object_id = OBJECT_ID(N'app.ali1688_title_keyword', N'U')
)
BEGIN
    CREATE NONCLUSTERED INDEX [IX_ali1688_title_keyword_word]
    ON [app].[ali1688_title_keyword] ([keyword], [category])
    INCLUDE ([search_popularity], [transaction_index], [competition], [relevance], [product_fit], [source], [source_stat_date]);
END;
GO

IF OBJECT_ID(N'app.ali1688_title_generation_history', N'U') IS NULL
BEGIN
    CREATE TABLE [app].[ali1688_title_generation_history] (
        [id] BIGINT IDENTITY(1,1) NOT NULL,
        [request_id] UNIQUEIDENTIFIER NOT NULL CONSTRAINT [DF_ali1688_title_generation_history_request_id] DEFAULT (NEWID()),
        [batch_id] NVARCHAR(64) NULL,
        [shop_name] NVARCHAR(100) NULL,
        [product_id] NVARCHAR(100) NULL,
        [sku_code] NVARCHAR(100) NULL,
        [category] NVARCHAR(100) NOT NULL,
        [original_title] NVARCHAR(500) NULL,
        [generated_title] NVARCHAR(500) NOT NULL,
        [score] DECIMAL(10,4) NOT NULL CONSTRAINT [DF_ali1688_title_generation_history_score] DEFAULT ((0)),
        [score_breakdown] NVARCHAR(MAX) NULL,
        [core_keywords] NVARCHAR(MAX) NULL,
        [attributes_snapshot] NVARCHAR(MAX) NULL,
        [selling_points_snapshot] NVARCHAR(MAX) NULL,
        [selected] BIT NOT NULL CONSTRAINT [DF_ali1688_title_generation_history_selected] DEFAULT ((0)),
        [generated_by] NVARCHAR(100) NOT NULL CONSTRAINT [DF_ali1688_title_generation_history_generated_by] DEFAULT (N'system'),
        [created_at] DATETIME2(0) NOT NULL CONSTRAINT [DF_ali1688_title_generation_history_created_at] DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT [PK_ali1688_title_generation_history] PRIMARY KEY CLUSTERED ([id] ASC)
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = N'IX_ali1688_title_generation_history_batch'
      AND object_id = OBJECT_ID(N'app.ali1688_title_generation_history', N'U')
)
BEGIN
    CREATE NONCLUSTERED INDEX [IX_ali1688_title_generation_history_batch]
    ON [app].[ali1688_title_generation_history] ([batch_id], [created_at] DESC)
    INCLUDE ([product_id], [sku_code], [category], [generated_title], [score], [selected]);
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = N'IX_ali1688_title_generation_history_product'
      AND object_id = OBJECT_ID(N'app.ali1688_title_generation_history', N'U')
)
BEGIN
    CREATE NONCLUSTERED INDEX [IX_ali1688_title_generation_history_product]
    ON [app].[ali1688_title_generation_history] ([shop_name], [product_id], [created_at] DESC)
    INCLUDE ([sku_code], [category], [generated_title], [score], [selected]);
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.extended_properties
    WHERE major_id = OBJECT_ID(N'app.ali1688_title_keyword', N'U')
      AND minor_id = 0
      AND name = N'MS_Description'
)
BEGIN
    EXEC sys.sp_addextendedproperty
        @name = N'MS_Description',
        @value = N'1688 标题助手关键词库，支持人工 CSV/Excel 导入和后续业务数据补充。',
        @level0type = N'SCHEMA', @level0name = N'app',
        @level1type = N'TABLE', @level1name = N'ali1688_title_keyword';
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.extended_properties
    WHERE major_id = OBJECT_ID(N'app.ali1688_title_generation_history', N'U')
      AND minor_id = 0
      AND name = N'MS_Description'
)
BEGIN
    EXEC sys.sp_addextendedproperty
        @name = N'MS_Description',
        @value = N'1688 标题助手生成历史和人工选择留痕，不代表自动修改线上商品标题。',
        @level0type = N'SCHEMA', @level0name = N'app',
        @level1type = N'TABLE', @level1name = N'ali1688_title_generation_history';
END;
GO

IF USER_ID(N'app_writer') IS NOT NULL
BEGIN
    GRANT SELECT, INSERT, UPDATE, DELETE ON [app].[ali1688_title_keyword] TO [app_writer];
    GRANT SELECT, INSERT, UPDATE, DELETE ON [app].[ali1688_title_generation_history] TO [app_writer];
END;
GO
