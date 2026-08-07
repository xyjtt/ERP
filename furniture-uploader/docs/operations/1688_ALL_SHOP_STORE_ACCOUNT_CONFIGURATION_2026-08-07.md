# 1688 下架/替换全店账号配置扩展（2026-08-07）

## 结论

- 基线：`08d22fc3888aed720037a294776d1a0fc315dd89`。
- authoritative inputs：执行机外置 `accounts.json`、`account_runtime_migration_roster.json`、`enabled=20` 只读 Preview。
- 正式配置从 4 家扩展到 17 家；替换配置通过 `extends=1688_sku_offline.json` 复用同一映射。
- 本次只修改代码仓库配置，没有启动浏览器、创建任务或操作生产数据库。

## 可唯一推出并新增的 13 家

`xiangpei_main`、`laijuke`、`famei`、`feitan_shaoyou`、`huazhixin`、`linjing`、`fanshe`、`banbanshun`、`yuezhai`、`jiukuo`、`jinhemeng`、`gutu_baiqian`、`qianzhishang_qiyiguo`。

每条映射只使用以下权威字段：

- `account_key`、`expected_member_id`、`shop_id`、`profile_key`、`browser_profile_dir` 来自外置 `accounts.json`。
- `task.shop_name` 来自任务迁移 roster，并要求它同时存在于外置账号 `shop_names`。
- `store_name` 来自只读 Preview 中唯一匹配的 `app.op_stop_sale.dpmc`。
- `store_aliases` 只复制外置账号 `shop_names` 和 roster 的 `task.shop_name`，不补人工猜测值。

## 继续 fail-closed 的 3 家

| account_key | 原因编码 | 精确缺口 | 允许的后续动作 |
|---|---|---|---|
| `pingcan` | `member_identity_collision` | 与 `pingcan_rpa` 共用 `expected_member_id=b2b-2221733989984a3731`、同一 `shop_id` 和源店铺 `阿里巴巴-常州平灿家居有限公司` | 业务必须指定唯一 ERP owner；在此之前两个账号都不加入 `store_accounts` |
| `pingcan_rpa` | `member_identity_collision` | 与 `pingcan` 共用同一 member/shop/source 身份 | 同上，禁止脚本自行选择 |
| `xinbaiguang_shanzhu` | `jushuitan_store_mapping_missing` | roster 只有 1688 名称 `新佰广1688`；聚水潭代码要求精确店铺名，当前没有权威 `jushuitan_store_name` | 取得聚水潭真实精确店铺名后新增独立字段和验证，禁止把 1688 名称改写或猜成聚水潭名称 |

## 同步器

`scripts/sync_1688_store_accounts_from_roster.py` 默认 dry-run，并校验：

1. accounts revision/hash 与 Preview 完全一致。
2. enabled 账号与非 disabled task roster 集合完全一致。
3. `account_key + task.shop_name + member + profile + source store` 均唯一且相互一致。
4. Preview 必须是 `read_only_preview`，且 write/task/browser 标志均为 false。
5. 实际 blocker 必须与 `1688_store_account_roster_policy.json` 完全一致；出现新增、消失或原因变化均失败关闭。
6. `--apply` 仅原子更新指定的本地 JSON；不会调用浏览器、数据库或任务入口。

本次真实 dry-run：`enabled=20`、`configured_before=4`、`added=13`、`blocked=3`、`configured_after=17`。证据见同目录 JSON。

## 部署范围

可以部署的范围：配置、同步器、policy 和测试。部署后 17 家可被下架和替换入口解析到正确 `account_key/Profile`。

不能据此宣布生产可执行：本次快照中这 17 家均为 `waiting_auth`。部署后必须逐店运行正式 preflight/账号 RPA 登录并核对 member 和店铺；不得直接启动全店生产。

`pingcan`、`pingcan_rpa`、`xinbaiguang_shanzhu` 不在部署后的正式映射中，源表出现这三家时仍会由 `require_store_account_mapping=true` 安全停止，不阻塞其他已配置店铺。
