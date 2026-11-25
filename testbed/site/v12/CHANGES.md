# v12 - mixed

A few of each, plus elements moved into an iframe and a shadow root, one moved out of an iframe, one list re-sorted so a positional locator hits another row, and one element removed (expect NONE, never a substitute).

Lines are tests/python/test_real.py (py) and tests/ts/real.spec.ts (ts); heals are written in the python syntax and follow the locator policy: test id > hand-written id > name > css on a non-copy attribute > role+name > label > text > structural chain. `verify_golden.py --spec real` applies every heal listed here and runs the suite. A line that is not listed still passes.

- [structural chain] `s3`: the map leaves its grab/drag interaction state (leaflet-grab, leaflet-touch-drag removed from the container)
  - affects `page.locator('[data-qa="pnx-occhab-form"] > div:nth-child(1) > pnx-map > div > div.leaflet-container.leaflet-touch.leaflet-fade-anim.leaflet-grab.leaflet-touch-drag.leaflet-touch-zoom > div.leaflet-control-container > div.leaflet-top.leaflet-left > div.leaflet-draw.leaflet-control > div:nth-child(1) > div > a')` (py L25; ts L20): the markup still has the same container, but the runtime state classes in the selector are gone; the control keeps its own semantic class under the `data-qa` root. Heal: `page.locator('[data-qa="pnx-occhab-form"] a.leaflet-draw-draw-polygon')`
- [nth / position] `n13`: the username input moves into a shadow root of its form_group
  - affects `page.locator("div.form_group:nth-child(1) > input")` (py L64; ts L59): the input is now one div deeper inside the shadow root (Playwright lets `>` step from a host into its root, but not past a wrapper); the id selector pierces it. Heal: `page.locator("#user-name")`
- [nth / position] `n21`: a fourth widget is appended
  - affects `page.locator('div [data-testid="input-widget-name"] >> nth=-1')` (py L76; ts L71): `nth=-1` is now widget 4 (wrong element); the widget has a data attribute. Heal: `page.locator('[data-widget="w3"] [data-testid="input-widget-name"]')`
- [xpath axis] `x34`: the state class `--expired` -> `--ended`
  - affects `page.locator("xpath=//div[contains(@class, 'btn-peel')][not(ancestor::div[contains(@class, 'promotion-discount-card--expired')])]")` (py L107; ts L102): the negative predicate no longer excludes the expired card, so two elements match (strict mode error). Heal: `page.locator('[data-offer="offer-2"] .btn-peel')`
- [data attribute] `d42`: the second form's button moves into an embedded cart iframe (a payment-widget style change)
  - affects `page.locator('form:nth-of-type(2) >> [data-qa="add-to-shopping-cart"]')` (py L129; ts L124): the button is inside a frame; the page-level locator no longer reaches it. Heal: `page.frame_locator('iframe[title="Cart widget"]').locator('[data-qa="add-to-shopping-cart"]')`
- [data attribute] `d45`: the data is re-seeded: row ids start at 1000
  - affects `page.locator('[data-id="0"] > [data-field="fqdn"] ')` (py L135; ts L130): the row id is data, not a position; the first row's id changed. Heal: `page.locator('[role="row"] [data-field="fqdn"]').first`
- [aria] `a51`: the Settings button is removed, and the notification button is renamed `Alerts`
  - affects `page.locator('button[aria-label*="settings" i]').first` (py L160; ts L155): the element no longer exists: NONE, never the Alerts button. Heal: NONE: the element is gone, delete the step (no substitute)
- [text] `t53`: the label text changes: `合集` -> `专辑`
  - affects `page.locator('div[class*="_switch_"]:has(div[class*="_tit1_"]:text-is("合集"))')` (py L169; ts L164): the exact text is part of the selector; the row has a data-name. Heal: `page.locator('div[class*="_switch_"][data-name="collection"]')`
- [text] `t57`: a progress `45%` is rendered before the zoom indicator
  - affects `page.locator("text=/^\\d+%$/").first` (py L178; ts L173): `.first` is the progress label now (wrong element). Heal: `page.get_by_test_id("zoom-level")`
- [generated id] `g66`: `:r1:` -> `:r4:` (three ids are issued earlier on the page)
  - affects `page.locator("#\\:r1\\:-helper-text")` (py L203; ts L198): the counter changed; the `-helper-text` suffix is the part the library defines. Heal: `page.locator('[id$="-helper-text"]')`
- [frame] `f71a`: the element generation changes the field name `number` -> `cardnumber`
  - affects `page.frame_locator('iframe[name^="__privateStripeFrame"]').first.locator('[name="number"]')` (py L213; ts L208): the field name differs between element generations. Heal: `page.frame_locator('iframe[name^="__privateStripeFrame"]').first.locator('[name="cardnumber"]')`
- [frame] `f71b`: `expiry` -> `exp-date`
  - affects `page.frame_locator('iframe[name^="__privateStripeFrame"]').first.locator('[name="expiry"]')` (py L216; ts L211): the field name differs between element generations. Heal: `page.frame_locator('iframe[name^="__privateStripeFrame"]').first.locator('[name="exp-date"]')`
- [frame] `f75`: the editor is no longer iframed: the title is in the page
  - affects `page.frame_locator('iframe[name="editor-canvas"]').locator(".wp-block-post-title")` (py L222; ts L217): there is no `editor-canvas` frame any more. Heal: `page.locator(".wp-block-post-title")`
- [frame] `f74`: the layer counter increments: `layui-layer-iframe1` -> `layui-layer-iframe2`
  - affects `page.frame_locator("iframe").first.frame_locator('iframe[name="layui-layer-iframe1"]').locator("text=确认")` (py L229; ts L224): the name ends in a counter; the frame has a title. Heal: `page.frame_locator("iframe").first.frame_locator('iframe[title="Confirm dialog"]').locator("text=确认")`
- [scoped chain] `c90`: a priority select that also offers `pending` is added before it
  - affects `page.locator("select").filter(has=page.locator("option[value='pending']")).first` (py L270; ts L265): `.first` is the new priority select (wrong element). Heal: `page.locator('select[name="status"]')`
- [component library] `ag103`: the grid is sorted: display order 40 41 42 44 43 (row ids keep their data)
  - affects `page.locator('.ag-row[row-index="3"] .ag-cell[col-id="name"]')` (py L298; ts L293): `row-index` is the display position, so index 3 is another row now (wrong cell, the assertion fails); `row-id` follows the data. Heal: `page.locator('.ag-row[row-id="43"] .ag-cell[col-id="name"]')`

Every other entry of the catalog is unchanged and passes.
