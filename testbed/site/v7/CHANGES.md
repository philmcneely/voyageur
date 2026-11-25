# v7 - restructure

Wrappers inserted, sibling order changed, table columns reordered, classes renamed to hashed CSS-module names. The structural, nth and class chains break; data-*, aria and ids stay, so the right heals move to those. Row text, order ids and statuses are unchanged, so no assertion changes.

Test lines are looked up by locator text; `py` = tests/python/test_orders.py, `ts` = tests/ts/orders.spec.ts (same calls, camelCased). A line that is not listed still passes. Heals are written in the python syntax and follow the locator policy: test id > hand-written id > name > css on a non-copy attribute > role+name > label > text > structural chain. `verify_golden.py` applies every heal listed here and runs the suite, so each one is known to pass.

- A banner and a `layout-root` wrapper are inserted around the shell, and the side column now comes BEFORE the main column in the DOM (a flex `row-reverse` keeps them where they were on screen).
  - affects `page.locator("#app > div > div:nth-child(2) > div.grid > div:nth-child(3) > span.cell")` (py L17; ts L14): `#app > div` is now the wrapper, its second child is the shell, and `.grid` is not a direct child of the second column; the classes are hashed too. The grid rows carry `data-activity` and grid row 3 is order 1041. Heal: `page.locator("[data-activity='1041']")`
- Table: the Total column now comes before Status in every row (header too), and a divider row `Earlier orders` sits between order 1041 and 1042. Row order and the order ids are unchanged.
  - affects `page.get_by_test_id("orders-table").locator("tbody tr").nth(3).locator("td").nth(2).locator("button")` (py L13; ts L10): the 4th body row is now order 1042 (the divider is row 3) and td 2 is Total, with no button: times out. The step wants order 1043's status button, so a positional edit (td 3) would click 1042's button and fail the `status.1043` assertion. Heal: `page.locator("[data-qa='order-row-1043'] [data-qa='status']")`
  - affects `page.locator("//table//tr[3]/td[2]")` (py L15; ts L12): tr[3] is now the divider row, which has no td: times out. The step wants order 1042's customer cell (tr[4] would pass but is the weakest heal). Heal: `page.locator("[data-qa='order-row-1042'] [data-col=customer]")`
- Filter chips reordered: All, Shipped, Paid, Refunded. Every class name is renamed to a hashed CSS-module name (`toolbar` -> `_toolbar_xxxxx`, `filters`, `chip`, ...).
  - affects `page.locator("div.toolbar div.filters > button:nth-of-type(2)")` (py L25; ts L22): the classes are gone and `button:nth-of-type(2)` would now be Shipped. A new structural chain would resolve but click the wrong chip and fail `filter: paid`; the chip carries `data-filter`. Heal: `page.locator("[data-filter=paid]")`
- The notes textarea is wrapped in two divs (`field-wrap`, `field-wrap-inner`).
  - affects `page.locator("#panel-notes > textarea")` (py L40; ts L36): `#panel-notes > textarea` has no direct child any more; the textarea keeps its `name`. Heal: `page.locator("textarea[name=note]")`
- The Cancel order button's generated class `sc-fzXfKy jQdBvX` becomes `_cancel_xxxxx`. It has no id, test id, data attribute or aria-label: only its text.
  - affects `page.locator("button.sc-fzXfKy.jQdBvX")` (py L43,49; ts L39,45): nothing stable is left but role + name. Heal: `page.get_by_role("button", name="Cancel order")`
- The class `toggle` inside the `<order-flag>` shadow root is hashed.
  - affects `page.locator("order-flag button.toggle")` (py L67; ts L62): the class is gone; the button is the only `role=switch` in the component. Heal: `page.locator("order-flag [role=switch]")`
- Everything addressed by id, data-qa, data-cy, data-testid, aria-label, aria-labelledby, role or name is unchanged.
  - no test line is affected (nothing to heal).
