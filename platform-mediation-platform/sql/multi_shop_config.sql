-- 分组上架配置表（分组级别默认配置）
CREATE TABLE [dbo].[group_publish_config] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [group_id] INT NOT NULL,
    [group_name] NVARCHAR(100) NOT NULL,
    [platform] NVARCHAR(50) NOT NULL DEFAULT '1688',
    [ship_from_template] NVARCHAR(500) NULL,
    [freight_template] NVARCHAR(200) NULL,
    [ship_time_template] NVARCHAR(200) NULL,
    [buyer_protection_ship_time] NVARCHAR(100) DEFAULT '15天发货',
    [default_quantity] INT DEFAULT 999,
    [default_brand] NVARCHAR(100) NULL,
    [default_material] NVARCHAR(100) NULL,
    [default_style] NVARCHAR(100) NULL,
    [default_space] NVARCHAR(100) NULL,
    [delivery_service_ids] NVARCHAR(MAX) NULL,
    [status] INT DEFAULT 1,
    [created_at] DATETIME DEFAULT GETDATE(),
    [updated_at] DATETIME DEFAULT GETDATE()
);

CREATE UNIQUE INDEX [UX_group_publish_config_group_platform] 
ON [dbo].[group_publish_config]([group_id], [platform]);

CREATE INDEX [IX_group_publish_config_platform] 
ON [dbo].[group_publish_config]([platform]);

-- 店铺上架配置表（店铺级别配置，可覆盖分组配置）
CREATE TABLE [dbo].[shop_publish_config] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [shop_id] INT NOT NULL,
    [shop_name] NVARCHAR(200) NOT NULL,
    [group_id] INT NULL,
    [platform] NVARCHAR(50) NOT NULL DEFAULT '1688',
    [ship_from_template] NVARCHAR(500) NULL,
    [freight_template] NVARCHAR(200) NULL,
    [ship_time_template] NVARCHAR(200) NULL,
    [buyer_protection_ship_time] NVARCHAR(100) DEFAULT '15天发货',
    [default_quantity] INT DEFAULT 999,
    [default_brand] NVARCHAR(100) NULL,
    [default_material] NVARCHAR(100) NULL,
    [default_style] NVARCHAR(100) NULL,
    [default_space] NVARCHAR(100) NULL,
    [delivery_service_ids] NVARCHAR(MAX) NULL,
    [inherit_from_group] BIT DEFAULT 1,
    [status] INT DEFAULT 1,
    [created_at] DATETIME DEFAULT GETDATE(),
    [updated_at] DATETIME DEFAULT GETDATE()
);

CREATE UNIQUE INDEX [UX_shop_publish_config_shop_platform] 
ON [dbo].[shop_publish_config]([shop_id], [platform]);

CREATE INDEX [IX_shop_publish_config_platform] 
ON [dbo].[shop_publish_config]([platform]);

CREATE INDEX [IX_shop_publish_config_group] 
ON [dbo].[shop_publish_config]([group_id]);

-- 类目属性配置表（所有店铺共享）
CREATE TABLE [dbo].[category_attribute_config] (
    [id] INT IDENTITY(1,1) PRIMARY KEY,
    [platform] NVARCHAR(50) NOT NULL,
    [category_code] NVARCHAR(100) NOT NULL,
    [category_name] NVARCHAR(255) NOT NULL,
    [attribute_name] NVARCHAR(100) NOT NULL,
    [attribute_type] NVARCHAR(50) NOT NULL,
    [required] BIT DEFAULT 0,
    [default_value] NVARCHAR(500) NULL,
    [value_options] NVARCHAR(MAX) NULL,
    [source_field] NVARCHAR(100) NULL,
    [sort_order] INT DEFAULT 0,
    [status] INT DEFAULT 1,
    [created_at] DATETIME DEFAULT GETDATE(),
    [updated_at] DATETIME DEFAULT GETDATE()
);

