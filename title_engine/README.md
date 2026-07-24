# Title Engine - 1688电商标题生成引擎

## 概述

Title Engine 是一个用于1688电商平台的SEO优化标题生成引擎。它能够根据商品类目、核心关键词、商品属性和卖点，自动生成符合SEO规则的30字标题。

## 功能特性

- **标题生成**：基于关键词和属性自动生成SEO优化标题
- **标题优化**：分析现有标题并提供优化建议
- **关键词管理**：查询、筛选并按热度 40%、低竞争 30%、产品适配 30% 排序
- **SEO规则验证**：确保标题符合平台规则
- **文本分析**：中文分词、词根提取、重复检测
- **评分系统**：关键词候选综合分与标题结构质量分分层输出

## 目录结构

```
title_engine/
├── rpa/                      # 核心模块
│   ├── title_generator.py    # 标题生成引擎
│   ├── title_optimizer.py    # 标题优化引擎
│   ├── keyword_manager.py    # 关键词管理器
│   ├── seo_rules.py          # SEO规则引擎
│   ├── text_analyzer.py      # 文本分析器
│   └── score_calculator.py   # 评分计算器
├── config/                   # 配置模块
│   ├── settings.py           # 配置管理
│   └── seo_rules.json        # SEO规则配置
├── database/                 # 数据库模块
│   └── db_manager.py         # 数据库操作
├── api/                      # API模块
│   └── title_api.py          # Flask API接口
├── tests/                    # 测试模块
│   ├── test_title_generator.py
│   └── test_title_optimizer.py
├── requirements.txt          # 依赖清单
└── README.md                 # 说明文档
```

## 安装

```bash
# 安装依赖
pip install -r requirements.txt
```

## 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| TITLE_ENGINE_SQLSERVER_HOST |  | SQL Server 服务器，也可回退读取 `SQLSERVER_HOST` |
| TITLE_ENGINE_SQLSERVER_PORT | 1433 | SQL Server 端口，也可回退读取 `SQLSERVER_PORT` |
| TITLE_ENGINE_SQLSERVER_DATABASE | JSReportReplica | 正式数据库，也可回退读取 `SQLSERVER_DATABASE` |
| TITLE_ENGINE_SQLSERVER_SCHEMA | app | 正式 schema，也可回退读取 `SQLSERVER_SCHEMA` |
| TITLE_ENGINE_SQLSERVER_USERNAME |  | 数据库用户名，也可回退读取 `SQLSERVER_USERNAME` |
| TITLE_ENGINE_SQLSERVER_PASSWORD |  | 数据库密码，也可回退读取 `SQLSERVER_PASSWORD` |
| TITLE_ENGINE_SQLSERVER_DRIVER | SQL Server Native Client 10.0 | ODBC 驱动，也可回退读取 `SQLSERVER_DRIVER` |
| TITLE_ENGINE_SQLSERVER_QUERY_TIMEOUT | 45 | 单条正式词源 SQL 的超时秒数 |
| TITLE_ENGINE_SOURCE_CACHE_TTL_SECONDS | 300 | 成功读取的正式词源内存缓存秒数，设为 0 可关闭 |
| APP_DEBUG | False | 调试模式 |
| APP_HOST | 0.0.0.0 | API主机 |
| APP_PORT | 5000 | API端口 |

正式存储固定使用 `JSReportReplica/app`。应用不会在启动或请求中自动建表；上线前先由部署门禁执行并验收：

```text
sql/350_ddl_title_engine_app_2026-07-20.sql
sql/351_ddl_hsds_root_word_snapshot_2026-07-22.sql
```

默认复用 `D:\script_1688` 的 Windows Credential Manager 凭据引用
`YYDD/1688/database/app-writer`，以及 `YYDD_1688_CONFIG_ROOT` 指向的外置
`database.json`。环境变量仅作为开发兼容方式，用户名和密码必须成对提供。

```powershell
python scripts/apply_title_engine_ddl.py
python scripts/apply_title_engine_ddl.py --apply
```

## API接口

### 1. 生成标题

**POST** `/api/title/generate`

请求体：
```json
{
  "category": "家具",
  "core_keywords": ["实木", "沙发"],
  "attributes": {"材质": "实木", "风格": "现代"},
  "selling_points": ["舒适", "耐用"],
  "brand": "家居优选",
  "num_titles": 5
}
```

响应：
```json
{
  "success": true,
  "data": {
    "titles": [
      {
        "title": "实木沙发现代简约客厅家具舒适耐用家居优选",
        "score": 0.85,
        "core_word_used": "实木",
        "word_count": 12,
        "breakdown": {
          "length_score": 0.9,
          "distribution_score": 0.8,
          "repetition_score": 1.0,
          "quality_score": 0.75
        }
      }
    ],
    "count": 1
  }
}
```

### 2. 优化标题

**POST** `/api/title/optimize`

请求体：
```json
{
  "title": "这是我的沙发很好用的沙发",
  "category": "家具",
  "core_keywords": ["沙发"]
}
```

