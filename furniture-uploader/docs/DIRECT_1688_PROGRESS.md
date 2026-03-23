# 1688 直连后台自动上架进度表

更新时间：2026-03-23

## 目标

先跑通一个最小可执行项目：

- 数据源：Excel / CSV
- 渠道：1688
- 方式：Selenium
- 策略：自动填表，人工复核后提交

## 进度表

| 编号 | 阶段 | 内容 | 当前状态 | 备注 |
|---|---|---|---|---|
| P1 | 项目骨架 | 复用现有 `furniture-uploader` 作为 1688 MVP 宿主 | 已完成 | 不新开仓库 |
| P2 | 执行入口 | 新增 `1688_direct` system 配置 | 已完成 | 已可被主程序识别 |
| P3 | 数据链路 | `Excel -> 校验 -> 运行入口` 打通 | 已完成 | `--validate-only` 正常 |
| P4 | 文档基线 | 新增 1688 MVP 说明文档 | 已完成 | 已写运行方式 |
| P5 | 操作流程 | 已读取 `1688.docx`，确认登录、进发布页、选类目、填详情页主链路 | 已完成 | 已提炼为自动化步骤 |
| P6 | 探针能力 | selector 抓取工具支持接入已有浏览器会话 | 已完成 | 已支持 `debugger-address` / `user-data-dir` |
| P7 | 会话复用 | 主执行器支持接入已有 Chrome 调试会话 | 已完成 | 减少重复登录 |
| P8 | 联调前置 | 评估当前 Chrome 是否可直接接管 | 已完成 | 当前未开启 remote debugging |
| P9 | 启动方式 | 补充可接管的 Chrome 启动方式 | 已完成 | 已新增调试 Chrome 启动脚本 |
| P10 | selector 采集 | 抓取 1688 登录后关键页面元素 | 进行中 | 已新增一键抓取脚本、selector 候选分析工具、增强探针标签/父级文本能力，并预埋类目路径上下文能力 |
| P11 | 配置补全 | 补齐 `config/platforms/1688.json` 关键 selector | 未开始 | 先补必填字段 |
| P12 | 首轮联调 | 跑通单店铺、单类目、单商品自动填表 | 未开始 | 目标先不自动提交 |
| P13 | 稳定性 | 增加异常提示、截图、重试策略 | 未开始 | MVP 后补 |
| P14 | 自动提交 | 评估是否开启自动点击发布 | 未开始 | 需在人工复核稳定后决定 |

## 当前落点

当前做到：`P10 selector 采集`

也就是：

- 代码骨架已经准备好
- 会话复用能力已经接好
- 调试 Chrome 启动方式已经固定
- 探针结果已增强到可输出标签文本和父级文本
- 下一步进入“抓取 1688 关键页面 selector”阶段

## 下一步

1. 固化可接管的 Chrome 启动方式
2. 抓取 1688 关键页面 selector
3. 先补 `title / price / quantity / main_image`
4. 跑通第一条自动填表链路

## 以后怎么看进度

如果你后面问：

- “现在做到哪一步了”
- “看一下进度”
- “P10 现在怎么样”

我都会按这张表直接回复对应状态和产出。
## 2026-03-23 Live Update

- P10 selector capture: completed for the current 1688 publish page session
- P11 platform config: completed for title, price, quantity, main image, detail images, description, and logistics template selectors
- P12 first live smoke: completed for `title / price / quantity / main_image / detail_images / description`
- Main image upload path no longer depends on the unstable picker opener; it now uses the live 1688 primary-picture React bridge
- Detail images now upload through the same bridge and are written back into TinyMCE as remote image URLs
- Remaining warnings are limited to category selection, publish error selectors, and success extractor selectors
