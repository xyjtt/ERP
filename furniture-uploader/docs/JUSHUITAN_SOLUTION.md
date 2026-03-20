# 聚水潭铺货方案

创建时间：2026-03-17

## 1. 目标

在聚水潭 Web 的“商品多店铺铺货”流程中，针对指定店铺、指定商品完成批量铺货，并在发布后记录链接 ID、负责人和执行日志。

当前首个落地渠道为 1688（阿里巴巴）。

## 2. 总体架构

### 2.1 数据来源

- 第一阶段：Excel
- 第二阶段：业务 API

两种来源都统一映射到同一套发布任务模型。

### 2.2 执行链路

1. 读取 Excel/API 中的铺货任务
2. 根据商品编码、渠道、店铺名称定位聚水潭商品
3. 在聚水潭中执行“批量上架”
4. 选择平台与授权店铺
5. 尝试复用历史成功资料
6. 自动完善平台资料
7. 通过校验后直接发布
8. 回收发布后的链接 ID/链接地址
9. 记录负责人、执行结果、异常和截图

当前代码结构已经按三层拆开：

- `systems/jushuitan.json`：聚水潭登录与铺货入口
- `platforms/1688.json`：1688 发布页字段与异常规则
- `database.py`：SQL Server 结果落库

### 2.3 执行器

- 当前阶段：先固化流程与规则
- 后续执行：由本地部署的 OpenClaw 负责浏览器控制

## 3. 聚水潭流程拆解

### 阶段 A：商品选择

- 进入聚水潭商品列表
- 按商品编码、名称、标签等条件定位待铺货商品
- 勾选指定商品，点击“批量上架”

### 阶段 B：选择平台和授权店铺

- 选择平台，例如“阿里巴巴”
- 搜索并勾选授权店铺
- 点击“确定”进入平台资料流程

#### 规则

- Excel/API 中必须包含目标渠道和目标店铺
- 若搜索不到店铺，抛出 `StoreSelectionError`
- 若店铺存在多个近似结果，必须按精确店铺名比对

### 阶段 C：匹配商品资料

弹出“匹配商品资料”窗口后，不直接使用第一条候选，而是按历史命中记录做多候选重试。

#### 目标

- 优先复用历史成功资料
- 某条成功资料失效时自动尝试下一条
- 所有候选都失败后再进入“跳过，自己编辑”

#### 处理逻辑

1. 收集当前弹窗中的所有候选资料
2. 按以下优先级排序
   - 历史成功次数高
   - 最近成功时间新
   - 同店铺命中优先
   - 同类目命中优先
3. 依次点击“使用该资料”
4. 每次点击后检测是否出现异常
5. 若异常为“资料失效”“日志失效”“已不可用”“页面校验失败”等，记录该候选失效并回退
6. 自动尝试下一条候选
7. 若全部候选失败，点击“跳过，自己编辑”

#### 失效回退策略

- 单商品单次任务中，同一候选最多尝试 1 次
- 连续失败的候选进入冷却，不再作为高优先级候选
- 失败原因写入历史表，用于后续排序降权

#### 需要记录的数据

- `candidate_source_id`
- `candidate_store_name`
- `candidate_title`
- `candidate_use_result`
- `candidate_error_message`
- `candidate_error_type`
- `candidate_attempted_at`

### 阶段 D：完善平台资料

进入 1688 发布页后，自动填写平台资料。

#### 核心填写区

- 商品分类
- 商品主图/白底图/详情图
- 商品标题
- 产品属性
- SKU 规格值与规格图
- 单价
- 可售数量
- 是否上架
- 发货地址
- 运费模板
- 件重尺
- 详情描述

#### 发布前文本清洗

在进入发布动作前，先对规格相关文本字段执行清洗，删除表情符号等高风险字符，避免出现“产品规格包含表情符号”类报错。

默认清洗范围包括：

- 商品标题
- 副标题
- 品牌
- 材质
- 颜色
- 尺寸
- 规格相关字段
- SKU 相关字段

清洗后保留文本主体，并在运行日志中记录被清洗的字段。

在页面执行层还需要补一层 DOM 级清洗：

- 对规格区和 SKU 区已有输入框做二次扫描
- 如果历史资料带入了表情符号，提交前再次剔除
- 这一步不依赖 Excel 原值，主要用于拦截历史资料复用带来的脏数据

#### 模板选择规则

物流信息中的下拉框默认选预设模板。

当前至少包含两类默认模板：

- 发货地址模板
- 运费模板

#### 下拉框选择策略

1. 优先使用 Excel/API 指定模板
2. 若任务未指定，则使用店铺默认模板
3. 若默认模板未出现在下拉框中，先点击“刷新”
4. 刷新后仍不存在，则抛出 `TemplateSelectionError`

#### 店铺维度默认项

建议为每个店铺维护：

- 默认发货地址
- 默认运费模板
- 默认发货时效
- 默认件重尺策略

### 阶段 E：发布控制

当前按你的要求，第一阶段直接发布，不走草稿。

#### 发布前保护

直接发布不等于无保护，仍需要保留发布前校验：

1. 页面不存在红框必填错误
2. 必填字段已填充
3. SKU 行价格、库存、编码齐全
4. 发货地址和运费模板已选中
5. 平台返回的即时错误提示为空

