# v10 - restructure

Wrappers inserted or removed, siblings added or reordered, columns and panes reordered. Attributes, ids, test hooks, aria and copy are unchanged, so the structural, nth and position chains break (some resolve to the WRONG element and fail the assertion instead of timing out) and the right heals move to a stable attribute.

Lines are tests/python/test_real.py (py) and tests/ts/real.spec.ts (ts); heals are written in the python syntax and follow the locator policy: test id > hand-written id > name > css on a non-copy attribute > role+name > label > text > structural chain. `verify_golden.py --spec real` applies every heal listed here and runs the suite. A line that is not listed still passes.

- [structural chain] `s1`: a wrapper div is inserted at hop 10 (a layout provider)
  - affects `page.locator("div:nth-child(4) > div:nth-child(2) > div:nth-child(2) > div > div > div > div > div > div > div > div > div:nth-child(3) > div > div:nth-child(2) > div > div > div:nth-child(3) > div > div > div > div:nth-child(2) > div > div > div")` (py L19; ts L14): the chain is one hop short and every nth-child after it is off; the row has nothing but its text. Heal: `page.get_by_text("Hi there, is the build green?", exact=True)`
- [structural chain] `s2`: a wrapper div is removed (hop 3)
  - affects `page.locator("div:nth-child(3) > div:nth-child(2) > div > div > div > .css-view-175oi2r")` (py L22; ts L17): one hop fewer: the chain no longer reaches the control; only its text identifies it. Heal: `page.get_by_text("Sign in with SSO", exact=True)`
- [structural chain] `s6`: a wrapper div is inserted at step 3
  - affects `page.locator('//*[@id="app"]/div[1]/div[3]/div[2]/div/div/div[2]/div[2]/div/div/div/div[1]/div')` (py L34; ts L29): one step shifted: every positional index after it is wrong. Heal: `page.get_by_text("Earn 10 coins", exact=True)`
- [structural chain] `s9`: a cookie banner div is added as the first child of body
  - affects `page.locator("xpath=/html/body/div[2]/div/div[1]/div[3]/div/div")` (py L41; ts L36): body > div[2] is now the page shell, not the modal. Heal: `page.get_by_role("button", name="Save", exact=True)`
- [structural chain] `s11`: one layout wrapper is removed (the link now sits under four divs)
  - affects `page.locator("div > div > div > div > div > a").first` (py L48; ts L43): five consecutive div parents are required and only four remain. Heal: `page.get_by_role("link", name="Read the guide")`
- [utility class] `g70a`: the loader is hoisted out of two wrappers
  - affects `page.locator("div > div > div.loader")` (py L51; ts L46): `div > div > div.loader` needs two div ancestors; the loader has role progressbar. Heal: `page.get_by_role("progressbar")`
- [nth / position] `n12`: a new `Middle name` field is inserted at position 2
  - affects `page.locator(".v-input:nth-child(2) input")` (py L61; ts L56): `:nth-child(2)` now resolves to the new field (the fill lands on the wrong input and the assertion fails); the field has a name. Heal: `page.locator('input[name="firstName"]')`
- [nth / position] `n13`: a hidden honeypot form_group is added first
  - affects `page.locator("div.form_group:nth-child(1) > input")` (py L64; ts L59): `:nth-child(1)` is now the hidden honeypot input (the fill times out); the real input has a hand-written id. Heal: `page.locator("#user-name")`
- [nth / position] `n14`: a `Type` column is inserted before Status
  - affects `page.locator(".dx-home-recent tbody tr td:nth-child(4)").first` (py L67; ts L62): the 4th cell is the new Type column (the click lands on the wrong cell); the status cell has a data-field. Heal: `page.locator('.dx-home-recent td[data-field="status"]').first`
- [nth / position] `n16`: a sponsored banner div is added before the results
  - affects `page.locator("div.coveo-list-layout.CoveoResult:nth-child(10)")` (py L70; ts L65): `:nth-child(10)` now counts the banner, so it is result 9 (wrong element); each card has a data-result. Heal: `page.locator('[data-result="doc-10"]')`
- [nth / position] `n17`: the script pane is moved to the first position
  - affects `page.locator(':nth-match([title="Change Language"], 3)')` (py L73; ts L68): the third match is the style pane now (wrong element, the assertion fails); the pane has a data-pane. Heal: `page.locator('[data-pane="script"] [title="Change Language"]')`
