import { test, expect } from '@playwright/test';

let BASE = process.env.QA_BASE_URL || 'http://127.0.0.1:8765/real-base/';
if (!BASE.endsWith('/')) BASE += '/';

test.use({ actionTimeout: 5000 });

// Every locator below is copied from a real public test suite; the comment names its category and kind of source.


test('real structure', async ({ page }) => {
  await page.goto(BASE + 'structure.html');
  // [structural chain] 24-hop absolute chain recorded from a chat app, Playwright python
  await page.locator('div:nth-child(4) > div:nth-child(2) > div:nth-child(2) > div > div > div > div > div > div > div > div > div:nth-child(3) > div > div:nth-child(2) > div > div > div:nth-child(3) > div > div > div > div:nth-child(2) > div > div > div').click();
  await expect(page.getByTestId('summary')).toContainText('hit.s1: click');
  // [structural chain] position chain ending in a generated react-native-web atomic class, Playwright python
  await page.locator('div:nth-child(3) > div:nth-child(2) > div > div > div > .css-view-175oi2r').click();
  await expect(page.getByTestId('summary')).toContainText('hit.s2: click');
  // [structural chain] map-widget chain that spells out the library's runtime state classes, Cypress
  await page.locator('[data-qa="pnx-occhab-form"] > div:nth-child(1) > pnx-map > div > div.leaflet-container.leaflet-touch.leaflet-fade-anim.leaflet-grab.leaflet-touch-drag.leaflet-touch-zoom > div.leaflet-control-container > div.leaflet-top.leaflet-left > div.leaflet-draw.leaflet-control > div:nth-child(1) > div > a').click();
  await expect(page.getByTestId('summary')).toContainText('hit.s3: click');
  // [structural chain] design-system class + css-module hash + anonymous divs, Cypress
  await page.locator('#app-layout-page > section.pf-c-page__main-section.pf-m-no-padding.pf-m-light.appLayout_app-layout--content__1Gx8I > section > div > div > div > div:nth-child(2) > form > div:nth-child(4) > div.pf-c-form__group-control > div').click();
  await expect(page.getByTestId('summary')).toContainText('hit.s4: click');
  // [structural chain] react `useId` id plus two css-module hashes plus design-system classes, Playwright ts (recorded)
  await page.locator('[id="accordion-item-\\:r2u\\:"] > .-esm-form-engine__page-renderer__formSection___NMgiH > .-esm-form-engine__section-renderer__section___m5psJ > div > div > .cds--fieldset > .cds--form-item > .cds--radio-button-group > div:nth-child(2) > .cds--radio-button__label > .cds--radio-button__appearance').click();
  await expect(page.getByTestId('summary')).toContainText('hit.s5: click');
  // [structural chain] absolute positional xpath from a framework mount id, Selenium python
  await page.locator('//*[@id="app"]/div[1]/div[3]/div[2]/div/div/div[2]/div[2]/div/div/div/div[1]/div').click();
  await expect(page.getByTestId('summary')).toContainText('hit.s6: click');
});

test('real abs', async ({ page }) => {
  await page.goto(BASE + 'abs.html');
  // [structural chain] absolute xpath from /html/body, Selenium python
  await page.locator('xpath=/html/body/div[2]/div/div[1]/div[3]/div/div').click();
  await expect(page.getByTestId('summary')).toContainText('hit.s9: click');
});

test('real loose', async ({ page }) => {
  await page.goto(BASE + 'loose.html');
  // [structural chain] anonymous nested layout, no attribute at all, Playwright ts
  await page.locator('div > div > div > div > div > a').first().click();
  await expect(page.getByTestId('summary')).toContainText('hit.s11: click');
  // [utility class] spinner located by position and a plain class, Playwright ts
  await page.locator('div > div > div.loader').click();
  await expect(page.getByTestId('summary')).toContainText('hit.g70a: click');
  // [utility class] button located by its tailwind padding utilities, Playwright ts
  await page.locator('button.py-1.px-3').click();
  await expect(page.getByTestId('summary')).toContainText('hit.g70b: click');
});

