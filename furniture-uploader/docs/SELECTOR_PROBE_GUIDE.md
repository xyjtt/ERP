# Selector Probe Guide

When real page selectors are still unknown, use the selector probe tool to export visible elements from the current page.

## Command

```bash
python rpa/selector_probe.py --url "https://www.erp321.com/login.aspx?refer=https%3A%2F%2Fwww.erp321.com%2Fepaas"
```

Attach to an existing Chrome debug session:

```bash
python rpa/selector_probe.py --debugger-address 127.0.0.1:9222
```

Attach through DevTools only, without webdriver startup:

```bash
python rpa/devtools_probe.py --debugger-address 127.0.0.1:9222 --url-contains "offer-new.1688.com/popular/publish.htm"
```

Reuse a local Chrome profile:

```bash
python rpa/selector_probe.py --user-data-dir "C:/Users/Administrator/AppData/Local/Google/Chrome/User Data" --profile-directory Default
```

Recommended 1688 workflow:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_debug_chrome.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\capture_1688_pages.ps1
```

Recommended workflow for Edge debug sessions:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_debug_edge.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\capture_1688_pages.ps1 -Browser edge -ProbeMode devtools
```

Generate and merge selector suggestions into `config/platforms/1688.local.json` during the same capture flow:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\capture_1688_pages.ps1 -UpdatePlatformLocalConfig
```

If you want the new probe result to overwrite existing selector values in the local config:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\capture_1688_pages.ps1 -UpdatePlatformLocalConfig -ReplacePlatformLocal
```

Run `doctor` immediately after updating the local config:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\capture_1688_pages.ps1 -UpdatePlatformLocalConfig -RunDoctorAfterUpdate
```

If you need to point the debug browser at an existing absolute Chrome profile path, pass `-ProfileDir` to `start_debug_chrome.ps1`.

For Edge sessions, `-ProbeMode devtools` avoids local `msedgedriver` download issues and captures directly through the remote debugging socket.

Workflow:

1. the browser opens
   or attaches to an existing Chrome session
2. log in manually
3. navigate to the exact target page or popup
4. press Enter in the terminal
5. the tool exports JSON, screenshot, and HTML snapshot

## Output

Default output files:

- `logs/selector_probe/selector_probe.json`
- `logs/selector_probe/selector_probe.png`
- `logs/selector_probe/selector_probe.html`

The JSON includes:

- current URL
- page title
- visible element count
- visible `input / textarea / select / button / a / img / role=button` elements
- selector hints based on `id / name / placeholder / class`
- nearby `label_text`
- nearby `parent_text`
- `dom_path_hint`

After capture, generate selector suggestions:

```bash
python rpa/suggest_selectors.py --probe-json logs/selector_probe/1688/publish_detail_page.json
```

Write the top-ranked suggestions directly into the local 1688 override file:

```bash
python rpa/suggest_selectors.py --probe-json logs/selector_probe/1688/publish_detail_page.json --platform-local-output config/platforms/1688.local.json
```

This writes a `*.suggestions.json` file next to the probe JSON and ranks likely selectors for:

- `title`
- `price`
- `quantity`
- `category`
- `brand`
- `material`
- `main_image`
- `detail_images`
- `description`
- `ship_from_template`
- `freight_template`
- `ship_time_template`
- `submit_selector`

The ranking now also uses:

- `label_text`
- `parent_text`
- `aria-label`
- `data-name`
- `data-testid`
- `dom_path_hint`

When `--platform-local-output` points at an existing file, the tool keeps non-empty selectors that are already confirmed and only fills blank entries. If you want to replace existing selector values with the new probe result, add:

```bash
python rpa/suggest_selectors.py --probe-json logs/selector_probe/1688/publish_detail_page.json --platform-local-output config/platforms/1688.local.json --replace-platform-local
```

## Suggested Usage

Run once for each critical page:

- 聚水潭“上架到店铺”弹窗
- 聚水潭“匹配商品资料”弹窗
- 1688 商品发布页
- 1688 发布成功结果页

Then fill the discovered selectors into:

- `config/systems/jushuitan.local.json`
- `config/platforms/1688.local.json`

## Example Custom Output

```bash
python rpa/selector_probe.py --output logs/selector_probe/jushuitan_match.json --screenshot logs/selector_probe/jushuitan_match.png
```
