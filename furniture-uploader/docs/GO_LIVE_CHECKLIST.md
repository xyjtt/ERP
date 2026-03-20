# Go-Live Checklist

## Already Completed

- Python runtime dependencies installed
- local bootstrap script available
- local DB config template generation available
- local operator private config template generation available
- selector local override template generation available
- environment preflight available
- database preflight available
- offline unit tests available
- publish task / result / error / match history / category history / store template persistence implemented
- emoji sanitization before publish implemented
- selector probe tool available

## Must Be Completed Before Real Publishing

### 1. Fill Real Selectors

Required files:

- `config/systems/jushuitan.local.json`
- `config/platforms/1688.local.json`

Minimum required selectors still missing:

- 聚水潭平台选择
- 聚水潭店铺搜索
- 聚水潭匹配商品资料弹窗
- 1688 标题输入框
- 1688 价格输入框
- 1688 库存输入框
- 1688 主图上传控件

Recommended helper:

```bash
python rpa/selector_probe.py --url "https://www.erp321.com/login.aspx?refer=https%3A%2F%2Fwww.erp321.com%2Fepaas"
```

### 2. Use Real Database Password

Current DB preflight result proves:

- driver selection is correct
- target SQL Server is reachable enough for driver negotiation
- current failure is credential failure for `sa`

You must set the real password:

```powershell
$env:FURNITURE_UPLOADER_DB_PASSWORD='真实密码'
```

Then run:

```bash
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --check-db --db-config config/database.local.json
```

### 3. First Real Dry Run

Recommended order:

1. `python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --check-env`
2. `python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --doctor`
3. `python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --validate-only --db-config config/database.local.json`
4. `python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --limit 1 --db-config config/database.local.json`

## Current Definition of Done

From a software-engineering perspective, the project scaffold is complete enough for real-world integration.

The remaining blockers are not missing code paths; they are missing production-specific runtime facts:

- real page selectors
- real database password / account availability
- first live browser walkthrough
