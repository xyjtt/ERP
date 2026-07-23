# Docs Index

这是 `furniture-uploader` 的当前有效文档总入口。

## 当前有效文档

### 1. 总览与当前状态

- [PROJECT_MEMORY.md](D:/script_files/ERP/furniture-uploader/docs/PROJECT_MEMORY.md)
  - 当前项目定位、验证结果、剩余 blocker、建议下一步
- [DIRECT_1688_PROGRESS.md](D:/script_files/ERP/furniture-uploader/docs/DIRECT_1688_PROGRESS.md)
  - 1688 主线进度、当前节点、下一阶段目标
- [PROJECT_EVOLUTION.md](D:/script_files/ERP/furniture-uploader/docs/PROJECT_EVOLUTION.md)
  - 项目演化记录和关键策略变化

### 2. 交付与交接

- [PROJECT_HANDOVER.md](D:/script_files/ERP/furniture-uploader/docs/PROJECT_HANDOVER.md)
  - 简版交接说明
- [DELIVERY_HANDOVER_2026-03-24.md](D:/script_files/ERP/furniture-uploader/docs/DELIVERY_HANDOVER_2026-03-24.md)
  - 完整交接文档，含经验、可复用代码、知识库和 AI 接手方式

### 3. 运行与上线

- [GO_LIVE_CHECKLIST.md](D:/script_files/ERP/furniture-uploader/docs/GO_LIVE_CHECKLIST.md)
  - 上线前检查项
- [LOCAL_SETUP.md](D:/script_files/ERP/furniture-uploader/docs/LOCAL_SETUP.md)
  - 本地运行说明
- [SELECTOR_PROBE_GUIDE.md](D:/script_files/ERP/furniture-uploader/docs/SELECTOR_PROBE_GUIDE.md)
  - 选择器采集指南
- [SELECTOR_CAPTURE_CHECKLIST.md](D:/script_files/ERP/furniture-uploader/docs/SELECTOR_CAPTURE_CHECKLIST.md)
  - 选择器采集清单
- [SQLSERVER_SETUP.md](D:/script_files/ERP/furniture-uploader/docs/SQLSERVER_SETUP.md)
  - SQL Server 配置说明

### 4. 经验与持续学习

- [PLATFORM_EXPERIENCE_KB.md](D:/script_files/ERP/furniture-uploader/docs/PLATFORM_EXPERIENCE_KB.md)
  - 平台经验知识库，记录 1688 可复用代码、踩坑和验证结论
- [AI_CONTINUITY_GUIDE.md](D:/script_files/ERP/furniture-uploader/docs/AI_CONTINUITY_GUIDE.md)
  - 如何让后续 AI 自动读取、更新并延续项目上下文
- [DOC_MAINTENANCE_POLICY.md](D:/script_files/ERP/furniture-uploader/docs/DOC_MAINTENANCE_POLICY.md)
  - 文档维护制度，规定什么时候必须更新哪份文档
- [DOC_UPDATE_TEMPLATES.md](D:/script_files/ERP/furniture-uploader/docs/DOC_UPDATE_TEMPLATES.md)
  - 文档更新模板，便于人和 AI 快速补充

### 5. 1688 SKU 下架相关入口

- [D:\script_files\1688\README.md](D:/script_files/1688/README.md)
  - 1688 项目统一入口
- [D:\script_files\1688\docs\requirements\1688_SKU_OFFLINE_REQUIREMENTS_V1.md](D:/script_files/1688/docs/requirements/1688_SKU_OFFLINE_REQUIREMENTS_V1.md)
  - 1688 SKU 下架需求说明
- [D:\script_files\ERP\furniture-uploader\rpa\sku_offline_main.py](D:/script_files/ERP/furniture-uploader/rpa/sku_offline_main.py)
  - 1688 SKU 下架执行入口

### 6. 1688 SKU 替换相关入口

- [1688_SKU_REPLACE_RUNBOOK.md](D:/script_files/ERP/furniture-uploader/docs/1688_SKU_REPLACE_RUNBOOK.md)
  - `全渠道替换` 的取数、1688 改码、聚水潭按链接同步和受控验收说明
- [config/systems/1688_sku_replace.json](D:/script_files/ERP/furniture-uploader/config/systems/1688_sku_replace.json)
  - 替换执行配置；继承下架系统的店铺/Profile/安全规则
- [scripts/run_1688_sku_replace_pipeline.py](D:/script_files/ERP/furniture-uploader/scripts/run_1688_sku_replace_pipeline.py)
  - 1688 SKU 替换 + 聚水潭手动同步商品完整流水线

## 建议阅读顺序

1. `PROJECT_MEMORY.md`
2. `DIRECT_1688_PROGRESS.md`
3. `DELIVERY_HANDOVER_2026-03-24.md`
4. `PLATFORM_EXPERIENCE_KB.md`
5. `AI_CONTINUITY_GUIDE.md`
6. `D:\script_files\1688\README.md`

## 历史档案

以下文件保留为历史资料，不作为当前唯一事实来源：

- `交接文档_2026-03-20.md`
- `文档分类索引_2026-03-20.md`
- `JUSHUITAN_SOLUTION.md`
- `JUSHUITAN_BATCH_UPDATE.md`
- `DIRECT_1688_MVP.md`

如果历史档案与当前有效文档冲突，以当前有效文档为准。
