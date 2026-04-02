# Platform Mediation Platform

`platform-mediation-platform` 是“平台上架下架任务与数据中台”的第一阶段代码骨架。

当前目标只覆盖 `1688 中台 v1`：

- 保留现有 `D:\script_files\ERP\furniture-uploader` 执行层
- 新建控制面骨架，用于承接任务中心、状态机、适配器注册、快照、重试和回流
- 第一阶段默认使用内存仓储，便于先把 API、服务边界和适配器契约跑通

## 当前范围

- `FastAPI` 控制面入口
- 任务中心骨架
- `1688 adapter v1` 包装接口
- 内存仓储与调度服务
- 第一阶段 API 契约实现
- SQL Server 建库草案同步副本

## 目录结构

```text
platform-mediation-platform/
  src/platform_mediation/
    api/                    FastAPI 路由
    application/services/   任务中心、调度、锁服务
    adapters/               平台适配器接口与 1688 包装
    domain/                 核心实体与状态枚举
    repositories/           仓储抽象与内存实现
    schemas/                API 请求/响应模型
    utils/                  公共工具
  scripts/                  本地运行脚本
  sql/                      建库 SQL
  tests/                    第一阶段骨架测试
```

## 快速开始

1. 创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. 启动本地服务：

```powershell
.\scripts\run_dev.ps1
```

3. 打开接口文档：

- `http://127.0.0.1:8000/docs`

## 环境变量

参考 `.env.example`：

- `PLATFORM_MEDIATION_ENV`
- `PLATFORM_MEDIATION_ADAPTER_MODE`
- `PLATFORM_MEDIATION_ENABLE_REAL_1688_EXECUTION`
- `PLATFORM_MEDIATION_FURNITURE_UPLOADER_ROOT`

默认情况下，`1688 adapter v1` 运行在 `mock` 模式，只返回标准化结果，不直接调用真实执行器。

## 当前说明

- `sql/001_init_schema.sql` 是从当前草案 schema 同步过来的第一阶段建库副本
- 第一阶段真实数据库仓储、调度器后台 worker 和回流同步器后续继续接入
- 真实 `1688` 执行链路后续通过 `Alibaba1688Adapter` 与 `furniture-uploader` 对接

