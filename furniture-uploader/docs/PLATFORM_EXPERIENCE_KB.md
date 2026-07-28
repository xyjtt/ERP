# Platform Experience Knowledge Base

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

## 知识库更新规则

出现以下情况就必须更新本文件：

- 抓到新的 live selector
- 验证了新的类目路径
- 发现新的稳定上传方式
- 修掉一个平台级坑
- 总结出可以复用到同平台其他页面的实现
## 2026-07-28 Live Finding: 1688 SKU Specification Entry

- Scope: `#guid-saleProp .module-spec-decorator` on the current 1688 publish page.
- Color accepts direct Chinese text. The verified sequence is: scroll the input into view, click the nearest `.value-select-container[aria-haspopup="true"]`, focus the input, clear and type the Chinese value, then press Tab.
- Do not require an exact suggestion option and do not choose the first standard color. The standard color-family overlay is optional UI assistance.
- Acceptance requires the expected value in `.value-select-item:not(.resident)` and the local required-field warning to be absent. Text left only in the resident input is not committed.
- Apply the same direct-entry and read-back rule to size.
- Never click a direct-save confirmation when the modal states that incomplete specification data will be cleared.

## 2026-07-28 Required Specification Review Rule

- A required 1688 specification must be checked for exact persisted text after refresh; non-empty alone is insufficient.
- Direct Chinese input plus Tab is valid for color, but review must compare the committed value with the payload.
- For CTG028601N1416V01, expected persisted values are color `胡桃色` and size `48/40/50`.