#### 发布动作

- 校验通过后点击“发布”
- 若页面支持同步状态反馈，则等待发布成功提示
- 发布后提取链接 ID、链接地址、平台商品编号等结果

#### 发布失败处理

- 截图
- 记录错误消息
- 保存页面步骤名
- 写入失败日志
- 将任务标记为 `publish_failed`

#### 页面级异常拦截

执行器需要在提交前后主动检测页面文案和弹窗，至少拦截以下错误：

- 发布失败
- 产品规格包含表情符号
- 资料失效
- 日志失效

检测到后立即中断当前商品提交，并记录异常文本。

## 4. Excel / API 字段设计

Excel 和 API 都可以增加负责人字段，并在发布后与链接 ID 绑定。

### 4.1 建议字段

- `task_id`
- `channel`
- `store_name`
- `store_label`
- `outer_sku`
- `title`
- `category_hint`
- `brand`
- `material`
- `color`
- `size`
- `price`
- `quantity`
- `main_image`
- `detail_images`
- `description`
- `ship_from_template`
- `freight_template`
- `ship_time_template`
- `length_cm`
- `width_cm`
- `height_cm`
- `weight_g`
- `link_owner`
- `operator_name`
- `source_record_id`

### 4.2 负责人字段说明

- `link_owner`
  链接负责人，表示这个铺货结果最终归属给谁维护

- `operator_name`
  本次执行人，表示是谁触发了本次铺货

如果后续通过 API 下发任务，还建议增加：

- `biz_id`
- `request_id`

方便和业务系统对账。

## 5. 发布结果日志设计

发布成功后，需要把“链接 ID”和“负责人”绑定记录下来。

### 5.1 成功日志建议字段

- `task_id`
- `channel`
- `store_name`
- `outer_sku`
- `platform_link_id`
- `platform_link_url`
- `platform_item_title`
- `link_owner`
- `operator_name`
- `published_at`
- `source_type`
- `source_record_id`
- `match_source_id`
- `category_path`
- `status`

### 5.2 失败日志建议字段

- `task_id`
- `channel`
- `store_name`
- `outer_sku`
- `step_name`
- `error_type`
- `error_message`
- `screenshot_path`
- `html_snapshot_path`
- `operator_name`
- `failed_at`
- `status`

## 6. 历史知识库设计

建议用数据库保存，不建议长期只靠本地 JSON。

当前已确定数据库为：

- `SQL Server 2012`
- 数据库名：`JianSun`

### 6.1 推荐沉淀的表

- `publish_task`
  原始铺货任务

- `publish_result`
  发布结果

- `match_candidate_history`
  匹配商品资料的候选使用记录

- `category_mapping_history`
  商品关键词到平台类目映射

- `store_default_template`
  店铺默认发货地址、运费模板等

- `selector_change_log`
  页面变更与选择器修复记录

### 6.2 候选资料排序建议

建议评分公式包含：

- 成功次数
- 最近成功时间
- 同店铺命中
- 同类目命中
- 最近失败次数
- 是否出现过失效错误

对出现“失效/不可用”的候选做显著降权。

## 7. 异常体系

建议至少定义以下异常类型：

- `StoreSelectionError`
- `ProductMatchNotFoundError`
- `MatchCandidateInvalidError`
- `CategoryMappingError`
- `FieldMappingMissingError`
- `SelectorNotFoundError`
- `TemplateSelectionError`
- `PublishValidationError`
- `PublishSubmitError`

### 统一异常动作

- 截图
- 记录当前步骤
- 记录页面提示文案
- 写入错误日志
- 保留当前任务上下文
- 决定是否继续后续商品

## 8. 第一阶段实施范围

第一阶段建议只固定：

- 渠道：1688
- 店铺：一个已授权的阿里巴巴店铺
- 任务来源：Excel
- 执行结果：直接发布

### 第一阶段必须实现

1. 商品筛选与批量上架入口
2. 平台与店铺选择
3. 匹配资料候选重试
4. 默认模板下拉框选择
5. 1688 主要字段自动填写
6. 发布前校验
7. 发布后回收链接 ID
8. 负责人和发布日志落库

当前已实现到：

- 任务模板已支持 `store_name / store_label / outer_sku / link_owner / operator_name`
- 运行入口已支持 `--system jushuitan`
- 可选 SQL Server 日志已能写入成功/失败结果
- 已支持匹配商品资料候选重试、下拉框模板选择、发布成功结果提取
- 已支持从 SQL Server 的 `store_default_template` 读取店铺默认模板
- 已支持失败截图 + HTML 快照，并可在批量任务中继续执行后续商品
- 仍待补的核心是页面真实 selector 和发布成功后的链接 ID 抓取

## 9. 当前结论

基于已确认的信息，最适合的方案是：

- 继续使用聚水潭 Web 铺货
- 用 Excel/API 精确指定商品与店铺
- 历史成功资料采用“多候选重试 + 失效降权”
- 默认直接发布
- 发布后抓取链接 ID 并和负责人绑定
- 后续由 OpenClaw 接管页面执行
- 发布历史、异常日志、模板默认值落到 SQL Server 2012
