import os
import re  # noqa: F401

from playwright.sync_api import Page, expect

TIMEOUT = int(os.environ.get("QA_ACTION_TIMEOUT_MS", "5000"))

BASE = os.environ.get("QA_BASE_URL", "http://127.0.0.1:8765/real-base/")
if not BASE.endswith("/"):
    BASE += "/"

# Every locator below is copied from a real public test suite; the comment names its category and kind of source.


def test_real_structure(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "structure.html")
    # [structural chain] 24-hop absolute chain recorded from a chat app, Playwright python
    page.locator("div:nth-child(4) > div:nth-child(2) > div:nth-child(2) > div > div > div > div > div > div > div > div > div:nth-child(3) > div > div:nth-child(2) > div > div > div:nth-child(3) > div > div > div > div:nth-child(2) > div > div > div").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.s1: click")
    # [structural chain] position chain ending in a generated react-native-web atomic class, Playwright python
    page.locator("div:nth-child(3) > div:nth-child(2) > div > div > div > .css-view-175oi2r").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.s2: click")
    # [structural chain] map-widget chain that spells out the library's runtime state classes, Cypress
    page.locator('[data-qa="pnx-occhab-form"] > div:nth-child(1) > pnx-map > div > div.leaflet-container.leaflet-touch.leaflet-fade-anim.leaflet-grab.leaflet-touch-drag.leaflet-touch-zoom > div.leaflet-control-container > div.leaflet-top.leaflet-left > div.leaflet-draw.leaflet-control > div:nth-child(1) > div > a').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.s3: click")
    # [structural chain] design-system class + css-module hash + anonymous divs, Cypress
    page.locator("#app-layout-page > section.pf-c-page__main-section.pf-m-no-padding.pf-m-light.appLayout_app-layout--content__1Gx8I > section > div > div > div > div:nth-child(2) > form > div:nth-child(4) > div.pf-c-form__group-control > div").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.s4: click")
    # [structural chain] react `useId` id plus two css-module hashes plus design-system classes, Playwright ts (recorded)
    page.locator('[id="accordion-item-\\:r2u\\:"] > .-esm-form-engine__page-renderer__formSection___NMgiH > .-esm-form-engine__section-renderer__section___m5psJ > div > div > .cds--fieldset > .cds--form-item > .cds--radio-button-group > div:nth-child(2) > .cds--radio-button__label > .cds--radio-button__appearance').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.s5: click")
    # [structural chain] absolute positional xpath from a framework mount id, Selenium python
    page.locator('//*[@id="app"]/div[1]/div[3]/div[2]/div/div/div[2]/div[2]/div/div/div/div[1]/div').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.s6: click")

def test_real_abs(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "abs.html")
    # [structural chain] absolute xpath from /html/body, Selenium python
    page.locator("xpath=/html/body/div[2]/div/div[1]/div[3]/div/div").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.s9: click")

def test_real_loose(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "loose.html")
    # [structural chain] anonymous nested layout, no attribute at all, Playwright ts
    page.locator("div > div > div > div > div > a").first.click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.s11: click")
    # [utility class] spinner located by position and a plain class, Playwright ts
    page.locator("div > div > div.loader").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.g70a: click")
    # [utility class] button located by its tailwind padding utilities, Playwright ts
    page.locator("button.py-1.px-3").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.g70b: click")

def test_real_positions(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "positions.html")
    # [nth / position] form field picked by its index among siblings, Playwright ts
    page.locator(".v-input:nth-child(2) input").fill("Jane")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.n12: Jane")
    # [nth / position] tutorial login form, flagged brittle by its own author, Playwright python
    page.locator("div.form_group:nth-child(1) > input").fill("standard_user")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.n13: standard_user")
    # [nth / position] status column of a recent-files table picked by column index, Playwright ts
    page.locator(".dx-home-recent tbody tr td:nth-child(4)").first.click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.n14: click")
    # [nth / position] the 10th search-result card, scrolled into view, Playwright ts
    page.locator("div.coveo-list-layout.CoveoResult:nth-child(10)").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.n16: click")
    # [nth / position] the third editor pane's language picker, Playwright ts
    page.locator(':nth-match([title="Change Language"], 3)').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.n17: click")
    # [nth / position] the last widget-name input of a document, Playwright ts
    page.locator('div [data-testid="input-widget-name"] >> nth=-1').fill("Revenue")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.n21: Revenue")
    # [nth / position] first related-result card by data-cy hooks plus a position, Playwright ts
    page.locator('[data-cy="related-byId"] [data-cy="result"]:nth-child(1)').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.n22: click")
    # [nth / position] items following a section header in a flat menu, then the n-th, Playwright ts
    page.locator('[role="menuitem"]:has-text("Projects") ~ [role="menuitem"]').nth(1).click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.n23: click")