test('real positions', async ({ page }) => {
  await page.goto(BASE + 'positions.html');
  // [nth / position] form field picked by its index among siblings, Playwright ts
  await page.locator('.v-input:nth-child(2) input').fill('Jane');
  await expect(page.getByTestId('summary')).toContainText('hit.n12: Jane');
  // [nth / position] tutorial login form, flagged brittle by its own author, Playwright python
  await page.locator('div.form_group:nth-child(1) > input').fill('standard_user');
  await expect(page.getByTestId('summary')).toContainText('hit.n13: standard_user');
  // [nth / position] status column of a recent-files table picked by column index, Playwright ts
  await page.locator('.dx-home-recent tbody tr td:nth-child(4)').first().click();
  await expect(page.getByTestId('summary')).toContainText('hit.n14: click');
  // [nth / position] the 10th search-result card, scrolled into view, Playwright ts
  await page.locator('div.coveo-list-layout.CoveoResult:nth-child(10)').click();
  await expect(page.getByTestId('summary')).toContainText('hit.n16: click');
  // [nth / position] the third editor pane's language picker, Playwright ts
  await page.locator(':nth-match([title="Change Language"], 3)').click();
  await expect(page.getByTestId('summary')).toContainText('hit.n17: click');
  // [nth / position] the last widget-name input of a document, Playwright ts
  await page.locator('div [data-testid="input-widget-name"] >> nth=-1').fill('Revenue');
  await expect(page.getByTestId('summary')).toContainText('hit.n21: Revenue');
  // [nth / position] first related-result card by data-cy hooks plus a position, Playwright ts
  await page.locator('[data-cy="related-byId"] [data-cy="result"]:nth-child(1)').click();
  await expect(page.getByTestId('summary')).toContainText('hit.n22: click');
  // [nth / position] items following a section header in a flat menu, then the n-th, Playwright ts
  await page.locator('[role="menuitem"]:has-text("Projects") ~ [role="menuitem"]').nth(1).click();
  await expect(page.getByTestId('summary')).toContainText('hit.n23: click');
});

test('real xpath', async ({ page }) => {
  await page.goto(BASE + 'xpath.html');
  // [xpath axis] climb from a text to its table row by a tailwind group marker, Playwright ts
  await page.getByText('My subscription', { exact: true }).locator("xpath=ancestor::div[contains(@class, 'group/row')]").click();
  await expect(page.getByTestId('summary')).toContainText('hit.x27: click');
  // [xpath axis] nearest scrolling container above a list by a tailwind utility, Playwright ts
  await page.locator('ol').locator("xpath=ancestor::div[contains(@class,'overflow-y-auto')][1]").click();
  await expect(page.getByTestId('summary')).toContainText('hit.x28: click');
  // [xpath axis] the alert rendered right after a row, by a sibling axis, Playwright ts
  await page.getByText('Agent A', { exact: true }).locator("xpath=ancestor::*[contains(@class, 'agent-row')]/following-sibling::*[1]").click();
  await expect(page.getByTestId('summary')).toContainText('hit.x29: click');
  // [xpath axis] code-editor content of the active tab, excluding inline widgets, by xpath with `not()`, Playwright ts
  await page.locator("xpath=//div[@role='tabpanel' and not(contains(@style, 'display: none'))]//*[starts-with(@id,'rstudio_source_text_editor')]//*[@class='ace_content' and not(ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' ace_lineWidgetContainer ')])]").click();
  await expect(page.getByTestId('summary')).toContainText('hit.x30: click');
  // [xpath axis] cell by row text and header text with `count(preceding-sibling)`, Playwright ts
  await page.locator("//tr[td[text()='Alice']]/td[count(//th[text()='Status']/preceding-sibling::th)+1]").click();
  await expect(page.getByTestId('summary')).toContainText('hit.x31: click');
  // [xpath axis] the upload block that owns a label, by a text anchor and an ancestor predicate, Playwright python
  await page.locator("xpath=//*[normalize-space(text())='Cover Letter']/ancestor::*[.//button[contains(.,'Attach')]][1]").click();
  await expect(page.getByTestId('summary')).toContainText('hit.x33: click');
  // [xpath axis] the live offer's button, excluding expired cards by a negative ancestor predicate, Playwright python
  await page.locator("xpath=//div[contains(@class, 'btn-peel')][not(ancestor::div[contains(@class, 'promotion-discount-card--expired')])]").click();
  await expect(page.getByTestId('summary')).toContainText('hit.x34: click');
  // [xpath axis] a styled-components section header matched on the generated `sc-...-0 ` suffix, Selenium python
  await page.locator("//div[contains(@class, 'sc-') and contains(@class, '-0 ') and contains(text(), 'Events Without Choices')]").click();
  await expect(page.getByTestId('summary')).toContainText('hit.x36: click');
  // [xpath axis] the search input of the open dropdown via parent and sibling steps, Selenium js
  await page.locator('//ul[@aria-hidden="false"]/../preceding-sibling::span/input').fill('adm');
  await expect(page.getByTestId('summary')).toContainText('hit.x38: adm');
  // [xpath axis] the day after the picked one, by `following::` and a label with a trailing space, Selenium java
  await page.locator("//div[text()='Start date ']/following::div[contains(@class,'day picked')]/following-sibling::div[1]").click();
  await expect(page.getByTestId('summary')).toContainText('hit.x58: click');
});