### 3. 诊断标题

**POST** `/api/title/diagnose`

请求体：
```json
{
  "title": "实木沙发现代简约客厅家具",
  "category": "家具"
}
```

响应包含长度、关键词、重复项、评分拆解和问题列表。该接口只做诊断，不修改线上商品标题。

### 4. 查询关键词

**GET** `/api/title/keywords?category=家具&limit=50`

响应按关键词综合分排序，包含 `keyword_score`、`score_status`、`score_breakdown` 和 `score_metadata`。三项指标在当前候选集内做 min-max 归一化；缺少搜索热度、竞争度或产品适配度时不生成总分并排在完整候选之后。`relevance` 只作为旧数据的产品适配度兼容回退。

注意：历史表早期字段可能用 `0` 代表未提供数据。正式启用自动排序前必须审计并清理这类旧值；新导入不会把空单元格转换为 `0`。

### 5. 实时词源预览

**POST** `/api/title/keywords/preview`

请求包含 `category、product_name、core_keywords、attributes、query、limit`。接口只读四张正式爬数快照表，返回来源角色、新鲜度、产品适配度和关键词综合分。下游市场及商机表当前是商品/信号证据，缺少可靠竞争指标时保持 `incomplete`，不伪装成完整关键词。

### 6. 批量生成

**POST** `/api/title/batch`

### 7. 历史记录

**GET** `/api/title/history?category=家具&limit=50`

## 使用示例

```python
from rpa.title_generator import TitleGenerator, ProductInfo

# 创建生成器
generator = TitleGenerator()

# 创建产品信息
product_info = ProductInfo(
    category="家具",
    core_keywords=["实木", "沙发"],
    attributes={"材质": "实木", "风格": "现代"},
    selling_points=["舒适", "耐用"],
    brand="家居优选",
)

# 生成标题
titles = generator.generate(product_info, num_titles=5)

# 输出结果
for title in titles:
    print(f"标题: {title.title}")
    print(f"评分: {title.score}")
    print(f"使用核心词: {title.core_word_used}")
    print("---")
```

## 人工关键词导入

首期关键词来源支持人工 CSV/XLSX，不等待新增爬虫。导入脚本默认只预览，不写库：

```bash
python scripts/import_keywords.py --file data/title_keywords.csv
python scripts/import_keywords.py --file data/title_keywords.xlsx --sheet Sheet1 --limit 20
```

真实写入必须显式增加 `--write-db`，并且目标表已由 `sql/350_ddl_title_engine_app_2026-07-20.sql` 预先部署：

```bash
python scripts/import_keywords.py --file data/title_keywords.csv --source manual --updated-by operator_name --write-db
```

支持的表头别名包括 `关键词/搜索词/word/keyword`、`类目/category`、`搜索人气`、`交易指数`、`竞争度`、`产品适配度`、`相关性`、`统计日期` 和 `来源记录键`。评分必需指标的空值会保留为空，不能用 `0` 代替缺失数据。

## SEO规则

1. **标题长度**：固定30个汉字
2. **核心词分布**：前10字包含核心词，第20-25字再次出现
3. **禁止重复词根**
4. **关键词候选分**：`0.40*热度分 + 0.30*低竞争分 + 0.30*产品适配分`

## 评分分层

关键词候选分用于选词，版本为 `keyword_score_40_30_30_v1`。HSDS 首版只作为独立候选和热度辅助证据，不进入该综合分。

标题结构质量分用于候选标题排序和人工评审，版本为 `title_structure_quality_v1`：

| 维度 | 权重 | 说明 |
|------|------|------|
| 长度评分 | 25% | 标题长度是否符合要求 |
| 分布评分 | 30% | 核心词位置是否合理 |
| 重复评分 | 25% | 是否存在重复词根 |
| 质量评分 | 20% | 整体质量和可读性 |

## 测试

```bash
# 运行所有测试
pytest tests/

# 运行特定测试
pytest tests/test_title_generator.py

# 生成覆盖率报告
pytest --cov=rpa tests/
```

当前基线：`85 passed`。

2026-07-22 正式只读词源验收：生意参谋搜索词 66 条（最新 2026-07-16）、市场商机商品 264 条（最新 2026-07-21）、市场商机信号 500 条（最新 2026-07-16）。HSDS 尚未迁入时明确返回 0 条和 `missing`，不伪造候选。真实柜类窄词池请求已返回 `30/30` 个唯一标题，且产品适配度低于 0.20 的“沙发、桌子、椅子”等词未进入标题。

只读运维命令：

```bash
python scripts/preview_keyword_sources.py --category 柜类 --core-keyword 床头柜 --limit 20
python scripts/audit_keyword_score_inputs.py --fail-on-ambiguous-zero
```

## 注意事项

1. 需要SQL Server数据库支持
2. 中文分词依赖jieba库
3. 生产环境请配置正确的数据库连接信息
4. 建议在虚拟环境中安装依赖
