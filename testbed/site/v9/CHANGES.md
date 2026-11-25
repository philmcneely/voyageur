# v9 - mixed

A few changes of every kind: a wrapper, a regenerated class, attribute and copy changes, one element removed (expect NONE), one moved into the iframe and one into a shadow root. Several lines must NOT be touched.

Test lines are looked up by locator text; `py` = tests/python/test_orders.py, `ts` = tests/ts/orders.spec.ts (same calls, camelCased). A line that is not listed still passes. Heals are written in the python syntax and follow the locator policy: test id > hand-written id > name > css on a non-copy attribute > role+name > label > text > structural chain. `verify_golden.py` applies every heal listed here and runs the suite, so each one is known to pass.

- Structure: the filter chips are wrapped in a `div.chip-group`.
  - affects `page.locator("div.toolbar div.filters > button:nth-of-type(2)")` (py L25; ts L22): `div.filters > button` has no direct button children any more; the classes are unchanged. The chip has `data-filter`. Heal: `page.locator("[data-filter=paid]")`
- Class: the generated class of the Cancel order button changes (`sc-fzXfKy jQdBvX` -> `sc-dkzDqf kLmNoP`).
  - affects `page.locator("button.sc-fzXfKy.jQdBvX")` (py L43,49; ts L39,45): the build regenerated the class; nothing stable but role + name. Heal: `page.get_by_role("button", name="Cancel order")`
- Attribute: the status hook value `data-qa=status` -> `data-qa=order-status` (rows keep `data-qa=order-row-N`); the history tab id becomes generated (`:r3:`, `data-testid=tab-history` stays) and so does the panel's aria-labelledby; the close button aria-label `Close dialog` -> `Close` (`data-qa=dialog-close` stays).
  - affects `page.locator("[data-qa='order-row-1042'] [data-qa='status']")` (py L28; ts L25): the value moved to `order-status`; the row hook is unchanged. Heal: `page.locator("[data-qa='order-row-1042'] [data-qa='order-status']")`
  - affects `page.locator("#tab-history")` (py L36; ts L32): generated id, never use it. Heal: `page.get_by_test_id("tab-history")`
  - affects `page.locator("[role=tabpanel][aria-labelledby=tab-history] li").first` (py L37; ts L33): `aria-labelledby` is generated now; `data-panel=history` is stable. Heal: `page.locator("[role=tabpanel][data-panel=history] li").first`
  - affects `page.locator("[aria-label='Close dialog']")` (py L47; ts L43): label reworded; heal through `data-qa=dialog-close`, not to `Close`. Heal: `page.locator("[data-qa=dialog-close]")`
- Copy: customer `Echo GmbH` -> `Echo Group GmbH`.
  - affects `page.get_by_role("row").filter(has_text="Echo GmbH").get_by_role("checkbox")` (py L23; ts L20): `has_text="Echo GmbH"` matches no row. The row has `data-qa=order-row-1044`. Heal: `page.locator("[data-qa='order-row-1044']").get_by_role("checkbox")`
- Removed: the Export CSV button is gone (Export PDF is still there; it is NOT a substitute).
  - affects `page.locator("[data-cy=export-csv]")` (py L30; ts L27): the element no longer exists; the right answer is NONE, never `export-pdf`. Heal: NONE: the element is gone, delete the step (no substitute)
- Moved into the payment iframe: the Billing ZIP input leaves the page and sits in `payment.html` (the iframe) with the same `name=zip`. The iframe grows taller.
  - affects `page.locator("input[name=zip]")` (py L58; ts L53): the input is now inside `#payment-frame`. Heal: `page.frame_locator("#payment-frame").locator("input[name=zip]")`
- Moved into a shadow root: the notes textarea becomes a `<note-editor>` custom element with an open shadow root (same `name=note`).
  - affects `page.locator("#panel-notes > textarea")` (py L40; ts L36): `#panel-notes > textarea` has no direct child. Playwright CSS pierces the open shadow root, so `textarea[name=note]` works as is. Heal: `page.locator("textarea[name=note]")`
- Unchanged: the table, columns, row order, the activity grid, the menu, the dialog, the payment frame id, the flag component.
  - no test line is affected (nothing to heal).
