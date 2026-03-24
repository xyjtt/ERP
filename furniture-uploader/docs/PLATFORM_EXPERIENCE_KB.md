# Platform Experience Knowledge Base

更新时间：2026-03-24

## 用途

记录平台级经验，供后续同平台功能复用。

## 适用范围

当前主要覆盖：

- 1688 发布页自动上架

未来可以继续扩展到：

- 1688 草稿保存
- 1688 正式发布
- 1688 多类目、多店铺差异化铺货

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

## 当前踩坑记录

### 坑 1：类目路径不要按 `/` 拆分

因为像 `角几/边几` 这种叶子类目本身就带 `/`。

### 坑 2：不要把不存在的可选字段当失败

不同类目下品牌、材质等属性可能完全不出现。

### 坑 3：不要默认成功页一定有稳定按钮或卡片

应优先从 URL 和页面文本做兜底提取。

### 坑 4：不要直接用业务模板做基础联调

业务模板常带脏数据、无效图片路径和非标准类目。

## 知识库更新规则

出现以下情况就必须更新本文件：

- 抓到新的 live selector
- 验证了新的类目路径
- 发现新的稳定上传方式
- 修掉一个平台级坑
- 总结出可以复用到同平台其他页面的实现
