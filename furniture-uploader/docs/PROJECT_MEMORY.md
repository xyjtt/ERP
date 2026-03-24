# Project Memory

更新时间：2026-03-24

## 项目定位

- 项目：`furniture-uploader`
- 当前业务目标：实现电商后台自动上架的最小可落地版本
- 当前执行入口：`1688_direct`
- 当前首个平台：`1688`
- 当前执行引擎：`Python + Selenium`
- 当前默认发布策略：人工复核优先，代码层已经支持 `manual / draft / submit`

## 当前已验证事实

- Python 依赖已安装
- 单元测试通过
- `doctor` 通过
- 已支持连接本机已登录的 `Edge/Chrome` 调试会话
- 1688 发布页主线已完成真实干跑
- 类目自动选择已在 live 页面验证
- 成功结果提取已支持 `current_url / body_text / page_source`
- 可选属性缺失时会自动跳过，不阻塞整单

## 当前已经做完的核心能力

- 模板读取与字段校验
- 图片路径校验
- 本地配置覆盖机制
- 选择器采集工具
- 环境体检
- 数据库预检骨架
- 发布前错误捕获
- 主图上传桥接
- 详情图上传桥接
- TinyMCE 描述写入
- 类目路径自动化
- 1688 smoke 回归模板

## 当前剩余 blocker

- 真实“保存草稿”按钮还没有完成 live 选择器确认
- 真实“发布成功”后的 offer 链接/ID 还没有完成最终验证
- 自动提交模式还没有完成生产级验证
- SQL Server 真实账号密码仍待提供
- 聚水潭系统链路仍属于后续阶段，不是当前 1688 主线 blocker

## 当前推荐下一步

1. 在真实 1688 发布页抓到草稿按钮选择器
2. 切换 `auto_save_draft` 做一次真实草稿保存验证
3. 在可控商品上做一次真实提交验证
4. 回填成功页结果提取
5. 再决定是否开启自动提交

## 关键文件

- [config/platforms/1688.json](D:/script_files/ERP/furniture-uploader/config/platforms/1688.json)
- [config/operator_config.local.json](D:/script_files/ERP/furniture-uploader/config/operator_config.local.json)
- [rpa/browser_rpa.py](D:/script_files/ERP/furniture-uploader/rpa/browser_rpa.py)
- [rpa/doctor.py](D:/script_files/ERP/furniture-uploader/rpa/doctor.py)
- [templates/1688_corner_table_smoke.csv](D:/script_files/ERP/furniture-uploader/templates/1688_corner_table_smoke.csv)
- [docs/PLATFORM_EXPERIENCE_KB.md](D:/script_files/ERP/furniture-uploader/docs/PLATFORM_EXPERIENCE_KB.md)

## 永久说明

新的人类同事或 AI 接手时，默认先读：

1. `docs/README.md`
2. `docs/PROJECT_MEMORY.md`
3. `docs/DIRECT_1688_PROGRESS.md`
4. `docs/DELIVERY_HANDOVER_2026-03-24.md`
5. `docs/AI_CONTINUITY_GUIDE.md`
