# Selector Probe Guide

When real page selectors are still unknown, use the selector probe tool to export visible elements from the current page.

## Command

```bash
python rpa/selector_probe.py --url "https://www.erp321.com/login.aspx?refer=https%3A%2F%2Fwww.erp321.com%2Fepaas"
```

Workflow:

1. the browser opens
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
