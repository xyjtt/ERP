# 1688 SKU Offline Live Update 2026-03-25

## Status

- Store session confirmed valid for `速班达家居:it`.
- The browser flow can now:
  - open the 1688 management page
  - assert the current store context
  - open the edit page directly by `product_id`
  - locate the target SKU row by cargo-number input
  - read and toggle the online/offline switch
  - click the real submit button `submitFormButton`

## Verified Finding

- Product `963374911361`
- SKU `SZ018003N371V01`
- Live submit result:
  - the page returns `加工方式不能为空`
  - the SKU remains online because the page refuses to persist the change

## Engineering Decision

- Treat this case as `business_validation`, not as a selector failure or generic Selenium failure.
- Capture the validation message in run reports and DingTalk failure notifications.
- Do not use this product as the final success-case validation target unless its base product data is fixed first.

## Success Case Verified

- Product `1035309130864`
- SKU `SJ023214N956V01`
- Real live result:
  - switch state changed from `上架` to `下架`
  - submit completed
  - post-submit verification confirmed `aria-checked=false`
  - the same SKU is now detected as `already_offline` on a follow-up execution pass

## Next Step

- Use another explicitly approved online SKU in the test store for the final end-to-end offline-submit validation.
