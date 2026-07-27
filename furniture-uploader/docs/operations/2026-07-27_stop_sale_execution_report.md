# 2026-07-27 1688 停产下架执行与修复报告

## 1. 生产执行结果

- 执行机：`PC-20210622ARIU`
- 管理批次：`daily_20260727_123002_977385`
- 运行时间：2026-07-27 12:30:02 至 16:58:59
- 数据：加载 116 条，选中 104 条，重复 12 条
- 1688：成功 13 条，已下架 10 条，失败 64 条，未尝试 17 条
- 聚水潭：成功 23 条
- 最终状态：`failed`，未完成当日全量下架

## 2. 异常分类

- 乐畅：7 条 `login_required` 后触发停店保护，9 条未尝试。
- 工莱/沃来：主要为等待“销售信息”区域超时。
- 淘淘：出现 Edge `Timed out receiving message from renderer`。
- 业务终态：14 条 `sole_sku_requires_product_offline`、3 条 `product_unavailable`、2 条 `sku_not_found`；按规则记录和通知，不自动整商品下架。
- 淘淘最后两个重试批次在 Pipeline 启动前被 Worker 活动保护拦截，因此旧代码没有生成单批次 Summary、正式审计终态和单批次通知。

## 3. 根因结论

1. 每日调度只在最外层暂停一次 Crawler Worker，无法防止长任务期间被外部计划任务或人工操作重新启动。
2. Worker/活动爬虫保护位于 `run_pipeline()` 之外，保护失败发生在审计批次创建前，造成批次证据缺口。
3. `automation_error` 重试复用原浏览器对象，renderer 卡死后继续重试同一损坏会话。
4. `1688-Watchdog` 已检查，只做 CDP/爬虫状态上报，不启动 Worker，不是本次外部重启的根因。

## 4. 代码修复

- `manage_1688_stop_sale_daily.py`
  - 新增 `ensure_worker_paused()`。
  - 每次批次尝试前重新检查并在必要时 Disable/Stop Worker。
  - 将干预写入 `worker_reassertions`，不全局终止进程。
- `run_1688_stop_sale_pipeline.py`
  - 新增审计生命周期内的 `execute_guard`。
  - 保护失败时写 `not_attempted`、Summary、审计结束状态并发送钉钉。
  - 避免 Pipeline 已记录异常后由主入口重复发送同类通知。
- `rpa/sku_offline_main.py`
  - `automation_error` 重试前重建当前店铺拥有的浏览器会话。
  - 新增浏览器恢复计数，登录/风控/业务终态保持原安全边界。

## 5. 验证与上线边界

- 聚焦回归覆盖 Worker 外部重启、保护失败审计闭环、分组返回异常恢复和直接抛异常恢复。
- 开发机全量验证：Python `319 passed, 5 subtests passed`，`compileall` 通过；聚水潭 `npm run check`、`18/18` 测试和 `npm run build` 通过。
- `1688_direct` doctor 为 `status: ok`（保留两个既有空 selector warning）；下架样例 preview 加载 3 条、选中 2 条、过滤 1 条，未启动真实浏览器操作。
- 开发机测试只证明代码路径，不能证明真实 1688 页面、聚水潭、执行机环境或正式数据库已经验收。
- 提交后仍需等待执行机当前业务结束，再部署并执行真实 Canary。验收必须同时具备页面/API、Pipeline Summary、正式审计表和钉钉证据。