def test_real_xpath(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "xpath.html")
    # [xpath axis] climb from a text to its table row by a tailwind group marker, Playwright ts
    page.get_by_text("My subscription", exact=True).locator("xpath=ancestor::div[contains(@class, 'group/row')]").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.x27: click")
    # [xpath axis] nearest scrolling container above a list by a tailwind utility, Playwright ts
    page.locator("ol").locator("xpath=ancestor::div[contains(@class,'overflow-y-auto')][1]").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.x28: click")
    # [xpath axis] the alert rendered right after a row, by a sibling axis, Playwright ts
    page.get_by_text("Agent A", exact=True).locator("xpath=ancestor::*[contains(@class, 'agent-row')]/following-sibling::*[1]").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.x29: click")
    # [xpath axis] code-editor content of the active tab, excluding inline widgets, by xpath with `not()`, Playwright ts
    page.locator("xpath=//div[@role='tabpanel' and not(contains(@style, 'display: none'))]//*[starts-with(@id,'rstudio_source_text_editor')]//*[@class='ace_content' and not(ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' ace_lineWidgetContainer ')])]").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.x30: click")
    # [xpath axis] cell by row text and header text with `count(preceding-sibling)`, Playwright ts
    page.locator("//tr[td[text()='Alice']]/td[count(//th[text()='Status']/preceding-sibling::th)+1]").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.x31: click")
    # [xpath axis] the upload block that owns a label, by a text anchor and an ancestor predicate, Playwright python
    page.locator("xpath=//*[normalize-space(text())='Cover Letter']/ancestor::*[.//button[contains(.,'Attach')]][1]").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.x33: click")
    # [xpath axis] the live offer's button, excluding expired cards by a negative ancestor predicate, Playwright python
    page.locator("xpath=//div[contains(@class, 'btn-peel')][not(ancestor::div[contains(@class, 'promotion-discount-card--expired')])]").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.x34: click")
    # [xpath axis] a styled-components section header matched on the generated `sc-...-0 ` suffix, Selenium python
    page.locator("//div[contains(@class, 'sc-') and contains(@class, '-0 ') and contains(text(), 'Events Without Choices')]").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.x36: click")
    # [xpath axis] the search input of the open dropdown via parent and sibling steps, Selenium js
    page.locator('//ul[@aria-hidden="false"]/../preceding-sibling::span/input').fill("adm")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.x38: adm")
    # [xpath axis] the day after the picked one, by `following::` and a label with a trailing space, Selenium java
    page.locator("//div[text()='Start date ']/following::div[contains(@class,'day picked')]/following-sibling::div[1]").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.x58: click")

def test_real_data(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "data.html")
    # [data attribute] a select inside a test-id container, with a non-default test-id attribute name, Playwright ts
    page.locator("data-test=category-space-permission >> .MuiSelect-select").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.d40: click")
    # [data attribute] a union of three strategies for a role picker that may be native or custom, Playwright ts
    page.locator('select[name*="role" i], [data-testid*="role-select"], [role="listbox"]:near(:text("role"))').select_option("editor")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.d41: editor")
    # [data attribute] a data-qa button scoped by an index-based parent form (the helper's template filled with a form selector), Playwright ts
    page.locator('form:nth-of-type(2) >> [data-qa="add-to-shopping-cart"]').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.d42: click")
    # [data attribute] a result card on a third-party site, renamed without notice, Playwright ts
    page.locator('[data-qa="vacancy-serp__vacancy"]').first.click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.d43: click")
    # [data attribute] the fqdn cell of the first data-grid row by row id and field, with a trailing space in the selector, Cypress
    page.locator('[data-id="0"] > [data-field="fqdn"] ').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.d45: click")

def test_real_aria(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "aria.html")
    # [aria] a disclosure toggle by the id it controls, Playwright python
    page.locator('button[aria-controls="box-busca-refinada"]').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.a46: click")
    # [aria] a Select2 combobox whose labelledby id embeds the form class and the field name, Playwright ts
    page.locator('[aria-labelledby="select2-virtualmachinecataloguesyncform-data_center_id-container"]').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.a47: click")
    # [aria] a taxonomy radio by an aria-label built from taxonomy and field names, Playwright ts
    page.locator('[aria-label="CSETv1:Physical Objects:no"]').check()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.a48: checked")
    # [aria] a status icon by an aria-label with leading and trailing spaces, Selenium python
    page.locator('//span[@aria-label=" Pending "]').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.a49: click")
    # [aria] trigger of a row-actions popover, Playwright ts
    page.get_by_role("button", name="Actions", exact=True).click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.a50o: click")
    # [aria] a popover found by the html `popover` attribute, role and label, Playwright ts
    page.locator('[popover="auto"][role="group"][aria-label="Row actions"]').get_by_role("button", name="Edit").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.a50: click")
    # [aria] a settings button by a case-insensitive aria-label substring, Playwright ts
    page.locator('button[aria-label*="settings" i]').first.click()

