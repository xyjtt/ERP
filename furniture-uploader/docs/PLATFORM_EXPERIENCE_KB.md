# Platform Experience Knowledge Base

## 2026-07-27 Long-Running Stop-Sale Recovery Findings

- 长时间每日下架不能假设 Crawler Worker 在外层上下文中一直保持 Disabled。其他计划任务或人工操作可能在中途重新启用它，因此每个浏览器批次尝试前都要重新检查计划任务状态、Worker 进程和 Profile Edge 进程。
- 重申暂停时只允许操作指定的 `YYDD-1688-Crawler-Worker` 任务并等待其拥有的进程自然退出；未知 Profile Edge 必须等待或安全失败，不能通过全局结束 Python/Edge/Node 兜底。
- Worker 或活动爬虫保护失败属于“批次已创建但未执行”的终态，应写 `not_attempted`、Pipeline Summary、审计结束状态和钉钉通知。仅打印异常或只发管理器总通知不足以追踪单批次。
- `Timed out receiving message from renderer`、通用 selector 超时等 `automation_error` 可能表示当前 Edge renderer 已损坏。继续在同一个浏览器对象内重试价值很低；应关闭当前代码拥有的店铺浏览器、重新打开相同 Profile、校验会话后再重试。
- 浏览器重建仅适用于可重试自动化异常。`login_required` 使用账户级自动登录恢复，`risk_control` 必须停止，`sole_sku_requires_product_offline`、`product_unavailable` 等业务终态只记录和通知。
- 2026-07-27 现场排查已明确：`1688-Watchdog` 只上报 CDP/爬虫状态，不会启动 Crawler Worker，不能把它写成事故根因。

## 2026-07-23 SKU Replacement Findings

- 商品管理入口对下架和替换都必须归一化为 `tab=all`，并在页面加载后显式确认“全部”Tab；“销售中”会漏掉已下架、审核中或其他状态商品。
- `tab=all` URL 不能作为最终证据：商品管理内容在 iframe/SPA 内渲染，搜索前必须在 iframe 中确认“全部”具有 active/aria-selected 状态；无法确认时按 `management_tab_mismatch` 停止整个店铺并告警。
- 下架和替换 Pipeline 默认不等待共享锁；重复启动会立即失败并返回占锁状态。运行日志必须保留完整前台输出，不允许通过 `head` 截断后再次启动同一批次。
- SKU 单品货号位于发布页运行时 `SellPublishSdk.engine.getJsonState().components.skuTable` 的 `sku_cargoNumber`；受控写入使用 `core.changeElementValue('skuTable', nextValues, {isDepth:false})`。
- 同商品多 SKU 应先做整组冲突校验，再一次性修改 `skuTable`、一次提交，最后重新打开商品编辑页逐项复核新货号。
- 幂等判断：旧货号不存在且新货号已存在时记为 `already_replaced`；新货号属于未参与映射的其他行时记为 `replacement_sku_conflict`，禁止提交。
- 聚水潭替换后不是“清除链接”，而是“手动同步商品 -> 按链接同步”；按店铺填写去重商品 ID，每批限制 50 个，并为每个原始 SKU 保留独立结果。

## 2026-07-24 Live Management Tab Probe

- 开发机使用工莱真实 Profile 打开 1688 商品管理 iframe，未触发自动登录。
- 探针先将真实页面切到 `销售中(1104)`，再调用统一的 Tab 校验逻辑；运行上下文记录 `management_products_tab_click=all`，最终 DOM 同时满足“全部”按钮 `aria-selected=true` 和父节点 `ant-tabs-tab-active`。
- 本探针只切换商品列表 Tab，没有搜索商品、修改 SKU 或提交发布。
- 后续只读商品探针出现过 iframe 超过 10 秒才渲染 Tab 的情况；生产默认等待已调整为 30 秒，超时后仍按 `management_tab_mismatch` fail-closed。

## 2026-07-24 Duplicate Barcode Offline Rule

- 同一商品 ID 内允许多个规格行使用完全相同的单品货号/条形码。停产输入仍按商品 ID + 条形码去重，但编辑页必须定位并下架该条形码的全部匹配行，只提交一次。
- 每切换一行后重新扫描当前 React DOM，避免第一行切换引起表格重渲染后继续使用失效的 WebElement。
- 提交前的 `skuTable` 状态写入和提交后复核都必须覆盖全部匹配索引；任一重复行仍在线时，该任务不得记为成功。
- 若目标条形码的全部匹配行就是商品当前全部在线 SKU，批量下架会导致零在线 SKU，仍按 `sole_sku_requires_product_offline` 业务异常停止并钉钉告警，不自动整商品下架。

## 2026-03-26 Managed Update

- New live-page finding:
  - required `配送服务` lives under `#guid-customExtraService .special-service-wrapper`;
  - it can show inline `配送服务为必填项` even when toast-level message selectors stay empty.
- Engineering updates:
  - added delivery-service auto-fill (prefer existing checked option, otherwise enable switch + check first option);
  - added broader stale retry support for dynamic 1688 React re-render behavior.
- Practical limitation observed:
  - some products still show `submitted but still online` after execute, meaning submit-state confirmation may need extra page-level success signals beyond current toast selectors.

更新时间：2026-03-26

## 用途

记录平台级经验，供后续同平台功能复用。

## 适用范围

当前主要覆盖：

- 1688 发布页自动上架
- 1688 商品管理页 SKU 下架

未来可以继续扩展到：

