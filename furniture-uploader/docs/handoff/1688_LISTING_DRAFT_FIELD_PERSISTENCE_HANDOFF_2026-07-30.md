# 1688 上架草稿字段持久化修复交接（2026-07-30）

> 本文档只记录当前事实，严格区分「代码已改」「本地测试」「真实页面」「正式库」「执行机部署」五个层面。
> 未在真实页面验证的内容，一律不写成已完成。

## 1. 背景与当前事实

- 业务对象：CTG0286（源 SKU `CTG028601N1416V01`），唯一草稿 ID `6a69bee6e4b01cad1b297a52`。
- 2026-07-29/30 真实页面证据：草稿保存请求返回 HTTP 200 且为同一草稿 ID（未新建草稿、未 submit），但服务器侧以下字段未持久化：
  - 主图只剩 1 张（要求 4 张）；
  - 24小时发货（买家保障）未持久化；
  - 发货地址未持久化；
  - 物流尺寸/重量未持久化。
- 标题、规格（颜色 `胡桃色`、尺寸 `48/40/50`）、价格、库存、详情内容已在真实页面确认持久化。
- 正式库状态：保持 `blocked/rejected`，最新事件 `draft_verification_failed`，Offer 数量为 0。审批门禁保持关闭。

## 2. 代码层面根因（已定位）

1. 草稿保存使用 `identity_only` 补丁模式：保证写回同一草稿，但不会把发货地址、物流、买家保障和完整主图补进请求体。
2. `full` 补丁模式此前不携带原草稿编辑身份（draftId/edit/isItemEdit），直接启用会有新建草稿风险。
3. 主图桥接上传始终写第 1 个槽位，多图互相覆盖。
4. 保存前校验与独立验收把「计划在 submit 时重填」误判为「草稿已合格」（`submit_reapply_nonpersistent_fields` 兜底）。

## 3. 已完成代码修复（工作树未提交，共 7 个文件）

工作树：`D:\script_files\ERP_auto_listing_spec_fix_20260728`（分支 `codex/1688-spec-selector-20260728`，基于 `b7021e7`）。

1. `furniture-uploader/config/platforms/1688.json`
   - `draft_request_patch_retry_modes` 改为 `["full", "identity_only"]`：已有草稿在 full 模式同时携带编辑身份，失败时回退 identity_only 保护；
   - 草稿校验新增 `minimum_main_image_count=4`；
   - 新增 `require_logistics_dimensions/strict_logistics_persist/required_logistics_fields`（长/宽/高/重，来源 length_cm/width_cm/height_cm/weight_g）；
   - `main_image` 步骤改为 `source=main_images`、`max_files=4`、`minimum_image_count=4`；
   - `submit_reapply_nonpersistent_fields` 清空：不再允许「submit 时重填」充当草稿合格依据。
2. `furniture-uploader/rpa/variant_pipeline.py`
   - 产品载荷保留完整 `main_images/main_images_remote` 列表（此前只取第 1 张）。
3. `furniture-uploader/rpa/browser_rpa.py`
   - full 补丁模式在存在 `expected_draft_id` 时同时注入草稿编辑身份（draftId/edit/isItemEdit）；
   - 主图桥接上传按槽位递增（slot 0-3），新增 `_limit_file_values` 按 `max_files` 截断；
   - 保存前阻断：主图数量不足 `minimum_main_image_count` 时禁止派发保存请求；
   - 保存后刷新校验：主图数量必须达标；`strict_logistics_persist` 下禁用 trace 兜底，且物流字段值必须与期望值逐项一致，不一致即阻断；
   - `_draft_main_image_state` 返回真实图片数量 `count`（state/DOM 双通道取大）。
4. `furniture-uploader/scripts/inspect_1688_saved_draft.py`
   - 独立只读验收改为真实字段判定：主图数量（按 payload 主图数，1-4）、发货地址必须真实存在、买家保障必须为 `24小时发货/essxsfh` 且 schedule 含 from=1 匹配项；
   - 移除「reapply 已记录即通过」的兜底逻辑。
5. 测试：`tests/test_browser_rpa_helpers.py`、`tests/test_inspect_1688_saved_draft.py`、`tests/test_variant_pipeline.py` 新增上述行为的回归覆盖。

## 4. 本地测试证据（2026-07-30，本机）

- 上架全量回归：`py -3 -X utf8 -m unittest discover -s tests -p "test_*.py"` → **432/432 OK**（修复前基线 425）。
- 聚焦回归：`tests.test_variant_pipeline tests.test_browser_rpa_helpers tests.test_inspect_1688_saved_draft` → 168/168 OK。
- 标题引擎（`title_engine/`）：**89/89 OK**。
  - 注意：本机系统 Python 曾缺声明依赖 `jieba`（requirements.txt 中 `jieba>=0.42.1`），缺分时 `test_real_shoe_cabinet_factual_pools_generate_thirty_candidates` 稳定失败（生成 8 字短标题）；安装 jieba 后全量通过。该失败与本次上架改动无关。
- Doctor：`status: ok`，仅保留既有 2 条空选择器警告（`sanitize_spec_inputs`、`extract_match_source_id`）。
- 测试环境：系统 Python 3.14.3（`py -3`）+ selenium 4.41.0 + jieba（本次补装）。

## 5. 真实页面状态（截至 2026-07-30 15:20）

- 官方草稿页已从 SYS_ERROR 恢复，可正常打开编辑。
- 已持久化：标题、规格、价格、库存、详情内容。
- 未持久化：主图（仅 1/4）、24小时发货、发货地址、物流尺寸/重量。
- 本轮代码修复尚未在真实页面执行；草稿未新建、未删除、未 submit。

## 6. 执行机部署状态

- 未部署。执行机仍运行旧版本；本轮修复只在开发工作树，未触碰执行机，未绕过浏览器互斥。

## 7. 下一步（严格按顺序）

1. 确认浏览器互斥/容量/源头门禁清空（`scripts/query_1688_listing_source.py` 等既有检查）。
2. 在受控会话中对草稿 `6a69bee6e4b01cad1b297a52` 执行**单次**真实草稿修复保存（full 模式携带编辑身份）。
3. 用 `scripts/inspect_1688_saved_draft.py` 对同一草稿做独立只读全字段验收（标题/价格/库存/主图 4 张/详情图/规格/物流/发货地址/24小时发货）。
4. 独立验收全部通过后，才允许：提交本轮代码、审批、单次 submit、执行机部署。
5. 任一环节失败：保持 blocked，回读证据，禁止重复保存制造新草稿。

## 8. 禁止事项（沿用既有约束）

- 禁止新建或删除草稿；禁止 submit；禁止整商品 ID 操作。
- 禁止触碰执行机与 `D:\script_files\ERP` 脏工作树。
- 禁止绕过浏览器互斥与全局锁流程。
- 凭据只走 Windows Credential Manager，不落地到代码、日志或证据文件。
