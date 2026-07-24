# 标题引擎模块 - 系统架构设计文档

## 版本信息
- **版本**: v1.0
- **创建日期**: 2026-07-09
- **架构师**: 高见远（软件架构师）
- **项目**: title_engine
- **最近更新**: 2026-07-21

## 1. 系统概述

### 1.1 项目背景
标题引擎是1688电商运营自动化项目的核心模块之一，负责基于SEO规则自动生成和优化商品标题，提升商品搜索排名和曝光率。

### 1.2 设计目标
1. **SEO优化**: 生成符合1688 SEO规则的30字标题
2. **自动化**: 减少人工编写标题时间，提高运营效率
3. **数据驱动**: 基于生意参谋关键词数据，生成数据支撑的标题
4. **可扩展性**: 支持后续新增SEO规则和关键词数据源
5. **集成性**: 与现有furniture-uploader和聚水潭系统无缝集成

### 1.3 当前评分契约

评分分为两个独立层次，API、数据库字段、OK 页面和 Excel 导出不得共用一个含糊的“评分”标签：

1. 关键词候选综合分 `keyword_score_40_30_30_v1`：候选集内 min-max 归一化后，按 `0.40*search_heat + 0.30*inverse_competition + 0.30*product_fit` 计算。必需指标缺失时状态为 `incomplete`、总分为空并排在完整候选之后；旧 `relevance` 仅作为产品适配度兼容回退。
2. 标题结构质量分 `title_structure_quality_v1`：按长度 25%、核心词位置/分布 30%、重复 25%、质量/可读性 20% 计算。

HSDS 在首版只作为独立词根候选和热度辅助证据，不参与关键词综合分。`GET /api/health` 输出两个评分契约的版本和权重，`GET /api/title/keywords` 输出关键词总分、拆解、状态和评分元数据。

## 2. 系统架构

### 2.1 架构模式
采用**分层架构**（Layered Architecture），分为：
- **表示层**（Presentation Layer）: 前端页面和API接口
- **业务逻辑层**（Business Logic Layer）: 核心引擎和规则处理
- **数据访问层**（Data Access Layer）: 数据库操作和外部数据源
- **基础设施层**（Infrastructure Layer）: 配置管理和工具类

### 2.2 模块划分
```
title_engine/
├── rpa/                          # 核心业务逻辑层
│   ├── __init__.py              # 模块初始化
│   ├── title_generator.py       # 标题生成引擎
│   ├── title_optimizer.py       # 标题优化引擎
│   ├── keyword_manager.py       # 关键词管理器
│   ├── seo_rules.py             # SEO规则引擎
│   ├── text_analyzer.py         # 文本分析器
│   ├── score_calculator.py      # 评分计算器
│   ├── database.py              # 数据库操作
│   ├── config_loader.py         # 配置加载器
│   └── exceptions.py            # 异常定义
├── config/                       # 配置文件
│   ├── seo_rules.json           # SEO规则配置
│   ├── keyword_categories.json  # 关键词分类配置
│   ├── database.example.json    # 数据库配置模板
│   └── title_engine.json        # 引擎主配置
├── tests/                        # 单元测试
│   ├── test_title_generator.py
│   ├── test_title_optimizer.py
│   ├── test_keyword_manager.py
│   ├── test_seo_rules.py
│   └── test_database.py
├── scripts/                      # 工具脚本
│   ├── import_keywords.py       # 关键词导入脚本
│   ├── export_titles.py         # 标题导出脚本
│   └── seed_database.py         # 数据库初始化脚本
├── docs/                         # 文档
│   └── architecture.md          # 本架构文档
├── README.md                     # 项目说明
└── requirements.txt              # 依赖包
```

### 2.3 核心模块职责

#### 2.3.1 标题生成引擎（TitleGenerator）
**职责**: 基于SEO规则和关键词数据自动生成商品标题

**核心功能**:
1. 关键词查询和排序
2. 标题结构设计（前10字、20-25字核心词分布）
3. 候选标题生成
4. 标题评分和筛选

**输入**:
- 商品类目（category）
- 核心关键词列表（keywords）
- 商品属性字典（attributes）
- 商品卖点列表（selling_points）

**输出**:
- 候选标题列表（3-5个）
- 每个标题的SEO评分
- 关键词分布说明

#### 2.3.2 标题优化引擎（TitleOptimizer）
**职责**: 分析现有标题并给出优化建议

**核心功能**:
1. 标题长度分析
2. 关键词分布检查
3. 重复词根检测
4. 优化建议生成
5. 优化标题生成