- [nth / position] `n22`: a heading div is added as the first child of the list
  - affects `page.locator('[data-cy="related-byId"] [data-cy="result"]:nth-child(1)')` (py L79; ts L74): `:nth-child(1)` is the heading, which is not a result (times out); the hooks are stable, only the index is not. Heal: `page.locator('[data-cy="related-byId"] [data-cy="result"]').first`
- [nth / position] `n23`: a `Tags` section header is inserted between Alpha and Beta
  - affects `page.locator('[role="menuitem"]:has-text("Projects") ~ [role="menuitem"]').nth(1)` (py L82; ts L77): `.nth(1)` is now the `Tags` header (wrong element); the project item has a data attribute. Heal: `page.locator('[role="menuitem"][data-project="beta"]')`
- [xpath axis] `x29`: an `agent-detail` div is inserted between the row and its alert
  - affects `page.get_by_text("Agent A", exact=True).locator("xpath=ancestor::*[contains(@class, 'agent-row')]/following-sibling::*[1]")` (py L95; ts L90): `following-sibling::*[1]` is the new detail div (wrong element). Heal: `page.get_by_role("alert")`
- [xpath axis] `x31`: a `Role` column is inserted before Status (header and cells)
  - py L101; ts L96 stays green: the xpath finds the column by its header, so the reorder changes nothing
- [xpath axis] `x38`: the popup list gets a wrapper div (`ul/..` is the wrapper now)
  - affects `page.locator('//ul[@aria-hidden="false"]/../preceding-sibling::span/input')` (py L113; ts L108): `ul/..` is the wrapper, which has no preceding span; the input has a name. Heal: `page.locator('input[name="role-filter"]')`
- [text] `t20`: an `Address` label is inserted before the buttons
  - affects `page.locator("text=Add >> nth=0")` (py L166; ts L161): `nth=0` is the new label (the click does nothing); the button is the exact-name match. Heal: `page.get_by_role("button", name="Add", exact=True)`
- [shadow dom] `w80`: a `Close` button is added first in the button row
  - affects `page.locator('section[role="dialog"] div div button:not(:disabled):nth-child(2)')` (py L245; ts L240): `:nth-child(2)` is now the disabled Back button (nothing matches). Heal: `page.locator('section[role="dialog"] button[data-action="accept"]')`
- [scoped chain] `c87`: the cards are wrapped in a `stores` div
  - affects `page.locator("div", has_text="E2E Sync Store").filter(has=page.locator("text=Local Disk")).first` (py L261; ts L256): the wrapper holds both texts and is first in document order (wrong element). Heal: `page.get_by_test_id("store-card").filter(has_text="E2E Sync Store")`
- [scoped chain] `c91`: the row stacks vertically (the switch is under its label)
  - affects `page.locator('button[role=switch]:right-of(:text("Versioning"))')` (py L273; ts L268): `:right-of` depends on layout; the switch has a test id. Heal: `page.get_by_test_id("versioning-switch")`
- [scoped chain] `c93`: the label is wrapped in a span
  - affects `page.get_by_text("Ban user until").locator("..").locator(".DatePicker-wrapper input")` (py L276; ts L271): `..` is the span now and has no date picker inside. Heal: `page.locator('input[name="banUntil"]')`
- [component library] `m94`: the Pro edition adds a `Pin` item before Filter
  - affects `page.locator(".MuiDataGrid-menu > div > .MuiList-root").locator("xpath=./*").nth(3)` (py L283; ts L278): `nth(3)` is Pin now (wrong element). Heal: `page.get_by_role("menuitem", name="Filter")`
- [component library] `a100`: v5 adds an `add` button as the first child of the tab list
  - affects `page.locator("[data-cy=run-detail-header] .ant-tabs-nav-list div:nth-child(2)")` (py L289; ts L284): `div:nth-child(2)` is the first tab now (wrong element). Heal: `page.locator('[data-cy=run-detail-header] [data-node-key="2"]')`
- [component library] `ag104`: the Name column is pinned left
  - affects `page.locator(".ag-center-cols-container .ag-row[role='row'] .ag-cell[col-id='name']").nth(1)` (py L301; ts L296): pinned cells live in `.ag-pinned-left-cols-container`, so the center container has no name cells. Heal: `page.locator('.ag-row[row-id="41"] .ag-cell[col-id="name"]')`
- [component library] `s109`: a hint span is inserted between the select and the widget
  - affects `page.locator("#addAssetModal-assets + .select2 .select2-selection")` (py L307; ts L302): `+` needs the widget to be the immediate sibling. Heal: `page.locator('[aria-labelledby="select2-addAssetModal-assets-container"]')`

Every other entry of the catalog is unchanged and passes.