- 1688 草稿保存
- 1688 正式发布
- 1688 多类目、多店铺差异化铺货
- 1688 SKU 下架批处理

## 当前已验证的 1688 经验

### 1. 当前 live 页面关键选择器

- 标题：`#guid-title input[maxlength="60"]`
- 价格：`#guid-priceRange td[data-next-table-col="2"] input`
- 库存：`#guid-totalSales input`
- 发布按钮：`#submitFormButton`
- 类目修改按钮：`#guid-catNamer button`
- 类目确认按钮：`#submitButton`
- 错误信息面板：`#guid-assistBoard .info-list li div`

### 2. 已验证类目路径

- `家装建材 > 客厅家具 > 角几/边几`

### 3. 已验证上传策略

- 主图：优先用 React bridge
- 详情图：优先用 React bridge，完成后插入 TinyMCE
- 描述：优先直接写 TinyMCE 内容

### 4. 已验证容错策略

- 可选属性不存在时自动跳过
- 类目文本没有 `>` 分隔符时，仍可识别为当前类目

### 5. 1688 SKU 下架当前页面理解

- 入口页：商品中心 -> 商品管理
- 搜索字段：`商品ID`
- 编辑入口：`修改详情`
- 实际执行位置：编辑页 `销售信息`
- SKU 定位键：`单品货号`
- 执行动作：切换 `是否上架`
- 当前保存动作：优先尝试 `我要发布`

### 6. 1688 SKU 下架店铺过滤经验

- 店铺名经常存在“公司全称 vs 运营简称”的差异，例如 `阿里巴巴-常州速班达家居有限公司` 与 `速班达家居`
- 过滤应使用“规范化匹配”，不能只做字符串全等
- 预览必须输出过滤诊断（`applied_filters / loaded_store_names / filter_hint`），避免批次误判为“无任务”

## 当前可复用代码清单

### A. 类目路径自动化

- 文件：[rpa/browser_rpa.py](D:/script_files/ERP/furniture-uploader/rpa/browser_rpa.py)
- 关键词：`category_path`
- 可复用场景：同平台其他类目的路径选择

### B. 非 DOM 结果提取

- 文件：[rpa/browser_rpa.py](D:/script_files/ERP/furniture-uploader/rpa/browser_rpa.py)
- 关键词：`current_url`, `body_text`, `page_source`
- 可复用场景：成功页不稳定、弹窗不稳定的平台

### C. 选择器体检

- 文件：[rpa/doctor.py](D:/script_files/ERP/furniture-uploader/rpa/doctor.py)
- 可复用场景：新平台接入前快速发现空 selector

### D. smoke 模板回归

- 文件：[templates/1688_corner_table_smoke.csv](D:/script_files/ERP/furniture-uploader/templates/1688_corner_table_smoke.csv)
- 可复用场景：联调前的标准回归入口

### E. SKU 下架任务解析

- 文件：[rpa/sku_offline_tasks.py](D:/script_files/ERP/furniture-uploader/rpa/sku_offline_tasks.py)
- 可复用场景：同平台其他批处理任务的 Excel 读取、筛选、去重、防重

### F. SKU 下架预览诊断

- 文件：[rpa/sku_offline_main.py](D:/script_files/ERP/furniture-uploader/rpa/sku_offline_main.py)
- 关键词：`applied_filters`, `loaded_store_names`, `filter_hint`
- 可复用场景：批次预检、托管模式自动巡检

## 当前踩坑记录

### 坑 1：类目路径不要按 `/` 拆分

因为像 `角几/边几` 这种叶子类目本身就带 `/`。

### 坑 2：不要把不存在的可选字段当失败

不同类目下品牌、材质等属性可能完全不出现。

### 坑 3：不要默认成功页一定有稳定按钮或卡片

应优先从 URL 和页面文本做兜底提取。

### 坑 4：不要直接用业务模板做基础联调

业务模板常带脏数据、无效图片路径和非标准类目。

### 坑 5：不要把“商品 ID 命中”当成“SKU 已命中”

1688 下架是 SKU 级操作，搜索到商品后还必须再按 `单品货号` 精确定位。

### 坑 6：不要假设店铺名在 Excel 和配置中完全一致

真实数据中经常出现全称/简称差异，直接全等过滤会导致 `selected_count = 0` 的假阴性结果。

### 坑 7：不能把 Profile 失效等同于要求运营手动登录

1688 的账号级登录能力已经存在于共享项目 `src.cli`。下架或替换执行时，正确顺序是：

1. 复用账号 Profile 打开管理页并检查登录跳转、页面风控和店铺身份。
2. 仅在登录失效且没有检测到风控时关闭 ERP 浏览器，调用 `python -m src.cli login`。
3. 自动登录成功后重新创建 ERP 浏览器，再次检查管理页和店铺身份。

自动登录最多每店一次。返回验证码、滑块或风控必须停止该店并告警，不得自动拖动或重试刷验证。

### 坑 8：显式 Edge 路径不能和系统其他安装目录混合选版本

当 `browser_binary_path` 已指定时，驱动版本检测只能检查该路径所属的 `Application` 目录。继续扫描 `PROGRAMFILES/LOCALAPPDATA` 并选最高版本，会把另一套 Edge 的版本误认为目标浏览器版本，最终造成驱动错配。只有未提供显式路径时才允许扫描系统默认安装目录。

## 知识库更新规则

出现以下情况就必须更新本文件：

- 抓到新的 live selector
- 验证了新的类目路径
- 发现新的稳定上传方式
- 修掉一个平台级坑
- 总结出可以复用到同平台其他页面的实现
