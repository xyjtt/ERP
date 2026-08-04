# Delivery Handover 2026-03-24

## 2026-08-04 Combination SKU Skip Handover

- `可替换商品编码（新）` 去除前后空格后等于 `运营自行组合替换` 时，按业务跳过处理，异常原因固定为 `组合货号`，编码为 `combination_sku`。
- 数据源 preview 生成独立 `business_skipped` CSV/JSON；该类行不进入可执行 CSV，不触发异常拒绝通知。
- 正式执行入口在租约、审计、Saga、浏览器和聚水潭之前再次过滤；全跳过输入返回 `business_skipped`，`online_actions_started=false`。
- 本地测试不能替代执行机验收；部署前仍需确认没有正在运行的爬虫、下架、替换或上架浏览器任务。

## 2026-07-28 Automatic Login Handover

- 下架/替换登录恢复调用共享 1688 CLI 时必须显式开启受限滑块 RPA 和身份核验；普通爬虫登录默认不变。
- 执行机外置 `accounts.json` 的四店条目必须有真实探测并审核后的 `expected_member_id`，或同值稳定 `shop_id=1688-member:<member_id>`；preflight 的 `all_account_identities_configured` 必须为 `true`。
- 退出码 2 表示滑块/风控未解决，退出码 3 表示真实店铺或 `member_id` 不匹配，退出码 4 表示身份无法证明。只有退出码 3 映射为停店安全终态，其余异常记录、通知并继续其他店铺。
- 部署后只允许替换 Preview 和一条合法旧 SKU -> 新 SKU Canary；核对 1688、聚水潭、正式审计、Summary 和钉钉后再决定是否扩大批量。

## 2026-07-27 Stop-Sale Runtime Recovery Handover

- 开发分支：`codex/1688-sku-replacement-20260723`。本次修复提交部署前必须先确认执行机没有正在运行的下架、替换或爬虫业务。
- 部署时不得 stash/reset/checkout/clean 执行机现有修改；先汇报 dirty 清单，再以不覆盖本地文件的方式处理差异和冲突。
- 新管理器 Summary 字段：`worker_pause_check_count` 记录尝试前检查次数，`worker_reassertions` 记录外部重启后执行的 Disable/Stop 动作及对应店铺、批次、尝试和 run_id。
- 新 RPA Summary 字段：`browser_recovery_attempts`、`browser_recovery_success`、`browser_recovery_failed`。
- 执行机验收顺序：确认无活动业务 -> 安全部署 -> preflight `status=ok` -> 单店单商品真实 Canary -> 核对 1688 页面与聚水潭结果 -> 核对 `app.ali1688_stop_sale_run/item` -> 核对 Pipeline Summary 和钉钉 -> 恢复 Worker/每日任务。
- 任何开发机单测、mock 或 preview 结果都不能替代真实页面/API、正式审计库和通知验收。

这是当前项目的正式交接文档，面向两类接手方：

- 人类开发/运营同事
- 后续 AI 代理

## 1. 项目现状

### 当前目标

先完成 1688 店铺后台自动化的最小可落地版本，再逐步扩展到更多平台与聚水潭协同。

### 当前完成度

- 已完成自动填表主流程
- 已完成类目路径自动化
- 已完成主图、详情图、描述写入
- 已完成真实干跑闭环
- 已具备草稿/提交两种最终动作的代码能力
- 已新增 1688 SKU 下架第一阶段代码骨架

### 当前边界

默认仍停在人工复核，不自动点击最终草稿或发布按钮。

补充：

- SKU 下架链路已经支持 `预览 / 单文件执行 / 定时扫描`
- 但仍需要真实页面联调来确认下架页选择器和保存结果

## 2. 当前关键结论

### 已证实可用

- 1688 当前发布页可以通过已登录 Edge 调试会话稳定接管
- 主图上传最稳定的方式是调用页面自己的 React bridge
- 详情图同样适合走 bridge，再写回 TinyMCE
- 类目路径支持直接从 `resolved_category_levels` 驱动
- 成功结果提取不应依赖脆弱的成功页 DOM，优先从 `current_url` 等来源提取

### 当前还未证实

- 真实“保存草稿”按钮的最终 selector
- 真实“发布成功”结果页的 offer ID/URL 是否能稳定提取

## 3. 同平台可复用代码

下面这些能力对 1688 同平台后续开发可以直接复用：

### 浏览器接管

- 文件：[rpa/browser_rpa.py](D:/script_files/ERP/furniture-uploader/rpa/browser_rpa.py)
- 作用：接管已登录浏览器，避免重复登录和验证码问题

### 发布主流程

- 文件：[rpa/browser_rpa.py](D:/script_files/ERP/furniture-uploader/rpa/browser_rpa.py)
- 作用：统一处理 `input / combobox / picker_upload / tinymce / tinymce_images / category_path / extract`

### 1688 平台配置