**输入**:
- 现有标题（title）
- 商品类目（category）
- 目标关键词列表（target_keywords，可选）

**输出**:
- 原始标题
- 优化后的标题
- 评分（0-100分）
- 问题列表
- 优化建议

#### 2.3.3 关键词管理器（KeywordManager）
**职责**: 管理1688关键词数据

**核心功能**:
1. 关键词数据导入
2. 关键词查询和排序
3. 关键词分类管理
4. 关键词效果追踪

**数据结构**:
```python
@dataclass
class KeywordData:
    keyword: str              # 关键词
    category: str             # 所属类目
    search_popularity: int    # 搜索人气
    transaction_index: int    # 交易指数
    competition: float        # 竞争度
    source: str               # 数据来源
    updated_at: datetime      # 更新时间
```

#### 2.3.4 SEO规则引擎（SEORules）
**职责**: 管理和应用SEO规则

**核心规则**:
1. **标题长度**: 固定30个汉字
2. **核心方向词分布**:
   - 前10个汉字必须包含核心方向词
   - 第20-25个汉字必须再次出现核心方向词
3. **禁止重复词根**: 同一标题内不能出现重复词根
4. **关键词候选分**: 热度 40% + 低竞争 30% + 产品适配 30%；竞争度反向归一化，缺必需指标时不生成总分

**配置格式**:
```json
{
  "title_length": 30,
  "core_word_positions": {
    "first_position": {"start": 1, "end": 10},
    "second_position": {"start": 20, "end": 25}
  },
  "forbidden_patterns": [],
  "priority_weights": {
    "search_heat": 0.4,
    "inverse_competition": 0.3,
    "product_fit": 0.3
  }
}
```

#### 2.3.5 文本分析器（TextAnalyzer）
**职责**: 分析和处理中文文本

**核心功能**:
1. 中文分词
2. 词根提取
3. 重复词根检测
4. 文本长度计算
5. 关键词位置分析

**依赖**:
- `jieba` 中文分词库

#### 2.3.6 评分计算器（ScoreCalculator）
**职责**: 计算标题的SEO评分

**评分维度**:
1. **长度合规性**（30分）: 是否符合30字规则
2. **关键词分布**（40分）: 核心词位置是否正确
3. **重复词根**（15分）: 是否有重复词根
4. **关键词质量**（15分）: 使用的关键词搜索人气、交易指数

**评分公式**:
```
总分 = 长度合规性得分 + 关键词分布得分 + 重复词根得分 + 关键词质量得分
```

## 3. 数据库设计

### 3.1 数据库表结构

#### 3.1.1 关键词表（keywords）
```sql
CREATE TABLE dbo.keywords (
    id INT IDENTITY(1,1) PRIMARY KEY,
    keyword NVARCHAR(100) NOT NULL,
    category NVARCHAR(100) NOT NULL,
    search_popularity INT DEFAULT 0,
    transaction_index INT DEFAULT 0,
    competition FLOAT DEFAULT 0.0,
    source NVARCHAR(50) DEFAULT 'manual',
    created_at DATETIME DEFAULT GETDATE(),
    updated_at DATETIME DEFAULT GETDATE(),
    CONSTRAINT UQ_keyword_category UNIQUE (keyword, category)
);

CREATE INDEX IX_keywords_category ON dbo.keywords(category);
CREATE INDEX IX_keywords_search_popularity ON dbo.keywords(search_popularity DESC);
CREATE INDEX IX_keywords_transaction_index ON dbo.keywords(transaction_index DESC);
```

#### 3.1.2 标题历史表（title_history）
```sql
CREATE TABLE dbo.title_history (
    id INT IDENTITY(1,1) PRIMARY KEY,
    original_title NVARCHAR(100),
    generated_title NVARCHAR(100) NOT NULL,
    category NVARCHAR(100),
    score INT DEFAULT 0,
    keywords_used NVARCHAR(MAX),  -- JSON格式存储使用的关键词
    seo_compliance BIT DEFAULT 0,
    created_at DATETIME DEFAULT GETDATE(),
    user_id NVARCHAR(50),
    status NVARCHAR(20) DEFAULT 'generated'
);

CREATE INDEX IX_title_history_category ON dbo.title_history(category);
CREATE INDEX IX_title_history_created_at ON dbo.title_history(created_at DESC);
```

