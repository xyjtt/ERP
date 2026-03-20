# Local Setup

## Quick Start

Run the PowerShell bootstrap script from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_local.ps1
```

The script will:

- install `requirements.txt`
- create `config/database.local.json` if missing
- create `config/operator_config.local.json` if missing
- create selector local override templates if missing
- run `--check-env`
- run `--doctor`
- run unit tests
- run `--check-db` when local DB config exists

## Optional Flags

Skip package installation:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_local.ps1 -SkipInstall
```

Skip database connectivity checks:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_local.ps1 -SkipDbCheck
```

Use a custom Python executable:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_local.ps1 -PythonExe py
```

Use a custom database config path:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_local.ps1 -DbConfigPath config\database.test.local.json
```

## Manual Command Equivalents

```bash
python -m pip install -r requirements.txt
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --init-db-config --db-config config/database.local.json
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --init-operator-local-config
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --init-local-config
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --check-env
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --doctor
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --check-db --db-config config/database.local.json
```
