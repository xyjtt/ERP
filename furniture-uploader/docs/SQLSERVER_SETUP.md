# SQL Server 2012 接入说明

## 目标

项目当前使用 SQL Server 2012 作为共享知识库与日志库，主要承载：

- 发布成功结果
- 发布失败日志
- 匹配商品资料候选历史
- 店铺默认模板

## 初始化

执行建表脚本：

- [sql/001_init_sqlserver.sql](/d:/script_files/furniture-uploader/sql/001_init_sqlserver.sql)

## 推荐配置方式

不要把生产密码直接写入共享仓库。

推荐做法：

1. 复制 `config/database.example.json` 为本机私有文件，例如 `config/database.local.json`
2. 在环境变量中设置数据库密码
3. 运行时通过 `--db-config` 指向本地私有文件

PowerShell 示例：

```powershell
$env:FURNITURE_UPLOADER_DB_PASSWORD='你的数据库密码'
```

`config/database.example.json` 当前采用 `password_env` 方式读取密码。

## 推荐驱动

- `ODBC Driver 17 for SQL Server`

如果本机使用其他 SQL Server ODBC 驱动，可在本地数据库配置文件中调整 `driver`。

## 自检命令

检查数据库配置与连通性：

```bash
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --check-db --db-config config/database.local.json
```

输出 JSON：

```bash
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --check-db --db-config config/database.local.json --check-json
```

## 当前表用途

- `publish_task`: 任务主表，记录 running / success / failed 状态流转
- `publish_result`: 发布成功结果与链接信息
- `publish_error_log`: 失败原因、截图、HTML 快照
- `match_candidate_history`: 候选资料成功、失效、回退记录
- `category_mapping_history`: 类目关键字到平台类目的成功/失败历史
- `store_default_template`: 店铺默认发货模板、运费模板、尺寸重量，支持成功后自动回写

## 备注

当前代码里没有把你提供的明文数据库密码写入共享配置。部署时只需要在本地环境变量或本地私有配置中补上即可。