def test_real_text(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "text.html")
    # [text] the first element containing a word (substring match) plus an index, Playwright ts
    page.locator("text=Add >> nth=0").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.t20: click")
    # [text] a toggle row matched by css-module infix classes and an exact CJK label, Playwright python
    page.locator('div[class*="_switch_"]:has(div[class*="_tit1_"]:text-is("合集"))').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.t53: click")
    # [text] the table row whose tool cell is exactly one word and which mentions a file, Playwright python
    page.locator("#tape tr:has(td.c-tool:text-is('Edit')):has-text('billing.py')").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.t54: click")
    # [text] a tab identified by an icon-font ligature, Playwright python
    page.locator("[role='tab']:has(i.google-symbols:text-is('image'))").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.t55: click")
    # [text] a zoom indicator found by a regex over its text, Playwright ts
    page.locator("text=/^\\d+%$/").first.click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.t57: click")
    # [text] a label found by a full sentence, Selenium java (adapted to a Playwright xpath)
    page.locator("xpath=//label[contains(text(), \"What's the patient's birth date?\")]").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.t59: click")

def test_real_generated(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "generated.html")
    # [generated class] list items by an emotion hash class, Playwright ts
    page.locator(".css-7g0azd").nth(0).click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.g60: click")
    # [generated class] the dropdown arrow of a react-select by its emotion class, Playwright ts
    page.locator(".css-1xc3v61-indicatorContainer").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.g61: click")
    # [generated class] the placeholder of a react-select by its instance counter, Playwright ts
    page.locator("//div[contains(@id,'react-select-2-placeholder')]").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.g63: click")
    # [generated class] the 7th option of the 3rd react-select on the page, Playwright ts
    page.locator("#react-select-3-option-6").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.g62: click")
    # [generated id] a form input by a server-rendered `useId` id, Playwright ts
    page.locator("#\\:Rajsq\\:-form-item").fill("Jane Doe")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.g65: Jane Doe")
    # [generated id] the helper text of an MUI field by its react 18 `useId` id, Playwright ts
    page.locator("#\\:r1\\:-helper-text").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.g66: click")
    # [generated class] the delete button of the third tree item by a component-library class and two positions, Cypress
    page.locator(".MuiTreeView-root ul > div > div > li:nth-child(3) button:nth-child(2)").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.g67: click")

def test_real_frames(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "frames.html")
    # [frame] a payment-element field inside a randomly named iframe, Playwright ts
    page.frame_locator('iframe[name^="__privateStripeFrame"]').first.locator('[name="number"]').fill("4242424242424242")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.f71a: 4242424242424242")
    # [frame] a payment-element field inside a randomly named iframe, Playwright ts
    page.frame_locator('iframe[name^="__privateStripeFrame"]').first.locator('[name="expiry"]').fill("1230")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.f71b: 1230")
    # [frame] an Amazon-style payment iframe by a name prefix, then a textbox by name, Playwright python
    page.frame_locator("iframe[name^='ApxSecureIframe']").get_by_role("textbox", name="Card number").fill("4111111111111111")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.f73: 4111111111111111")
    # [frame] a block-editor canvas that only exists in iframed-editor mode, Playwright python
    page.frame_locator('iframe[name="editor-canvas"]').locator(".wp-block-post-title").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.f75: click")

def test_real_frames_nested(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "frames-nested.html")
    # [frame] a confirm button in a layer iframe nested in the first iframe of the page, Playwright python
    page.frame_locator("iframe").first.frame_locator('iframe[name="layui-layer-iframe1"]').locator("text=确认").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.f74: click")

def test_real_shadow(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "shadow.html")
    # [shadow dom] an input inside a component library's open shadow root, test id on the host, Playwright ts
    page.locator('ion-searchbar[data-testid="search-filter-input"] input').fill("cash")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.w77: cash")
    # [shadow dom] a textarea inside a component library's open shadow root, Playwright ts
    page.locator('ion-textarea[data-testid="group-note-textarea"] textarea').fill("Quarterly review")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.w78: Quarterly review")
    # [shadow dom] a toggle reached through an internal primitive component of a vendor library, Playwright ts
    page.locator("lightning-input.pipelineInspectionToggle lightning-primitive-input-toggle").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.w79: click")
    # [shadow dom] the accept button of a vendor dialog by anonymous divs, a state pseudo-class and a position, Playwright ts
    page.locator('section[role="dialog"] div div button:not(:disabled):nth-child(2)').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.w80: click")

