# jushuitan-link-ops

聚水潭链接运维脚本项目。

当前第一阶段只做一件事：
- 批量修改线上商品编码

项目定位：
- 不再走开放接口修改“线上商品编码”
- 直接通过聚水潭 Web 页面执行批量修改
- 支持 Excel、API、手动输入三种任务来源
- 执行后自动回查旧编码剩余数量
- 写入 SQL Server 任务、结果、异常日志

## 目录

- `config/` 运行配置
- `docs/` 方案和流程文档
- `rpa/` 主代码
- `scripts/` 打包脚本
- `sql/` SQL Server 建表脚本
- `templates/` Excel 模板
- `tests/` 单元测试

## 快速命令

环境检查：

```powershell
python main.py --check-env
```

初始化数据库配置：

```powershell
python main.py --init-db-config
```

检查 selector 配置：

```powershell
python main.py --doctor
```

只做数据校验：

```powershell
python main.py --source excel --file templates\code_change_template.csv --validate-only
```

执行任务：

```powershell
python main.py --source excel --file templates\code_change_template.csv --db-config config\database.local.json
```

打包：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1
```

## 说明

- `config/operations/code_change.json` 默认保留了 selector 占位，未配置时会走人工兜底。
- 正式无人值守前，需要先用 `rpa/selector_probe.py` 或手工补全 `.local.json` selector。
- 数据库密码默认不写入仓库，建议放 `config/database.local.json` 或环境变量。
