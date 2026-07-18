# 1688 停产下架执行机 AI 交接

请先阅读：

1. `furniture-uploader/docs/operations/1688_STOP_SALE_WINDOWS_DEPLOYMENT.md`
2. `furniture-uploader/docs/operations/1688_SKU_OFFLINE_DATA_VALIDATION_2026-07-17.md`
3. `jushuitan-sku-offline-batch/docs/1688_LINK_CLEANUP.md`

执行边界：

- 只下架指定 SKU，禁止自动整商品 ID 下架。
- 唯一在线 SKU、活动限制、商品删除不重试。
- 不处理验证码、滑块或风控。
- 共用 `D:\script_1688` 全局锁，不允许绕开锁并发操作同一 Profile。
- 复用执行机已有登录 Profile，不复制开发机登录数据。
- 首轮只做 preview 和单店单条 execute。
- 运营日志走钉钉群机器人；技术日志保留在执行机本地。

执行机调试完成后，请回传：

- preflight JSON（不得包含秘密值）
- Gitee commit/branch
- Profile 映射是否全部存在
- 共享锁路径及竞争测试结果
- preview 结果
- 单条 execute 的 1688/JST 报告路径
- 钉钉 `notification_sent` 状态