test('real data', async ({ page }) => {
  await page.goto(BASE + 'data.html');
  // [data attribute] a select inside a test-id container, with a non-default test-id attribute name, Playwright ts
  await page.locator('data-test=category-space-permission >> .MuiSelect-select').click();
  await expect(page.getByTestId('summary')).toContainText('hit.d40: click');
  // [data attribute] a union of three strategies for a role picker that may be native or custom, Playwright ts
  await page.locator('select[name*="role" i], [data-testid*="role-select"], [role="listbox"]:near(:text("role"))').selectOption('editor');
  await expect(page.getByTestId('summary')).toContainText('hit.d41: editor');
  // [data attribute] a data-qa button scoped by an index-based parent form (the helper's template filled with a form selector), Playwright ts
  await page.locator('form:nth-of-type(2) >> [data-qa="add-to-shopping-cart"]').click();
  await expect(page.getByTestId('summary')).toContainText('hit.d42: click');
  // [data attribute] a result card on a third-party site, renamed without notice, Playwright ts
  await page.locator('[data-qa="vacancy-serp__vacancy"]').first().click();
  await expect(page.getByTestId('summary')).toContainText('hit.d43: click');
  // [data attribute] the fqdn cell of the first data-grid row by row id and field, with a trailing space in the selector, Cypress
  await page.locator('[data-id="0"] > [data-field="fqdn"] ').click();
  await expect(page.getByTestId('summary')).toContainText('hit.d45: click');
});

test('real aria', async ({ page }) => {
  await page.goto(BASE + 'aria.html');
  // [aria] a disclosure toggle by the id it controls, Playwright python
  await page.locator('button[aria-controls="box-busca-refinada"]').click();
  await expect(page.getByTestId('summary')).toContainText('hit.a46: click');
  // [aria] a Select2 combobox whose labelledby id embeds the form class and the field name, Playwright ts
  await page.locator('[aria-labelledby="select2-virtualmachinecataloguesyncform-data_center_id-container"]').click();
  await expect(page.getByTestId('summary')).toContainText('hit.a47: click');
  // [aria] a taxonomy radio by an aria-label built from taxonomy and field names, Playwright ts
  await page.locator('[aria-label="CSETv1:Physical Objects:no"]').check();
  await expect(page.getByTestId('summary')).toContainText('hit.a48: checked');
  // [aria] a status icon by an aria-label with leading and trailing spaces, Selenium python
  await page.locator('//span[@aria-label=" Pending "]').click();
  await expect(page.getByTestId('summary')).toContainText('hit.a49: click');
  // [aria] trigger of a row-actions popover, Playwright ts
  await page.getByRole('button', { name: 'Actions', exact: true }).click();
  await expect(page.getByTestId('summary')).toContainText('hit.a50o: click');
  // [aria] a popover found by the html `popover` attribute, role and label, Playwright ts
  await page.locator('[popover="auto"][role="group"][aria-label="Row actions"]').getByRole('button', { name: 'Edit' }).click();
  await expect(page.getByTestId('summary')).toContainText('hit.a50: click');
  // [aria] a settings button by a case-insensitive aria-label substring, Playwright ts
  await page.locator('button[aria-label*="settings" i]').first().click();
});

