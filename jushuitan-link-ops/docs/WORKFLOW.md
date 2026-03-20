# Workflow

Updated: 2026-03-18

## Page Flow

目标页面是聚水潭：

- 店铺商品管理
- 批量修改线上商品编码

## Standard Steps

### Step 1. Open 店铺商品管理

- 进入聚水潭
- 打开 `店铺商品管理`

### Step 2. Select Search Dimension

- 选择 `按SKU`

### Step 3. Filter Platform / Shop

- 选择目标平台
- 选择目标店铺

如果任务未指定店铺：

- 抛出 `ShopSelectionError`

### Step 4. Search Old Online Code

在搜索区域输入：

- `old_online_sku_code`

然后点击搜索。

### Step 5. Open Batch Dialog

- 勾选目标记录
- 点击 `批量更新线上商品编码`

如果弹窗未出现：

- 抛出 `BatchDialogOpenError`

### Step 6. Fill New Code

在弹窗顶部输入：

- `new_online_sku_code`

然后执行：

- `批量填充`

### Step 7. Verify Rows Before Submit

逐行检查：

- 平台是否正确
- 店铺是否正确
- 原商品编码是否等于 `old_online_sku_code`
- 修改后是否等于 `new_online_sku_code`

若任一不满足：

- 抛出 `SearchResultMismatchError`

### Step 8. Confirm Submit

- 点击 `确定`

如果页面出现报错提示：

- 抛出 `SubmitConfirmError`

### Step 9. Re-query Historical Code

提交后必须返回列表页重新搜索：

- 再次用 `old_online_sku_code` 查询

返回结果：

- `remaining_count = 0`：成功
- `remaining_count > 0`：未完全成功

### Step 10. Return Result

返回至少包含：

- task_id
- platform
- shop_name
- old_online_sku_code
- new_online_sku_code
- matched_count_before
- remaining_count_after
- status
- error_message

## Status Rules

- `success`: 修改成功，且 `remaining_count_after = 0`
- `partial_success`: 提交成功，但 `remaining_count_after > 0`
- `failed`: 页面提交失败或校验失败

## Required Runtime Artifacts

- screenshot_path
- html_snapshot_path
- current_url
- page_title

## Manual Input Mode

手动输入模式最少需要：

- platform
- shop_name
- old_online_sku_code
- new_online_sku_code
- operator_name
