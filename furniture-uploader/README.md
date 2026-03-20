# furniture-uploader

`furniture-uploader` 是家具铺货自动化项目。

当前第一阶段目标：

- 操作入口：聚水潭 Web
- 首个渠道：1688
- 任务来源：Excel
- 执行形态：Python + Selenium
- 数据留痕：SQL Server

## 当前建议入口

如果是接手项目，先看：

1. `docs/README.md`
2. `docs/handoff/README.md`
3. `docs/交接文档_2026-03-20.md`
4. `docs/roadmap/README.md`

## 当前项目定位

当前主线是：

- 聚水潭家具铺货自动化
- Excel 模板驱动
- Web RPA 执行链路

说明：

- 当前还不是无人值守稳定生产态
- 当前重点仍然是 selector、环境和数据库联调

## 目录结构

```text
config/
docs/
rpa/
scripts/
sql/
templates/
tests/
```

## 常用命令

```bash
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --doctor
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --check-env
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --check-db --db-config config/database.local.json
python -m unittest discover -s tests -p "test_*.py"
```

## 当前文档分层规则

- `docs/handoff/`
  - 正式交接文档
- `docs/architecture/`
  - 总体方案、系统设计
- `docs/operations/`
  - 本地部署、联调、上线检查
- `docs/changelog/`
  - 每次变更记录
- `docs/roadmap/`
  - 后续任务和优先级
- `docs/research/`
  - 参考资料和额外调研
