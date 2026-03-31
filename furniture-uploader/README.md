# furniture-uploader

`furniture-uploader` 是一个面向电商铺货场景的 RPA 自动化项目，当前主线目标是先跑通 `1688` 店铺后台的自动上架，并复用同一套 Selenium 底座逐步扩展到 `SKU 下架`、聚水潭协同链路和更多平台。

## 当前状态

- 当前主线：`1688_direct`
- 新增能力：`1688_sku_offline`
- 数据源：`Excel / CSV`
- 执行方式：`Python + Selenium`
- 浏览器接管：支持连接已登录的 `Edge/Chrome` 调试会话
- 当前默认策略：`自动填表 + 人工复核`，支持后续切换为 `保存草稿` 或 `正式提交`

## 已完成的核心能力

- 1688 发布页核心字段自动填写
- 类目自动选择
- 主图上传
- 详情图上传并写入 TinyMCE
- 商品描述写入 TinyMCE
- 发布前错误检测
- 成功结果提取框架
- 1688 smoke 干跑样本
- 1688 SKU 下架任务预览 / 执行 / 扫描入口
- 单元测试与 `doctor` 配置体检

## 当前交付边界

已经可以稳定完成：

- 打开已登录 1688 发布页
- 自动填写商品内容
- 自动走类目路径
- 自动上传图片和详情
- 自动跳过当前类目下不存在的可选属性
- 停在人工复核节点

还没有完成最终生产闭环的部分：

- 真实“保存草稿”按钮的 live 选择器确认
- 真实“提交发布”后的成功页结果确认
- 自动提交模式的最终上线验证
- 1688 下架流程的 live 选择器联调确认
- 多店铺会话映射

## 推荐阅读顺序

1. [docs/README.md](D:/script_files/ERP/furniture-uploader/docs/README.md)
2. [docs/PROJECT_MEMORY.md](D:/script_files/ERP/furniture-uploader/docs/PROJECT_MEMORY.md)
3. [docs/DIRECT_1688_PROGRESS.md](D:/script_files/ERP/furniture-uploader/docs/DIRECT_1688_PROGRESS.md)
4. [docs/DELIVERY_HANDOVER_2026-03-24.md](D:/script_files/ERP/furniture-uploader/docs/DELIVERY_HANDOVER_2026-03-24.md)
5. [docs/AI_CONTINUITY_GUIDE.md](D:/script_files/ERP/furniture-uploader/docs/AI_CONTINUITY_GUIDE.md)

## 常用命令

```bash
python -m unittest discover -s tests -p "test_*.py"
python rpa/main.py --system 1688_direct --platform 1688 --file templates/1688_corner_table_smoke.csv --doctor
python rpa/main.py --system 1688_direct --platform 1688 --file templates/1688_corner_table_smoke.csv --validate-only
python rpa/main.py --system 1688_direct --platform 1688 --file templates/1688_corner_table_smoke.csv --limit 1 --skip-login
python rpa/sku_offline_main.py --mode preview --file templates/1688_sku_offline_sample.csv
```

## 目录结构

```text
config/
docs/
rpa/
scripts/
templates/
tests/
```

## 说明

- `docs/` 下的部分早期文件保留为历史档案
- 当前以 `docs/README.md` 中列出的“当前有效文档”为准
- 1688 项目总入口见 [D:\script_files\1688](D:/script_files/1688/README.md)
