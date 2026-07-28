# Project Evolution

更新时间：2026-03-24

## 目的

记录项目的重要方向变化、能力演进、验证节点和策略调整。

## 演化时间线

### 2026-03-17

- 项目从泛化“家具铺货工具”收敛到先做 `1688` 最小可落地方案
- 保留未来接入聚水潭与多平台的结构设计
- 确定基础技术栈为 `Python + Selenium`
- 引入 SQL Server 作为长期结果沉淀和知识库方向

### 2026-03-18

- 完成基础脚手架
- 完成本地 bootstrap、环境体检、数据库预检
- 增加离线单元测试
- 增加 selector probe 工具

### 2026-03-20

- 明确第一阶段不追求无人值守生产
- 先做“人工登录 + 自动填表 + 人工复核”的 MVP

### 2026-03-23

- 1688 发布页 live selector 采集完成
- 主图上传切到页面 React bridge，稳定性明显提升
- 详情图上传通过同一桥接链路完成
- `title / price / quantity / main_image / detail_images / description` 完成真实 smoke

### 2026-03-24

- 类目路径自动化完成并通过 live 验证
- 成功结果提取支持 `current_url / body_text / page_source`
- 可选属性在当前类目不存在时可自动跳过
- 增加标准 smoke 模板
- 完成一次 `1688_direct --limit 1 --skip-login` 的真实干跑闭环
- 发布最终动作改为 3 模式：`manual / draft / submit`
- 增加草稿模式支持，但默认仍然保持保守的人工复核
- 建立 `D:\script_files\1688` 作为新的 1688 项目统一入口
- 在 `furniture-uploader` 内新增 `1688_sku_offline` 第一阶段代码骨架
- 把 1688 项目从“只做上架”扩展到“上架 + 下架共用一套 Selenium 底座”

## 当前阶段

- 阶段：pre-go-live
- 重点：把“真实草稿”和“真实提交成功”两条最终动作补齐

## 下一次必须记录的触发点

- 抓到真实草稿按钮并验证成功
- 完成一次真实提交
- 成功页结果提取闭环打通
- 自动提交进入可上线状态
- 完成一次真实 SKU 下架联调
## 2026-07-28 Draft Save Policy Tightening

- A successful draft HTTP response is no longer treated as proof that required page state persisted.
- Required listing fields are now checked before the save click and checked again after save plus refresh.
- SKU color and size use direct text entry with Enter; suggestion-list matching is advisory UI only and is not part of the data contract.
- Forced confirmation of a modal warning that specifications will be cleared is prohibited.

## 2026-07-28 Exact Specification Acceptance

- Saved-draft verification evolved from presence checks to exact business-value checks for required specifications.
- This prevents a stale platform value such as `红色100` from being accepted when the payload requires `胡桃色`.
- The same rule applies to size, including the CTG0286 value `48/40/50`.