- 文件：[config/platforms/1688.json](D:/script_files/ERP/furniture-uploader/config/platforms/1688.json)
- 作用：沉淀 1688 当前发布页的 live selector 和动作配置

### smoke 样本

- 文件：[templates/1688_corner_table_smoke.csv](D:/script_files/ERP/furniture-uploader/templates/1688_corner_table_smoke.csv)
- 作用：后续每次联调先跑这一条，避免直接拿业务大表试错

### 体检工具

- 文件：[rpa/doctor.py](D:/script_files/ERP/furniture-uploader/rpa/doctor.py)
- 作用：检查配置是否缺 selector、是否存在明显错误

### SKU 下架任务入口

- 文件：[rpa/sku_offline_main.py](D:/script_files/ERP/furniture-uploader/rpa/sku_offline_main.py)
- 作用：处理 1688 下架任务的预览、执行、扫描、重试和通知

### SKU 替换与聚水潭同步

- 配置：[config/systems/1688_sku_replace.json](D:/script_files/ERP/furniture-uploader/config/systems/1688_sku_replace.json)
- 流水线：[scripts/run_1688_sku_replace_pipeline.py](D:/script_files/ERP/furniture-uploader/scripts/run_1688_sku_replace_pipeline.py)
- 样本：[templates/1688_sku_replace_sample.csv](D:/script_files/ERP/furniture-uploader/templates/1688_sku_replace_sample.csv)
- 作用：按 `线上商品编码 -> 可替换商品编码（新）` 修改 1688 SKU 单品货号，并在成功后触发聚水潭“按链接同步”。
- 边界：默认 preview；没有业务批准的真实旧新 SKU 映射时，不执行线上发布或聚水潭立即下载。

## 4. 项目经验

### 经验 1：不要把“选择器能填”当成“流程可上线”

真实上线前至少要分成：

- 填表阶段
- 最终动作阶段
- 成功结果回收阶段

这三段分别验证。

### 经验 2：1688 富文本和图片上传不能只靠传统 input[file]

当前最稳定的方案是桥接到页面内部上传逻辑，而不是死点弹窗。

### 经验 3：类目相关属性经常是动态的

同一个类目有属性、另一个类目没有属性是常态，所以可选字段必须支持自动跳过。

### 经验 4：成功页 DOM 往往不如 URL 稳定

因此结果提取框架已经优先支持：

- `current_url`
- `body_text`
- `page_source`

### 经验 5：必须保留 smoke 样本

生产模板太脏、变量太多，不能拿来做每次基础回归。

## 5. 持续学习和进化文档

本项目已经把“持续学习”拆成三类文档：

- [PROJECT_MEMORY.md](D:/script_files/ERP/furniture-uploader/docs/PROJECT_MEMORY.md)
  - 当前事实，接手必读
- [PROJECT_EVOLUTION.md](D:/script_files/ERP/furniture-uploader/docs/PROJECT_EVOLUTION.md)
  - 记录策略变化和演化节点
- [PLATFORM_EXPERIENCE_KB.md](D:/script_files/ERP/furniture-uploader/docs/PLATFORM_EXPERIENCE_KB.md)
  - 沉淀平台经验、可复用代码、踩坑和验证结论

## 6. 后续 AI 怎么接手

推荐固定读取顺序：

1. `AGENTS.md`
2. `docs/README.md`
3. `docs/PROJECT_MEMORY.md`
4. `docs/DIRECT_1688_PROGRESS.md`
5. `docs/PLATFORM_EXPERIENCE_KB.md`
6. `docs/AI_CONTINUITY_GUIDE.md`

## 7. 下一位接手者最应该先做什么

1. 跑单测
2. 跑 `doctor`
3. 用 smoke 模板跑一次 `--limit 1 --skip-login`
4. 在测试店铺验证 `1688_sku_offline`
5. 在 live 页面抓草稿按钮
6. 打通真实草稿保存

## 8. 交接结论

这个项目已经不是“概念验证”阶段，而是“最终动作收口”阶段。

真正剩下的工作已经很聚焦：

- 联调 1688 SKU 下架页
- 抓草稿按钮
- 验证真实草稿
- 验证真实提交
- 回收成功结果

## 9. 2026-07-24 自动登录交接说明

- 新增 `rpa/sku_offline_auth.py`，下架和替换共用。
- 执行机必须通过 `--shared-runtime-root` 指向真实 `E:\1688\1688-script-new`，该目录需要包含 `src\cli.py` 和账号 Profile/Credential Manager 配置。
- 生产默认自动恢复过期登录态；人工调试可使用 `--require-manual-login`。
- 预期钉钉结果：自动登录成功且身份匹配后继续执行；退出码 1/2/4 记录对应技术或认证异常并继续隔离后的业务范围；退出码 3 才因真实店铺/`member_id` 不匹配停止店铺。
- 交付验证缺口：需要执行机关闭或使一个测试店铺 Profile 失效，确认自动登录命令、店铺身份复核和后续 SKU 操作真实完成。不能用单元测试代替该 canary。
