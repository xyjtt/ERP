# Go-Live Checklist

更新时间：2026-03-24

## 当前已经完成

- Python 依赖安装
- 本地 bootstrap
- 环境检查
- 单元测试
- selector probe 工具
- 1688 基线 selector 补齐
- 类目路径自动化
- 文本/图片/详情 live 干跑
- smoke 模板准备
- 草稿模式代码支持

## 上线前必须完成

### 1. 浏览器与账号

- 1688 后台账号可稳定登录
- 本机调试浏览器可接管
- 如需长期运行，确认独立浏览器 profile

### 2. 配置

- `config/operator_config.local.json` 已填好浏览器接管信息
- `config/platforms/1688.local.json` 如有覆盖，已确认不覆盖有效基线
- 如需数据库写回，已填好 `config/database.local.json`

### 3. 页面能力验证

- 类目选择成功
- 主图上传成功
- 详情图上传成功
- 描述写入成功
- 运费/发货模板填写成功
- 当前类目下的可选属性行为已确认

### 4. 最终动作验证

- 真实草稿按钮选择器已确认
- 已完成一次真实草稿保存
- 已完成一次真实正式提交
- 提交成功后的链接/ID 已被脚本提取

### 5. 稳定性

- 单测通过
- `doctor` 通过
- 至少连续 3 次干跑无结构性报错
- 失败时截图和 HTML 快照可用

## 当前未完成的关键上线项

- 真实草稿按钮选择器确认
- 真实提交成功结果确认
- 自动提交前的最终风险复核

## 推荐验证命令

```bash
python -m unittest discover -s tests -p "test_*.py"
python rpa/main.py --system 1688_direct --platform 1688 --file templates/1688_corner_table_smoke.csv --doctor
python rpa/main.py --system 1688_direct --platform 1688 --file templates/1688_corner_table_smoke.csv --validate-only
python rpa/main.py --system 1688_direct --platform 1688 --file templates/1688_corner_table_smoke.csv --limit 1 --skip-login
```