test('real text', async ({ page }) => {
  await page.goto(BASE + 'text.html');
  // [text] the first element containing a word (substring match) plus an index, Playwright ts
  await page.locator('text=Add >> nth=0').click();
  await expect(page.getByTestId('summary')).toContainText('hit.t20: click');
  // [text] a toggle row matched by css-module infix classes and an exact CJK label, Playwright python
  await page.locator('div[class*="_switch_"]:has(div[class*="_tit1_"]:text-is("合集"))').click();
  await expect(page.getByTestId('summary')).toContainText('hit.t53: click');
  // [text] the table row whose tool cell is exactly one word and which mentions a file, Playwright python
  await page.locator("#tape tr:has(td.c-tool:text-is('Edit')):has-text('billing.py')").click();
  await expect(page.getByTestId('summary')).toContainText('hit.t54: click');
  // [text] a tab identified by an icon-font ligature, Playwright python
  await page.locator("[role='tab']:has(i.google-symbols:text-is('image'))").click();
  await expect(page.getByTestId('summary')).toContainText('hit.t55: click');
  // [text] a zoom indicator found by a regex over its text, Playwright ts
  await page.locator('text=/^\\d+%$/').first().click();
  await expect(page.getByTestId('summary')).toContainText('hit.t57: click');
  // [text] a label found by a full sentence, Selenium java (adapted to a Playwright xpath)
  await page.locator('xpath=//label[contains(text(), "What\'s the patient\'s birth date?")]').click();
  await expect(page.getByTestId('summary')).toContainText('hit.t59: click');
});

test('real generated', async ({ page }) => {
  await page.goto(BASE + 'generated.html');
  // [generated class] list items by an emotion hash class, Playwright ts
  await page.locator('.css-7g0azd').nth(0).click();
  await expect(page.getByTestId('summary')).toContainText('hit.g60: click');
  // [generated class] the dropdown arrow of a react-select by its emotion class, Playwright ts
  await page.locator('.css-1xc3v61-indicatorContainer').click();
  await expect(page.getByTestId('summary')).toContainText('hit.g61: click');
  // [generated class] the placeholder of a react-select by its instance counter, Playwright ts
  await page.locator("//div[contains(@id,'react-select-2-placeholder')]").click();
  await expect(page.getByTestId('summary')).toContainText('hit.g63: click');
  // [generated class] the 7th option of the 3rd react-select on the page, Playwright ts
  await page.locator('#react-select-3-option-6').click();
  await expect(page.getByTestId('summary')).toContainText('hit.g62: click');
  // [generated id] a form input by a server-rendered `useId` id, Playwright ts
  await page.locator('#\\:Rajsq\\:-form-item').fill('Jane Doe');
  await expect(page.getByTestId('summary')).toContainText('hit.g65: Jane Doe');
  // [generated id] the helper text of an MUI field by its react 18 `useId` id, Playwright ts
  await page.locator('#\\:r1\\:-helper-text').click();
  await expect(page.getByTestId('summary')).toContainText('hit.g66: click');
  // [generated class] the delete button of the third tree item by a component-library class and two positions, Cypress
  await page.locator('.MuiTreeView-root ul > div > div > li:nth-child(3) button:nth-child(2)').click();
  await expect(page.getByTestId('summary')).toContainText('hit.g67: click');
});