CREATE UNIQUE INDEX [UX_category_attribute_config_platform_category_attr] 
ON [dbo].[category_attribute_config]([platform], [category_code], [attribute_name]);

CREATE INDEX [IX_category_attribute_config_platform] 
ON [dbo].[category_attribute_config]([platform]);

CREATE INDEX [IX_category_attribute_config_category] 
ON [dbo].[category_attribute_config]([category_code]);

-- 插入分组默认配置（根据实际分组）
INSERT INTO [dbo].[group_publish_config] 
([group_id], [group_name], [platform], [ship_from_template], [freight_template], [ship_time_template], [default_brand])
VALUES
(77081, '山东项目组', '1688', '山东省济南市历下区XX路XX号', '山东模板', '48小时发货', '速班达'),
(77079, '运营中心G', '1688', '江苏省常州市武进区横洛路219号', '日常模板', '24小时发货', '速班达'),
(77078, '运营中心B', '1688', '江苏省常州市武进区横洛路219号', '日常模板', '24小时发货', '速班达'),
(77077, '运营中心C', '1688', '江苏省常州市武进区横洛路219号', '日常模板', '24小时发货', '速班达'),
(0, '分销', '1688', '江苏省常州市武进区横洛路219号', '分销模板', '72小时发货', '速班达');

-- 插入床头柜类目默认属性配置
INSERT INTO [dbo].[category_attribute_config] 
([platform], [category_code], [category_name], [attribute_name], [attribute_type], [required], [default_value], [value_options], [source_field], [sort_order])
VALUES
('1688', 'bedside_table', '床头柜', '风格', 'select', 1, '现代简约', '["现代简约","北欧","中式","欧式","美式"]', NULL, 1),
('1688', 'bedside_table', '床头柜', '品牌', 'input', 1, '速班达', NULL, 'brand', 2),
('1688', 'bedside_table', '床头柜', '材质', 'select', 1, '人造板', '["人造板","实木","金属","玻璃"]', 'material', 3),
('1688', 'bedside_table', '床头柜', '台面材质', 'select', 0, '人造板', '["人造板","实木","金属"]', 'material', 4),
('1688', 'bedside_table', '床头柜', '空间', 'select', 1, '卧室', '["卧室","客厅","书房","办公"]', NULL, 5),
('1688', 'bedside_table', '床头柜', '适用场所', 'select', 1, '卧室', '["卧室","客厅","书房","办公"]', NULL, 6),
('1688', 'bedside_table', '床头柜', '适用对象', 'multiselect', 1, '成人', '["成人","儿童","老人"]', NULL, 7),
('1688', 'bedside_table', '床头柜', '功能', 'multiselect', 1, '收纳储物', '["收纳储物","展示","装饰"]', NULL, 8),
('1688', 'bedside_table', '床头柜', '是否配送上门', 'select', 1, '送货至楼下', '["送货至楼下","送货入户","不配送"]', NULL, 9),
('1688', 'bedside_table', '床头柜', '是否提供安装服务', 'select', 1, '否', '["是","否"]', NULL, 10),
('1688', 'bedside_table', '床头柜', '是否组装', 'select', 1, '组装', '["组装","整装"]', NULL, 11),
('1688', 'bedside_table', '床头柜', '是否带滚轮', 'select', 0, '否', '["是","否"]', NULL, 12),
('1688', 'bedside_table', '床头柜', '是否有3D模型', 'select', 0, '否', '["是","否"]', NULL, 13),
('1688', 'bedside_table', '床头柜', '是否一般纳税人', 'select', 0, '否', '["是","否"]', NULL, 14),
('1688', 'bedside_table', '床头柜', '是否有专利', 'select', 0, '否', '["是","否"]', NULL, 15),
('1688', 'bedside_table', '床头柜', '设计元素', 'select', 0, '其它', '["简约","复古","现代","其它"]', NULL, 16);