# Project Handover

更新时间：2026-03-24

## 项目定位

- 项目名：`furniture-uploader`
- 当前主线：`1688` 店铺后台自动上架
- 当前方式：`Excel/CSV -> Python/Selenium -> 浏览器 RPA`
- 当前策略：默认人工复核，支持后续切换到草稿或提交

## 当前已经可用的能力

- 1688 发布页自动填表
- 类目自动选择
- 主图/详情图上传
- 描述写入 TinyMCE
- 发布前错误检查
- smoke 样本干跑

## 当前最重要的未完成项

1. 真实草稿按钮的 live 选择器确认
2. 真实提交成功后的链接/ID 验证
3. 自动草稿或自动提交的最终生产验证

## 接手建议

1. 先看 [PROJECT_MEMORY.md](D:/script_files/ERP/furniture-uploader/docs/PROJECT_MEMORY.md)
2. 再看 [DIRECT_1688_PROGRESS.md](D:/script_files/ERP/furniture-uploader/docs/DIRECT_1688_PROGRESS.md)
3. 然后看 [DELIVERY_HANDOVER_2026-03-24.md](D:/script_files/ERP/furniture-uploader/docs/DELIVERY_HANDOVER_2026-03-24.md)
4. 真正开始改代码前，跑一遍单测和 `doctor`

## 验证命令

```bash
python -m unittest discover -s tests -p "test_*.py"
python rpa/main.py --system 1688_direct --platform 1688 --file templates/1688_corner_table_smoke.csv --doctor
python rpa/main.py --system 1688_direct --platform 1688 --file templates/1688_corner_table_smoke.csv --limit 1 --skip-login
```