#### 3.1.3 SEO规则表（seo_rules）
```sql
CREATE TABLE dbo.seo_rules (
    id INT IDENTITY(1,1) PRIMARY KEY,
    rule_name NVARCHAR(100) NOT NULL,
    rule_config NVARCHAR(MAX) NOT NULL,  -- JSON格式存储规则配置
    is_active BIT DEFAULT 1,
    priority INT DEFAULT 0,
    created_at DATETIME DEFAULT GETDATE(),
    updated_at DATETIME DEFAULT GETDATE()
);
```

#### 3.1.4 关键词分类表（keyword_categories）
```sql
CREATE TABLE dbo.keyword_categories (
    id INT IDENTITY(1,1) PRIMARY KEY,
    category_name NVARCHAR(100) NOT NULL UNIQUE,
    parent_category_id INT,
    description NVARCHAR(500),
    is_active BIT DEFAULT 1,
    created_at DATETIME DEFAULT GETDATE()
);
```

### 3.2 数据关系
```
keyword_categories (1) ──< (N) keywords
title_history (N) >── (1) keywords (通过keywords_used JSON字段关联)
seo_rules (1) ──< (N) title_history (通过规则应用关联)
```

## 4. API接口设计

### 4.1 标题生成接口
```python
# 接口: POST /api/title/generate
# 描述: 生成SEO优化标题
# 请求体:
{
    "category": "家具",
    "keywords": ["沙发", "客厅", "实木"],
    "attributes": {
        "material": "实木",
        "color": "原木色",
        "size": "2.5米"
    },
    "selling_points": ["北欧风格", "可定制", "环保材质"],
    "count": 3  # 生成标题数量
}

# 响应体:
{
    "success": true,
    "data": {
        "titles": [
            {
                "title": "北欧风格实木沙发客厅家具2.5米可定制环保材质原木色",
                "score": 92,
                "analysis": {
                    "length": 30,
                    "core_word_positions": [1, 22],
                    "keywords_used": ["沙发", "实木", "北欧风格"],
                    "compliance": true
                }
            }
        ],
        "metadata": {
            "rule_compliance": true,
            "keyword_coverage": 0.85,
            "generation_time": 0.23
        }
    }
}
```

### 4.2 标题优化接口
```python
# 接口: POST /api/title/optimize
# 描述: 优化现有标题
# 请求体:
{
    "title": "实木沙发北欧风格客厅家具2.5米",
    "category": "家具",
    "target_keywords": ["沙发", "客厅", "实木"]
}

# 响应体:
{
    "success": true,
    "data": {
        "original_title": "实木沙发北欧风格客厅家具2.5米",
        "optimized_title": "北欧风格实木沙发客厅家具2.5米可定制环保材质原木色",
        "score": 92,
        "issues": [
            "标题长度不足30字",
            "核心关键词分布不符合SEO规则"
        ],
        "suggestions": [
            "在标题前10字添加核心关键词",
            "在第20-25字位置再次出现核心词",
            "补充商品卖点词"
        ]
    }
}
```

### 4.3 关键词查询接口
```python
# 接口: GET /api/keywords
# 描述: 查询关键词数据
# 查询参数:
#   category: 商品类目
#   keyword_type: 关键词类型（all/search/transaction/competition）
#   limit: 返回数量限制
#   sort_by: 排序字段（search_popularity/transaction_index/competition）

# 响应体:
{
    "success": true,
    "data": {
        "keywords": [
            {
                "keyword": "沙发",
                "category": "家具",
                "search_popularity": 125000,
                "transaction_index": 89000,
                "competition": 0.75
            }
        ],
        "total": 150,
        "page": 1,
        "page_size": 50
    }
}
```

### 4.4 批量生成接口
```python
# 接口: POST /api/title/batch-generate
# 描述: 批量生成标题
# 请求体:
{
    "products": [
        {
            "id": "product_001",
            "category": "家具",
            "keywords": ["沙发", "客厅"],
            "attributes": {"material": "实木"},
            "selling_points": ["北欧风格"]
        }
    ]
}

# 响应体:
{
    "success": true,
    "data": {
        "results": [
            {
                "product_id": "product_001",
                "titles": [
                    {
                        "title": "北欧风格实木沙发客厅家具",
                        "score": 88
                    }
                ]
            }
        ],
        "progress": {
            "current": 1,
            "total": 1
        },
        "summary": {
            "success": 1,
            "failed": 0
        }
    }
}
```

## 5. 程序调用流程

### 5.1 标题生成流程
```
用户输入 → 前端页面 → API接口 → TitleGenerator
    ↓
KeywordManager.query_keywords()
    ↓
SEORules.validate_title_structure()
    ↓
TextAnalyzer.analyze_keywords()
    ↓
ScoreCalculator.calculate_score()
    ↓
返回候选标题列表
```