test('real frames', async ({ page }) => {
  await page.goto(BASE + 'frames.html');
  // [frame] a payment-element field inside a randomly named iframe, Playwright ts
  await page.frameLocator('iframe[name^="__privateStripeFrame"]').first().locator('[name="number"]').fill('4242424242424242');
  await expect(page.getByTestId('summary')).toContainText('hit.f71a: 4242424242424242');
  // [frame] a payment-element field inside a randomly named iframe, Playwright ts
  await page.frameLocator('iframe[name^="__privateStripeFrame"]').first().locator('[name="expiry"]').fill('1230');
  await expect(page.getByTestId('summary')).toContainText('hit.f71b: 1230');
  // [frame] an Amazon-style payment iframe by a name prefix, then a textbox by name, Playwright python
  await page.frameLocator("iframe[name^='ApxSecureIframe']").getByRole('textbox', { name: 'Card number' }).fill('4111111111111111');
  await expect(page.getByTestId('summary')).toContainText('hit.f73: 4111111111111111');
  // [frame] a block-editor canvas that only exists in iframed-editor mode, Playwright python
  await page.frameLocator('iframe[name="editor-canvas"]').locator('.wp-block-post-title').click();
  await expect(page.getByTestId('summary')).toContainText('hit.f75: click');
});

test('real frames nested', async ({ page }) => {
  await page.goto(BASE + 'frames-nested.html');
  // [frame] a confirm button in a layer iframe nested in the first iframe of the page, Playwright python
  await page.frameLocator('iframe').first().frameLocator('iframe[name="layui-layer-iframe1"]').locator('text=确认').click();
  await expect(page.getByTestId('summary')).toContainText('hit.f74: click');
});

test('real shadow', async ({ page }) => {
  await page.goto(BASE + 'shadow.html');
  // [shadow dom] an input inside a component library's open shadow root, test id on the host, Playwright ts
  await page.locator('ion-searchbar[data-testid="search-filter-input"] input').fill('cash');
  await expect(page.getByTestId('summary')).toContainText('hit.w77: cash');
  // [shadow dom] a textarea inside a component library's open shadow root, Playwright ts
  await page.locator('ion-textarea[data-testid="group-note-textarea"] textarea').fill('Quarterly review');
  await expect(page.getByTestId('summary')).toContainText('hit.w78: Quarterly review');
  // [shadow dom] a toggle reached through an internal primitive component of a vendor library, Playwright ts
  await page.locator('lightning-input.pipelineInspectionToggle lightning-primitive-input-toggle').click();
  await expect(page.getByTestId('summary')).toContainText('hit.w79: click');
  // [shadow dom] the accept button of a vendor dialog by anonymous divs, a state pseudo-class and a position, Playwright ts
  await page.locator('section[role="dialog"] div div button:not(:disabled):nth-child(2)').click();
  await expect(page.getByTestId('summary')).toContainText('hit.w80: click');
});

test('real scoped', async ({ page }) => {
  await page.goto(BASE + 'scoped.html');
  // [scoped chain] an option of the visible dropdown among stale hidden copies left in the portal, Playwright ts
  await page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)').last().locator('.ant-select-item-option').getByText('Marketing', { exact: true }).click();
  await expect(page.getByTestId('summary')).toContainText('hit.c84: click');
  // [scoped chain] the menu that contains a given button, by a relational filter, Playwright ts
  await page.locator('mat-menu').filter({ has: page.locator('button:has-text("Add New Tag")') }).click();
  await expect(page.getByTestId('summary')).toContainText('hit.c85: click');
  // [scoped chain] an option whose name cell matches an exact regex, Playwright ts
  await page.locator('.agent-option').filter({ has: page.locator('.agent-option-name', { hasText: /^Alpha$/ }) }).click();
  await expect(page.getByTestId('summary')).toContainText('hit.c86: click');
  // [scoped chain] the card that holds two texts; `div` matches every ancestor so `.first` is needed, Playwright ts
  await page.locator('div', { hasText: 'E2E Sync Store' }).filter({ has: page.locator('text=Local Disk') }).first().click();
  await expect(page.getByTestId('summary')).toContainText('hit.c87: click');
  // [scoped chain] a label reached by climbing from a matched part and descending by test id, Playwright ts
  await page.getByText('Row A', { exact: true }).locator("xpath=ancestor::*[@data-testid='on-call-direct-page']").getByTestId('on-call-direct-page-label').click();
  await expect(page.getByTestId('summary')).toContainText('hit.c88: click');
  // [scoped chain] the edit button of the first row, identified by its icon's data-icon name, Playwright ts
  await page.locator('.ant-table-row').first().locator('button:has(svg[data-icon="pen-to-square"])').click();
  await expect(page.getByTestId('summary')).toContainText('hit.c89: click');
  // [scoped chain] a status filter found by one of its options, Playwright ts
  await page.locator('select').filter({ has: page.locator("option[value='pending']") }).first().selectOption('approved');
  await expect(page.getByTestId('summary')).toContainText('hit.c90: approved');
  // [scoped chain] a switch to the right of a label (a geometry selector), Playwright ts
  await page.locator('button[role=switch]:right-of(:text("Versioning"))').click();
  await expect(page.getByTestId('summary')).toContainText('hit.c91: click');
  // [scoped chain] a field reached through a parent hop from its label, Playwright ts
  await page.getByText('Ban user until').locator('..').locator('.DatePicker-wrapper input').fill('2026-12-01');
  await expect(page.getByTestId('summary')).toContainText('hit.c93: 2026-12-01');
});

