# Delivery Handover 2026-03-24

这是当前项目的正式交接文档，面向两类接手方：

- 人类开发/运营同事
- 后续 AI 代理

## 1. 项目现状

### 当前目标

先完成 1688 店铺后台自动上架的最小可落地版本，再逐步扩展到更多平台与聚水潭协同。

### 当前完成度

- 已完成自动填表主流程
- 已完成类目路径自动化
- 已完成主图、详情图、描述写入
- 已完成真实干跑闭环
- 已具备草稿/提交两种最终动作的代码能力

### 当前边界

默认仍停在人工复核，不自动点击最终草稿或发布按钮。

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
4. 在 live 页面抓草稿按钮
5. 打通真实草稿保存

## 8. 交接结论

这个项目已经不是“概念验证”阶段，而是“最终动作收口”阶段。

真正剩下的工作已经很聚焦：

- 抓草稿按钮
- 验证真实草稿
- 验证真实提交
- 回收成功结果
