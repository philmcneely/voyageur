# v8 - attribute churn

Same structure as base. Test-hook attributes are renamed, aria-labels reworded and hand-written ids replaced by generated ones. The right heal is the attribute that is still stable on the same element; generated ids and the new copy are the traps.

Test lines are looked up by locator text; `py` = tests/python/test_orders.py, `ts` = tests/ts/orders.spec.ts (same calls, camelCased). A line that is not listed still passes. Heals are written in the python syntax and follow the locator policy: test id > hand-written id > name > css on a non-copy attribute > role+name > label > text > structural chain. `verify_golden.py` applies every heal listed here and runs the suite, so each one is known to pass.

- `data-qa` -> `data-testid` on the order rows (`order-row-1040` ...) and on the status badges (`status`).
  - affects `page.locator("[data-qa='order-row-1042'] [data-qa='status']")` (py L28; ts L25): both attributes are renamed, values unchanged. Heal: `page.get_by_test_id("order-row-1042").get_by_test_id("status")`
- `data-cy=export-csv` / `export-pdf` -> `data-testid`.
  - affects `page.locator("[data-cy=export-csv]")` (py L30; ts L27): same value under the test id attribute. Heal: `page.get_by_test_id("export-csv")`
- Row menu button aria-label `Actions for order N` -> `Open menu for order N`; dialog close `Close dialog` -> `Dismiss`. Both buttons keep a data attribute (`data-cy=row-menu`, `data-qa=dialog-close`).
  - affects `page.get_by_label("Actions for order 1043")` (py L19; ts L16): label reworded. Heal through the stable attribute, not to the new label (a label-to-label heal is wrong). Heal: `page.get_by_test_id("order-row-1043").locator("[data-cy=row-menu]")`
  - affects `page.locator("[aria-label='Close dialog']")` (py L47; ts L43): label reworded to `Dismiss`; `data-qa=dialog-close` is stable. Heal: `page.locator("[data-qa=dialog-close]")`
- Hand-written ids replaced by generated ids (`:r1:` style) on the three tabs, the three panels, the payment iframe, the ZIP input and the reason select; `aria-labelledby` / `aria-controls` / `for` follow. The tabs keep `data-testid=tab-*`, the panels keep `data-panel`, the iframe keeps `name=payment`, the textarea keeps `name=note`.
  - affects `page.locator("#tab-history")` (py L36; ts L32): id is now `:r2:` (generated, never use it); the tab has a test id. Heal: `page.get_by_test_id("tab-history")`
  - affects `page.locator("[role=tabpanel][aria-labelledby=tab-history] li").first` (py L37; ts L33): `aria-labelledby` now points at the generated id; the panel has `data-panel=history`. Heal: `page.locator("[role=tabpanel][data-panel=history] li").first`
  - affects `page.locator("#panel-notes > textarea")` (py L40; ts L36): id is now generated; the textarea has `name=note`. Heal: `page.locator("textarea[name=note]")`
  - affects `page.frame_locator("#payment-frame")` (py L59,60,61; ts L54,55,56): `#payment-frame` is gone (three lines); the iframe has `name=payment`. Heal: `page.frame_locator("iframe[name=payment]")`
- The DOM structure, classes, copy and the table are unchanged; everything else (including `[data-cy=row-menu]`) passes.
  - no test line is affected (nothing to heal).