test('real components', async ({ page }) => {
  await page.goto(BASE + 'components.html');
  // [component library] the 4th item of a data-grid column menu, Cypress (`.children().eq(3)` as a Playwright xpath step)
  await page.locator('.MuiDataGrid-menu > div > .MuiList-root').locator('xpath=./*').nth(3).click();
  await expect(page.getByTestId('summary')).toContainText('hit.m94: click');
  // [component library] an option of a select popup rendered in a portal, id taken from the field name, Playwright ts
  await page.locator('#menu-cardRegion ul.MuiMenu-list li').nth(2).click();
  await expect(page.getByTestId('summary')).toContainText('hit.m97: click');
  // [component library] the second tab of a run page by a data-cy header and a position in a design-system tab list, Cypress
  await page.locator('[data-cy=run-detail-header] .ant-tabs-nav-list div:nth-child(2)').click();
  await expect(page.getByTestId('summary')).toContainText('hit.a100: click');
  // [component library] a react-select input scoped by the field's id, Playwright ts
  await page.locator('#country input').fill('Far');
  await expect(page.getByTestId('summary')).toContainText('hit.rs101a: Far');
  // [component library] the first option of a react-select (rendered as plain divs) by the class prefix, Playwright ts
  await page.locator('#country .react-select__option').nth(0).click();
  await expect(page.getByTestId('summary')).toContainText('hit.rs101b: click');
  // [component library] a grid cell by the display row index and the column id, Playwright ts (the grid vendor's own harness)
  await page.locator('.ag-row[row-index="3"] .ag-cell[col-id="name"]').click();
  await expect(page.getByTestId('summary')).toContainText('hit.ag103: click');
  // [component library] name cells of the unpinned (center) columns, Selenium python (adapted to a Playwright nth)
  await page.locator(".ag-center-cols-container .ag-row[role='row'] .ag-cell[col-id='name']").nth(1).click();
  await expect(page.getByTestId('summary')).toContainText('hit.ag104: click');
  // [component library] the buyer cell of the row whose status text is `Needs you`, Playwright python
  await page.locator('.ag-row', { hasText: 'Needs you' }).locator('[col-id=buyer]').click();
  await expect(page.getByTestId('summary')).toContainText('hit.ag105: click');
  // [component library] a Select2 widget injected as the sibling of the hidden select, by an adjacent-sibling selector, Playwright ts
  await page.locator('#addAssetModal-assets + .select2 .select2-selection').click();
  await expect(page.getByTestId('summary')).toContainText('hit.s109: click');
  // [component library] the 1st of the displayed month, excluding the next month's leading cell, Playwright ts
  await page.locator('.react-datepicker__day--001:not(.react-datepicker__day--outside-month)').click();
  await expect(page.getByTestId('summary')).toContainText('hit.s111: click');
  // [component library] an option inside a headless-ui portal root, Playwright ts
  await page.locator('[data-headlessui-portal]').getByRole('option', { name: 'Beta', exact: true }).click();
  await expect(page.getByTestId('summary')).toContainText('hit.s113: click');
});
