# 平台上架下架任务与数据中台 - 第一阶段开发启动清单

## 文档目的

用于在第一阶段正式开工前，快速确认需求、方案、数据、接口和环境都已对齐，避免开发过程中反复返工。

## 1. 文档真源检查

- [ ] 已确认需求真源：`docs/requirements/PLATFORM_MEDIATION_REQUIREMENT_INTAKE.md`
- [ ] 已确认技术方案真源：`docs/plans/PLATFORM_MEDIATION_TECH_PLAN.md`
- [ ] 已确认架构真源：`docs/architecture/PLATFORM_MEDIATION_ARCHITECTURE.md`
- [ ] 已确认路线图：`docs/roadmap/PLATFORM_MEDIATION_ROADMAP.md`
- [ ] 已确认第一阶段工作包：`docs/plans/PLATFORM_MEDIATION_PHASE1_WORKPACKAGES.md`
- [ ] 已确认 API 与适配器契约：`docs/plans/PLATFORM_MEDIATION_API_CONTRACT_V1.md`
- [ ] 已确认数据模型说明：`docs/architecture/PLATFORM_MEDIATION_DATA_MODEL.md`

## 2. 范围检查

- [ ] 当前阶段只做 `1688 中台 v1`
- [ ] 现有 `T-005` 执行层只包装，不重写主执行链路
- [ ] 第一阶段不做全平台覆盖
- [ ] 第一阶段不做完整在线规则平台
- [ ] 第一阶段不做重型监控平台

## 3. 数据与 schema 检查

- [ ] `task / task_item / source_snapshot / task_attempt / task_artifact / reflow_event` 结构已确认
- [ ] `platform_adapter` 已有稳定业务键
- [ ] `mapping_version` 和 `mapping_rule` 口径已统一
- [ ] 锁与租约模型已明确
- [ ] `reflow_event` 幂等键已设计
- [ ] `shop_config` 密钥策略已确定

## 4. 执行链路检查

- [ ] 已明确 `1688 adapter v1` 的包装边界
- [ ] 已明确适配器输入结构
- [ ] 已明确适配器输出结构
- [ ] 已明确错误分类
- [ ] 已明确工件归档规则

## 5. 环境与资源检查

- [ ] 已确认 `Windows + Python + SQL Server` 为第一阶段技术基线
- [ ] 已确认 1688 RPA 执行环境可用
- [ ] 已确认店铺账号、会话和登录方式
- [ ] 已确认聚水潭数据源访问权限
- [ ] 已确认 AI 素材接口访问方式

## 6. 开发顺序检查

- [ ] 先做 `WP-01` 1688 适配器包装
- [ ] 再做 `WP-02` 任务中心与状态机
- [ ] 再做 `WP-03` 锁与执行资源治理
- [ ] 再做 `WP-04` 数据快照与标准商品模型
- [ ] 再做 `WP-05` 三层映射与版本绑定
- [ ] 再做 `WP-06` 结果落库与工件归档
- [ ] 再做 `WP-07` 受控回流与回流事件
- [ ] 最后做 `WP-08` 简版运营入口

## 7. 验收检查

- [ ] 任务可创建并入队
- [ ] 任务可调度执行 1688 适配器
- [ ] 执行过程可追踪快照、映射版本和适配器版本
- [ ] 执行失败可定位并可重试
- [ ] 工件可回查
- [ ] 回流事件可重试
- [ ] 简版运营入口可查看状态和失败原因

## 8. 协作检查

- [ ] `.trae/specs/platform-mediation-platform/` 继续作为草案层
- [ ] `docs/` 作为正式真源层
- [ ] 新的重要决策同步写入 `docs/decisions/`
- [ ] 跨项目可复用经验后续再沉淀到 `team-playbook`
