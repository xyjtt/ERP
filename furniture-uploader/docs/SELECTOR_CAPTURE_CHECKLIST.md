# Selector Capture Checklist

## Goal

Capture the minimum real-page selectors needed to move this project from framework-ready to live page debugging.

## Jushuitan

Fill these keys in [jushuitan.json](../config/systems/jushuitan.json):

- `select_platform_1688.selector`
  The button/card for the 1688 platform in the batch publish dialog.
- `search_store_name.selector`
  The store search input box.
- `click_store_search.selector`
  The store search button.
- `match_history_candidate.row_selector`
  Each candidate row in the "匹配商品资料" popup.
- `match_history_candidate.title_selector`
  Candidate title text inside a row.
- `match_history_candidate.store_name_selector`
  Candidate store name text inside a row.
- `match_history_candidate.source_id_selector`
  Candidate source/material id text inside a row.
- `match_history_candidate.use_button_selector`
  The "使用该资料" button inside a row.
- `match_history_candidate.fallback_selector`
  The "跳过，自己编辑" button.
- `match_history_candidate.invalid_error_detection.message_selector`
  Popup/error message when a candidate is invalid.
- `match_history_candidate.invalid_error_detection.close_selector`
  Close button for that popup.

## 1688

Fill these keys in [1688.json](../config/platforms/1688.json):

- `title.selector`
- `price.selector`
- `quantity.selector`
- `main_image.selector`
- `description.selector`
- `ship_from_template.selector`
- `freight_template.selector`
- `ship_time_template.selector`
- `sanitize_spec_inputs.selector`
  A container around the spec/SKU section if available.
- `assert_no_publish_errors.error_detection.message_selector`
- `assert_no_publish_errors.error_detection.close_selector`
- `submit_selector`
- `success_extractors[0].selector`
  Link id or item id source.
- `success_extractors[1].selector`
  Link URL source.

## Capture Tips

- Prefer stable attributes first: `id`, `name`, `data-*`.
- Use `css` selectors before `xpath` unless the page forces xpath.
- If the page is inside an iframe, note that separately.
- For repeating rows, capture the row selector first, then child selectors relative to the row.
- When there are multiple similar buttons, target visible text or the closest stable container.

## Validation Command

Run this before live testing:

```bash
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --doctor
```

For machine-readable output:

```bash
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --doctor --doctor-json
```

To block execution until required selectors are filled:

```bash
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --strict-config
```

To initialize local selector override files:

```bash
python rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --init-local-config
```

## Local Override Files

You can avoid editing shared config files directly by creating local override files beside them:

- `config/operator_config.local.json`
- `config/systems/jushuitan.local.json`
- `config/platforms/1688.local.json`

These files will be deep-merged over the base config at runtime.