### 5.2 标题优化流程
```
用户输入现有标题 → 前端页面 → API接口 → TitleOptimizer
    ↓
TextAnalyzer.analyze_title()
    ↓
SEORules.check_compliance()
    ↓
ScoreCalculator.calculate_score()
    ↓
生成优化建议
    ↓
TitleGenerator.generate_optimized_title()
    ↓
返回优化结果
```

### 5.3 关键词数据流程
```
关键词数据导入 → KeywordManager.import_keywords()
    ↓
数据清洗和验证
    ↓
SQL Server数据库
    ↓
标题生成/优化时查询使用
```

## 6. 技术选型

### 6.1 核心依赖
```txt
# 数据库
pyodbc>=4.0.30          # SQL Server数据库连接

# 文本处理
jieba>=0.42.1           # 中文分词
pypinyin>=0.48.0        # 拼音转换（可选）

# 数据处理
pandas>=1.3.0           # 数据处理（用于批量导入）

# 工具库
python-dateutil>=2.8.0  # 日期处理
json5>=0.9.0            # JSON5格式支持（配置文件）

# Web框架（用于API接口）
flask>=2.0.0            # 轻量级Web框架
flask-cors>=3.0.0       # 跨域支持

# 测试
pytest>=6.0.0           # 单元测试框架
pytest-cov>=2.12.0      # 测试覆盖率
```

### 6.2 开发工具
```txt
# 代码质量
black>=21.0.0           # 代码格式化
flake8>=3.9.0           # 代码检查
mypy>=0.900             # 类型检查

# 文档
sphinx>=4.0.0           # 文档生成
```

## 7. 集成设计

### 7.1 与furniture-uploader集成
**集成方式**: API调用
**集成点**:
- 标题生成完成后，可直接调用上架模块的API
- 上架模块可通过API获取生成的标题

**数据流**:
```
标题引擎生成标题 → API接口 → furniture-uploader使用标题 → 商品发布到1688
```

### 7.2 与聚水潭系统集成
**集成方式**: 数据同步
**集成点**:
- 从聚水潭同步商品类目和属性数据
- 生成的标题可批量应用到聚水潭商品

**数据流**:
```
聚水潭商品数据 → 数据同步 → 标题引擎使用数据 → 标题生成 → 应用到聚水潭
```

## 8. 配置管理

### 8.1 配置文件结构
```
config/
├── seo_rules.json           # SEO规则配置
├── keyword_categories.json  # 关键词分类配置
├── database.example.json    # 数据库配置模板
├── database.local.json      # 本地数据库配置
├── title_engine.json        # 引擎主配置
└── title_engine.local.json  # 本地引擎配置
```

### 8.2 配置加载机制
采用与furniture-uploader相同的配置加载机制：
1. 加载基础配置（`*.json`）
2. 应用本地覆盖（`*.local.json`）
3. 支持环境变量替换

## 9. 错误处理

### 9.1 异常类型
```python
# 自定义异常类
class TitleEngineError(Exception):
    """标题引擎基础异常"""
    pass

class KeywordNotFoundError(TitleEngineError):
    """关键词未找到异常"""
    pass

class SEOComplianceError(TitleEngineError):
    """SEO规则不合规异常"""
    pass

class DatabaseConnectionError(TitleEngineError):
    """数据库连接异常"""
    pass

class ValidationError(TitleEngineError):
    """数据验证异常"""
    pass
```

### 9.2 错误处理策略
1. **数据库异常**: 自动重试3次，失败后记录日志并返回友好错误信息
2. **关键词查询失败**: 返回默认关键词列表，继续生成流程
3. **SEO规则违规**: 记录违规详情，返回优化建议
4. **输入验证失败**: 返回详细错误信息，指导用户修正

## 10. 性能优化

### 10.1 缓存策略
1. **关键词数据缓存**: 使用内存缓存（LRU Cache）缓存热门关键词查询
2. **SEO规则缓存**: 缓存解析后的规则配置
3. **标题模板缓存**: 缓存常用的标题模板

### 10.2 批量处理
1. **批量查询**: 一次性查询多个关键词的数据
2. **批量生成**: 支持批量生成多个商品的标题
3. **异步处理**: 批量生成时支持异步处理，提高响应速度

### 10.3 数据库优化
1. **索引优化**: 为常用查询字段创建索引
2. **查询优化**: 使用参数化查询，避免SQL注入
3. **连接池**: 使用数据库连接池，减少连接开销

## 11. 监控和日志

