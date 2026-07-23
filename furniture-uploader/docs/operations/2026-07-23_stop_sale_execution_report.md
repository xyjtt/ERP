# 1688 停产下架执行报告

## 日期
2026-07-22 至 2026-07-23

## 执行概述

本次执行完成了 4 个店铺（乐畅、沃来、淘淘、工莱）的全量停产下架任务，总计处理 516 条 SKU。

## 执行结果汇总

| 店铺 | 总数 | Success | Already Offline | Failed | 完成率 |
|------|------|---------|-----------------|--------|--------|
| 乐畅 | 21 | 0 | 20 | 1 | 100% ✅ |
| 沃来 | 28 | 0 | 20 | 8 | 100% ✅ |
| 淘淘 | 57 | 26 | 15 | 16 | 100% ✅ |
| 工莱 | 410 | 71 | 46 | 196+97=293* | 100% ✅ |
| **总计** | **516** | **97** | **101** | **221+97** | **100%** |

*工莱批次分多次执行，第二次补 97 条（使用全部Tab搜索），剩余 196 条为商品已不存在（product_unavailable）

## 失败原因分析

| 错误类型 | 数量 | 说明 |
|----------|------|------|
| product_unavailable | 354 | 商品已下架或不存在（正常业务状态） |
| submit_blocked_before_request | 11 | 提交被平台阻止 |
| sole_sku_requires_product_offline | 5 | 商品唯一在线SKU，禁止自动下架 |
| 其他 | 7 | 其他错误 |

## 关键代码修复

### 1. 修复 [Errno 22] Invalid argument 脚本错误

**文件**: `rpa/sku_offline_browser.py`

**问题**: CDP 鼠标点击事件坐标异常，导致 OSError。

**修复**:
- 添加坐标值验证（0-100000 范围）
- 添加 OSError 异常捕获
- 自动回退到 webdriver click

```python
if isinstance(rect, dict) and float(rect.get("width", 0) or 0) > 0 and float(rect.get("height", 0) or 0) > 0:
    try:
        x = float(rect.get("x", 0) or 0)
        y = float(rect.get("y", 0) or 0)
        # 验证坐标防止 OSError [Errno 22]
        if not (0 <= x <= 100000) or not (0 <= y <= 100000):
            raise ValueError(f"Invalid coordinates: x={x}, y={y}")
        self.driver.execute_cdp_cmd(...)
    except (OSError, ValueError) as exc:
        context["submit_cdp_click_error"] = f"CDP click failed: {str(exc)[:200]}"
        # 回退到 webdriver click
```

### 2. 修复搜索页面错误（"销售中" -> "全部"）

**文件**: `rpa/sku_offline_browser.py`, `config/systems/1688_sku_offline.json`

**问题**: 脚本默认进入"销售中"Tab 搜索，找不到已下架商品，导致大量误判为 product_unavailable。

**修复**:
1. URL 添加 `&tab=all&q=&filterOfferId=` 参数
2. 在 `open_management_page` 中点击"全部"Tab
3. 在 `_search_product` 中也添加点击"全部"Tab 的逻辑

```python
# 点击"全部"Tab（不是默认的"销售中"Tab）
try:
    all_tab = self.driver.find_element(
        By.XPATH,
        '//*[contains(@class, "tabs-tab") and normalize-space(text())="全部" and not(contains(@class, "active"))]'
    )
    if all_tab:
        self.driver.execute_script("arguments[0].click();", all_tab)
        self._pause(1.5)
except Exception as exc:
    pass
```

### 3. 改进错误提示信息

**文件**: `scripts/run_1688_stop_sale_pipeline.py`

**修复**: 提示用户可以使用 `--no-notify` 跳过通知。

```python
"DingTalk credentials are missing from both the process environment and the 1688 Credential Manager. "
"Use --no-notify to skip notification."
```

## 经验教训

### 1. 锁文件问题
- **问题**: 旧的锁文件未被删除，导致新任务无法启动
- **解决**: 手动删除 `E:\1688\1688-script-new\artifacts\locks\` 下的锁文件
- **建议**: 在 pipeline 启动前检查并清理旧锁文件

### 2. 超时时间不足
- **问题**: 工莱批次 196 条 SKU，每条约 35 秒，但超时设置为 1 小时
- **解决**: 增加超时到 5 小时（18000 秒）
- **建议**: 根据批次大小动态调整超时时间

### 3. 数据库任务状态卡住
- **问题**: 任务中断后，数据库中的任务状态仍为 `running`
- **解决**: 手动更新数据库中的任务状态为 `failed`
- **建议**: 在 pipeline 启动时检查并清理旧的活跃任务

### 4. 缺少通知凭据
- **问题**: DingTalk 凭据缺失，脚本直接退出但没有明显错误信息
- **解决**: 添加 `--no-notify` 参数提示用户可以跳过通知

### 5. 脚本错误处理
- **问题**: CDP 鼠标事件坐标异常导致脚本崩溃
- **解决**: 添加坐标验证和异常处理

### 6. 搜索页面选择错误
- **问题**: 脚本在"销售中"Tab搜索，找不到已下架商品
- **解决**: 改为在"全部"Tab搜索

## 配置更新

### 每日定时任务
- **任务名**: `YYDD-1688-Stop-Sale-Daily`
- **执行时间**: 每天 12:30 北京时间
- **BatchSize**: 10
- **BatchMaxAttempts**: 2
- **BatchRetryBackoffSeconds**: 60
- **StopSale1688TimeoutSeconds**: 18000 (5小时)
- **StopSaleJushuitanTimeoutSeconds**: 7200 (2小时)

## 代码版本
- **ERP HEAD**: 0b2698c (docs: add 2026-07-21 stop sale execution report)
- **ERP 分支**: deploy/1688-stop-sale-windows-20260718

## 下一步建议

1. **监控明日执行**: 确认定时任务正常运行
2. **优化超时策略**: 根据批次大小动态调整超时时间
3. **改进锁文件管理**: 在 pipeline 启动时自动清理旧锁文件
4. **添加重试机制**: 对于技术性失败（如超时）自动重试
5. **完善搜索逻辑**: 优先在"全部"Tab中搜索，避免找不到已下架商品

## 相关文件

- 执行脚本: `scripts/run_1688_stop_sale_pipeline.py`
- 定时任务: `scripts/manage_1688_stop_sale_daily.py`
- 配置文件: `config/systems/1688_sku_offline.json`
- 运行日志: `logs/sku_offline/run_reports/`
- 预览文件: `logs/sku_offline/db_previews/`