def test_real_scoped(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "scoped.html")
    # [scoped chain] an option of the visible dropdown among stale hidden copies left in the portal, Playwright ts
    page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").last.locator(".ant-select-item-option").get_by_text("Marketing", exact=True).click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.c84: click")
    # [scoped chain] the menu that contains a given button, by a relational filter, Playwright ts
    page.locator("mat-menu").filter(has=page.locator('button:has-text("Add New Tag")')).click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.c85: click")
    # [scoped chain] an option whose name cell matches an exact regex, Playwright ts
    page.locator(".agent-option").filter(has=page.locator(".agent-option-name", has_text=re.compile(r"^Alpha$"))).click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.c86: click")
    # [scoped chain] the card that holds two texts; `div` matches every ancestor so `.first` is needed, Playwright ts
    page.locator("div", has_text="E2E Sync Store").filter(has=page.locator("text=Local Disk")).first.click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.c87: click")
    # [scoped chain] a label reached by climbing from a matched part and descending by test id, Playwright ts
    page.get_by_text("Row A", exact=True).locator("xpath=ancestor::*[@data-testid='on-call-direct-page']").get_by_test_id("on-call-direct-page-label").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.c88: click")
    # [scoped chain] the edit button of the first row, identified by its icon's data-icon name, Playwright ts
    page.locator(".ant-table-row").first.locator('button:has(svg[data-icon="pen-to-square"])').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.c89: click")
    # [scoped chain] a status filter found by one of its options, Playwright ts
    page.locator("select").filter(has=page.locator("option[value='pending']")).first.select_option("approved")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.c90: approved")
    # [scoped chain] a switch to the right of a label (a geometry selector), Playwright ts
    page.locator('button[role=switch]:right-of(:text("Versioning"))').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.c91: click")
    # [scoped chain] a field reached through a parent hop from its label, Playwright ts
    page.get_by_text("Ban user until").locator("..").locator(".DatePicker-wrapper input").fill("2026-12-01")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.c93: 2026-12-01")

def test_real_components(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "components.html")
    # [component library] the 4th item of a data-grid column menu, Cypress (`.children().eq(3)` as a Playwright xpath step)
    page.locator(".MuiDataGrid-menu > div > .MuiList-root").locator("xpath=./*").nth(3).click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.m94: click")
    # [component library] an option of a select popup rendered in a portal, id taken from the field name, Playwright ts
    page.locator("#menu-cardRegion ul.MuiMenu-list li").nth(2).click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.m97: click")
    # [component library] the second tab of a run page by a data-cy header and a position in a design-system tab list, Cypress
    page.locator("[data-cy=run-detail-header] .ant-tabs-nav-list div:nth-child(2)").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.a100: click")
    # [component library] a react-select input scoped by the field's id, Playwright ts
    page.locator("#country input").fill("Far")
    expect(page.get_by_test_id("summary")).to_contain_text("hit.rs101a: Far")
    # [component library] the first option of a react-select (rendered as plain divs) by the class prefix, Playwright ts
    page.locator("#country .react-select__option").nth(0).click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.rs101b: click")
    # [component library] a grid cell by the display row index and the column id, Playwright ts (the grid vendor's own harness)
    page.locator('.ag-row[row-index="3"] .ag-cell[col-id="name"]').click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.ag103: click")
    # [component library] name cells of the unpinned (center) columns, Selenium python (adapted to a Playwright nth)
    page.locator(".ag-center-cols-container .ag-row[role='row'] .ag-cell[col-id='name']").nth(1).click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.ag104: click")
    # [component library] the buyer cell of the row whose status text is `Needs you`, Playwright python
    page.locator(".ag-row", has_text="Needs you").locator("[col-id=buyer]").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.ag105: click")
    # [component library] a Select2 widget injected as the sibling of the hidden select, by an adjacent-sibling selector, Playwright ts
    page.locator("#addAssetModal-assets + .select2 .select2-selection").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.s109: click")
    # [component library] the 1st of the displayed month, excluding the next month's leading cell, Playwright ts
    page.locator(".react-datepicker__day--001:not(.react-datepicker__day--outside-month)").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.s111: click")
    # [component library] an option inside a headless-ui portal root, Playwright ts
    page.locator("[data-headlessui-portal]").get_by_role("option", name="Beta", exact=True).click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.s113: click")