### 11.1 日志记录
```python
# 日志格式
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# 日志级别
# DEBUG: 调试信息
# INFO: 一般信息
# WARNING: 警告信息
# ERROR: 错误信息
# CRITICAL: 严重错误
```

### 11.2 监控指标
1. **性能指标**:
   - 标题生成时间
   - 批量生成速度
   - API响应时间

2. **业务指标**:
   - 标题生成成功率
   - SEO规则符合率
   - 关键词查询次数

3. **系统指标**:
   - 数据库连接数
   - 内存使用情况
   - CPU使用率

## 12. 安全设计

### 12.1 数据安全
1. **数据库密码**: 使用环境变量存储，不硬编码
2. **敏感信息**: 日志中不记录敏感信息
3. **输入验证**: 所有输入进行严格的验证和清洗

### 12.2 API安全
1. **跨域控制**: 使用flask-cors控制跨域访问
2. **请求限流**: 对API接口进行请求限流
3. **参数验证**: 使用marshmallow进行请求参数验证

## 13. 部署设计

### 13.1 部署方式
1. **本地部署**: 与现有项目一起本地部署
2. **API服务**: 作为Flask API服务运行
3. **集成部署**: 与furniture-uploader一起部署

### 13.2 部署步骤
1. 安装依赖包
2. 配置数据库连接
3. 初始化数据库表
4. 导入关键词数据
5. 启动API服务
6. 验证功能正常

## 14. 测试策略

### 14.1 测试层次
1. **单元测试**: 测试单个函数和类
2. **集成测试**: 测试模块间的交互
3. **API测试**: 测试API接口的正确性
4. **性能测试**: 测试系统性能

### 14.2 测试覆盖率
- **目标覆盖率**: > 80%
- **核心模块覆盖率**: > 90%

### 14.3 测试工具
- **pytest**: 单元测试框架
- **pytest-cov**: 测试覆盖率
- **pytest-mock**: Mock对象
- **requests**: API测试

## 15. 文档设计

### 15.1 文档清单
1. **架构设计文档**: 本文档
2. **API接口文档**: 详细的API接口说明
3. **用户操作手册**: 用户使用指南
4. **开发文档**: 开发者指南和代码规范
5. **部署文档**: 部署和运维指南

### 15.2 文档维护
1. 代码变更时同步更新文档
2. 定期审查和更新文档
3. 使用版本控制管理文档

## 16. 风险评估

### 16.1 技术风险
1. **中文分词准确性**: jieba分词可能不准确
   - 缓解措施: 使用自定义词典，定期更新

2. **SEO规则变化**: 1688 SEO规则可能调整
   - 缓解措施: 规则配置化，支持快速更新

3. **关键词数据获取**: 生意参谋数据获取可能受限
   - 缓解措施: 使用代理IP，控制请求频率，数据缓存

### 16.2 业务风险
1. **标题质量评估**: 自动评估可能不准确
   - 缓解措施: 结合人工审核，A/B测试验证

2. **用户接受度**: 用户可能不接受自动生成的标题
   - 缓解措施: 提供多个候选标题，支持人工调整

### 16.3 项目风险
1. **进度延迟**: 开发进度可能延迟
   - 缓解措施: 分阶段开发，优先实现核心功能

2. **人员变动**: 开发人员可能变动
   - 缓解措施: 完善文档，知识共享

## 17. 扩展性设计

### 17.1 规则扩展
1. 支持新增SEO规则
2. 支持自定义规则模板
3. 支持规则优先级配置

### 17.2 数据源扩展
1. 支持多个关键词数据源
2. 支持自定义数据导入格式
3. 支持数据源优先级配置

### 17.3 功能扩展
1. 支持竞品标题分析
2. 支持标题A/B测试
3. 支持标题效果追踪

## 18. 总结

本架构设计文档详细描述了标题引擎模块的系统设计，包括：
1. **分层架构**: 清晰的层次划分，便于维护和扩展
2. **模块化设计**: 独立的模块职责，便于测试和复用
3. **数据库设计**: 合理的表结构，支持高效查询
4. **API接口**: RESTful API设计，便于集成
5. **配置管理**: 灵活的配置机制，支持快速调整
6. **错误处理**: 完善的异常处理机制，提高系统稳定性
7. **性能优化**: 多层次的性能优化策略
8. **测试策略**: 全面的测试覆盖，保证代码质量

通过本架构的实施，标题引擎将成为1688电商运营的重要工具，显著提升运营效率和商品曝光率，为业务增长提供有力支持。

---

**文档状态**: 已完成
**下一步**: 开发工程师根据本架构设计进行详细设计和编码实现
