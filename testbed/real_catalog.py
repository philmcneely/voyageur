"""The real-locator catalog: every locator is copied from a public test suite (Playwright python / ts, Selenium, Cypress).

Where a locator was adapted to this harness (a Selenium relative xpath turned into a chained Playwright call, a js shadow
query turned into the css Playwright pierces with, a helper template filled with a value) the entry's `src` says so.
Each E() holds the locator, the action, the smallest markup that makes it resolve, and what each variant changes
(M): see build_real.py. {H} marks the element a test acts on.
"""
from build_real import E, M, L, F, T, R, TID, LAB, FIRST, LAST, NTH, FT, FTRE, FH


def ins(sel, i):
    h = sel.split(" > ")
    h.insert(i, "div")
    return " > ".join(h)


def rm(sel, i):
    h = sel.split(" > ")
    del h[i]
    return " > ".join(h)


S1 = ("div:nth-child(4) > div:nth-child(2) > div:nth-child(2) > div > div > div > div > div > div > div > div > div:nth-child(3) > div > "
      "div:nth-child(2) > div > div > div:nth-child(3) > div > div > div > div:nth-child(2) > div > div > div")
S2 = "div:nth-child(3) > div:nth-child(2) > div > div > div > .css-view-175oi2r"
S3 = ('[data-qa="pnx-occhab-form"] > div:nth-child(1) > pnx-map > div > div.leaflet-container.leaflet-touch.leaflet-fade-anim.leaflet-grab.'
      'leaflet-touch-drag.leaflet-touch-zoom > div.leaflet-control-container > div.leaflet-top.leaflet-left > div.leaflet-draw.leaflet-control > '
      'div:nth-child(1) > div > a')
S4 = ('#app-layout-page > section.pf-c-page__main-section.pf-m-no-padding.pf-m-light.appLayout_app-layout--content__1Gx8I > section > div > div > '
      'div > div:nth-child(2) > form > div:nth-child(4) > div.pf-c-form__group-control > div')
S5 = ('[id="accordion-item-\\:r2u\\:"] > .-esm-form-engine__page-renderer__formSection___NMgiH > .-esm-form-engine__section-renderer__section___m5psJ > '
      'div > div > .cds--fieldset > .cds--form-item > .cds--radio-button-group > div:nth-child(2) > .cds--radio-button__label > '
      '.cds--radio-button__appearance')
X6 = '//*[@id="app"]/div[1]/div[3]/div[2]/div/div/div[2]/div[2]/div/div/div/div[1]/div'
X9 = "/html/body/div[2]/div/div[1]/div[3]/div/div"
S11 = "div > div > div > div > div > a"

def ag_html(order=(40, 41, 42, 43, 44), pinned=False):
    """An ag-grid style grid: a header, a pinned-left container and a center container. Hits follow the data, not the position."""
    hits = {("name", 43): "{H}", ("name", 41): "{H:ag104}", ("buyer", 44): "{H:ag105}"}
    cell = lambda col, rid, text: '<div class="ag-cell" role="gridcell" col-id="%s"%s>%s</div>' % (col, hits.get((col, rid), ""), text)
    left, center = [], []
    for i, rid in enumerate(order):
        n = rid - 40
        lc = cell("id", rid, str(rid)) + (cell("name", rid, "Name %d" % n) if pinned else "")
        cc = ("" if pinned else cell("name", rid, "Name %d" % n)) + cell("buyer", rid, "Buyer %d" % n) + cell("status", rid, "Needs you" if rid == 44 else "Done")
        left.append('<div class="ag-row" role="row" row-index="%d" row-id="%d">%s</div>' % (i, rid, lc))
        center.append('<div class="ag-row" role="row" row-index="%d" row-id="%d">%s</div>' % (i, rid, cc))
    head = "".join('<div class="ag-header-cell" col-id="%s">%s</div>' % (c, c.title()) for c in ("id", "name", "buyer", "status"))
    return ('<div class="ag-root"><div class="ag-header">%s</div><div class="ag-body"><div class="ag-pinned-left-cols-container">%s</div>'
            '<div class="ag-center-cols-container">%s</div></div></div>' % (head, "".join(left), "".join(center)))


# react-select draws its dropdown arrow as an svg inside the aria-hidden indicator container (no text, no role)
SELECT_CHEVRON = ('<svg height="20" width="20" viewBox="0 0 20 20" aria-hidden="true" focusable="false" class="css-tj5bde-Svg">'
                  '<path d="M4.516 7.548c0.436-0.446 1.043-0.481 1.576 0l3.908 3.747 3.908-3.747c0.533-0.481 1.141-0.446 1.574 0 '
                  '0.436 0.445 0.408 1.197 0 1.615-0.406 0.418-4.695 4.502-4.695 4.502-0.217 0.223-0.502 0.335-0.787 0.335s-0.57-0.112-0.789-0.335'
                  'c0 0-4.287-4.084-4.695-4.502s-0.436-1.17 0-1.615z"></path></svg>')


def dp_day(cls, num, label, tab="-1", extra=""):
    """A react-datepicker day cell as the library renders it: an option with its date as aria-label, aria-disabled and a tabindex."""
    return ('<div class="react-datepicker__day %s" aria-label="Choose %s" role="option" tabindex="%s" aria-disabled="false" '
            'aria-selected="false"%s>%d</div>' % (cls, label, tab, extra, num))


ENTRIES = [
    # ------------------------------------------------------------------------------------------------ structural chains
    E("s1", "structural chain", "structure", [L(S1)], "click", chain=S1, text="Hi there, is the build green?",
      src="24-hop absolute chain recorded from a chat app, Playwright python",
      muts={"v10": M("a wrapper div is inserted at hop 10 (a layout provider)", chain=ins(S1, 9),
                     heal=[T("Hi there, is the build green?", exact=True)],
                     why="the chain is one hop short and every nth-child after it is off; the row has nothing but its text")}),
    E("s2", "structural chain", "structure", [L(S2)], "click", chain=S2, text="Sign in with SSO",
      src="position chain ending in a generated react-native-web atomic class, Playwright python",
      muts={"v10": M("a wrapper div is removed (hop 3)", chain=rm(S2, 2), heal=[T("Sign in with SSO", exact=True)],
                     why="one hop fewer: the chain no longer reaches the control; only its text identifies it")}),
    E("s3", "structural chain", "structure", [L(S3)], "click", chain=S3, text="Draw a polygon", leaf={"class": "leaflet-draw-draw-polygon", "title": "Draw a polygon"},
      src="map-widget chain that spells out the library's runtime state classes, Cypress",
      muts={"v12": M("the map leaves its grab/drag interaction state (leaflet-grab, leaflet-touch-drag removed from the container)",
                     chain=S3.replace(".leaflet-grab", "").replace(".leaflet-touch-drag", ""),
                     heal=[L('[data-qa="pnx-occhab-form"] a.leaflet-draw-draw-polygon')],
                     why="the markup still has the same container, but the runtime state classes in the selector are gone; the control keeps its own semantic class under the `data-qa` root")}),
    E("s4", "structural chain", "structure", [L(S4)], "click", chain=S4, text='<input type="search" name="search" placeholder="Search APIs">',
      src="design-system class + css-module hash + anonymous divs, Cypress",
      muts={"v11": M("css-module hash `__1Gx8I` -> `__Zr7Qd` (a rebuild)", chain=S4.replace("__1Gx8I", "__Zr7Qd"),
                     heal=[L('input[name="search"]')],
                     why="the hash class no longer matches; the field has a name attribute")}),
    E("s5", "structural chain", "structure", [L(S5)], "click", chain=S5, text="Yes", leaf_tag="span",
      src="react `useId` id plus two css-module hashes plus design-system classes, Playwright ts (recorded)",
      muts={"v11": M("`useId` `:r2u:` -> `:r5k:`, both css-module hashes regenerate", chain=S5.replace("r2u", "r5k").replace("NMgiH", "X7hQ2").replace("m5psJ", "Pq9Lw"),
                     heal=[T("Yes", exact=True)], why="the id and both hashes are new; the radio's label text is all that is stable")}),
    E("s6", "structural chain", "structure", [L(X6)], "click", xchain=X6, text="Earn 10 coins",
      src="absolute positional xpath from a framework mount id, Selenium python",
      muts={"v10": M("a wrapper div is inserted at step 3", xchain=X6.replace("/div[3]/", "/div/div[3]/", 1),
                     heal=[T("Earn 10 coins", exact=True)], why="one step shifted: every positional index after it is wrong")}),

    # ------------------------------------------------------------------------------------------------ absolute xpath
    E("s9", "structural chain", "abs", [L("xpath=" + X9)], "click", xchain=X9, text="Save", leaf={"role": "button"},
      src="absolute xpath from /html/body, Selenium python",
      muts={"v10": M("a cookie banner div is added as the first child of body", xchain=X9.replace("div[2]", "div[3]", 1),
                     heal=[R("button", "Save", exact=True)], why="body > div[2] is now the page shell, not the modal")}),

    # ------------------------------------------------------------------------------------------------ loose chains and utility classes
    E("s11", "structural chain", "loose", [L(S11), FIRST], "click", chain=S11, text="Read the guide", leaf={"href": "#guide"},
      src="anonymous nested layout, no attribute at all, Playwright ts",
      muts={"v10": M("one layout wrapper is removed (the link now sits under four divs)", chain=S11.replace("div > div > div > div > div > a", "div > div > div > div > a"),
                     heal=[R("link", "Read the guide")], why="five consecutive div parents are required and only four remain")}),
    E("g70a", "utility class", "loose", [L("div > div > div.loader")], "click", chain="div > div > div.loader", text="Loading", leaf={"role": "progressbar"},
      src="spinner located by position and a plain class, Playwright ts",
      muts={"v10": M("the loader is hoisted out of two wrappers", chain="div.loader", heal=[R("progressbar")],
                     why="`div > div > div.loader` needs two div ancestors; the loader has role progressbar")}),
    E("g70b", "utility class", "loose", [L("button.py-1.px-3")], "click", html='<button class="py-1 px-3 rounded" data-testid="save-draft"{H}>Save draft</button>',
      src="button located by its tailwind padding utilities, Playwright ts",
      muts={"v11": M("a restyle: `py-1 px-3` -> `py-2 px-4`", sub=[('py-1 px-3', 'py-2 px-4')], heal=[TID("save-draft")],
                     why="the utilities are the only hook in the selector; the button has a test id")}),

    # ------------------------------------------------------------------------------------------------ nth / position
    E("n12", "nth / position", "positions", [L(".v-input:nth-child(2) input")], ("fill", "Jane"),
      html='<div class="v-form"><div class="v-input"><select name="title"><option>Ms</option></select></div>'
           '<div class="v-input"><label for="fn">First name</label><input id="fn" name="firstName" type="text"{H}></div>'
           '<div class="v-input"><label for="ln">Last name</label><input id="ln" name="lastName" type="text"></div></div>',
      src="form field picked by its index among siblings, Playwright ts",
      muts={"v10": M("a new `Middle name` field is inserted at position 2",
                     sub=[('<div class="v-input"><label for="fn">', '<div class="v-input"><label for="mn">Middle name</label><input id="mn" name="middleName" type="text"></div><div class="v-input"><label for="fn">')],
                     heal=[L('input[name="firstName"]')],
                     why="`:nth-child(2)` now resolves to the new field (the fill lands on the wrong input and the assertion fails); the field has a name")}),
    E("n13", "nth / position", "positions", [L("div.form_group:nth-child(1) > input")], ("fill", "standard_user"),
      html='<div class="login"><div class="form_group"><input id="user-name" name="user-name" placeholder="Username" type="text"{H}></div>'
           '<div class="form_group"><input id="password" name="password" placeholder="Password" type="password"></div></div>',
      src="tutorial login form, flagged brittle by its own author, Playwright python",
      muts={"v10": M("a hidden honeypot form_group is added first",
                     sub=[('<div class="login">', '<div class="login"><div class="form_group" hidden><input name="website" type="text" tabindex="-1"></div>')],
                     heal=[L("#user-name")], why="`:nth-child(1)` is now the hidden honeypot input (the fill times out); the real input has a hand-written id"),
            "v12": M("the username input moves into a shadow root of its form_group",
                     html='<div class="login"><div class="form_group" data-shadow-input="n13" data-shadow-wrap="1" data-shadow-input-attrs="id=user-name;name=user-name;placeholder=Username"></div>'
                          '<div class="form_group"><input id="password" name="password" placeholder="Password" type="password"></div></div>',
                     heal=[L("#user-name")], why="the input is now one div deeper inside the shadow root (Playwright lets `>` step from a host into its root, but not past a wrapper); the id selector pierces it")}),
    E("n14", "nth / position", "positions", [L(".dx-home-recent tbody tr td:nth-child(4)"), FIRST], "click",
      html='<table class="dx-home-recent"><thead><tr><th>Name</th><th>Owner</th><th>Size</th><th>Status</th><th>Opened</th></tr></thead><tbody>'
           '<tr><td>a.docx</td><td>me</td><td>1 KB</td><td data-field="status"{H}>synced</td><td>today</td></tr>'
           '<tr><td>b.docx</td><td>me</td><td>2 KB</td><td data-field="status">pending</td><td>today</td></tr></tbody></table>',
      src="status column of a recent-files table picked by column index, Playwright ts",
      muts={"v10": M("a `Type` column is inserted before Status",
                     sub=[('<th>Status</th>', '<th>Type</th><th>Status</th>'), ('<td data-field="status"{H}>', '<td>docx</td><td data-field="status"{H}>'),
                          ('<td data-field="status">pending', '<td>docx</td><td data-field="status">pending')],
                     heal=[L('.dx-home-recent td[data-field="status"]'), FIRST],
                     why="the 4th cell is the new Type column (the click lands on the wrong cell); the status cell has a data-field")}),
    E("n16", "nth / position", "positions", [L("div.coveo-list-layout.CoveoResult:nth-child(10)")], "click",
      html="<div>" + "".join('<div class="coveo-list-layout CoveoResult" data-result="doc-%d"%s>Result %d</div>' % (i, "{H}" if i == 10 else "", i) for i in range(1, 11)) + "</div>",
      src="the 10th search-result card, scrolled into view, Playwright ts",
      muts={"v10": M("a sponsored banner div is added before the results",
                     sub=[("<div><div class=\"coveo-list-layout CoveoResult\" data-result=\"doc-1\">", "<div><div class=\"ad-banner\">Sponsored</div><div class=\"coveo-list-layout CoveoResult\" data-result=\"doc-1\">")],
                     heal=[L('[data-result="doc-10"]')], why="`:nth-child(10)` now counts the banner, so it is result 9 (wrong element); each card has a data-result")}),
    E("n17", "nth / position", "positions", [L(':nth-match([title="Change Language"], 3)')], "click",
      html='<div class="panes"><div class="pane" data-pane="markup"><button title="Change Language">HTML</button></div>'
           '<div class="pane" data-pane="style"><button title="Change Language">CSS</button></div>'
           '<div class="pane" data-pane="script"><button title="Change Language"{H}>JS</button></div></div>',
      src="the third editor pane's language picker, Playwright ts",
      muts={"v10": M("the script pane is moved to the first position",
                     html='<div class="panes"><div class="pane" data-pane="script"><button title="Change Language"{H}>JS</button></div>'
                          '<div class="pane" data-pane="markup"><button title="Change Language">HTML</button></div>'
                          '<div class="pane" data-pane="style"><button title="Change Language">CSS</button></div></div>',
                     heal=[L('[data-pane="script"] [title="Change Language"]')],
                     why="the third match is the style pane now (wrong element, the assertion fails); the pane has a data-pane")}),
    E("n21", "nth / position", "positions", [L('div [data-testid="input-widget-name"] >> nth=-1'), ], ("fill", "Revenue"),
      html='<div class="blocks"><div class="widget" data-widget="w1"><input data-testid="input-widget-name" type="text"></div>'
           '<div class="widget" data-widget="w2"><input data-testid="input-widget-name" type="text"></div>'
           '<div class="widget" data-widget="w3"><input data-testid="input-widget-name" type="text"{H}></div></div>',
      src="the last widget-name input of a document, Playwright ts",
      muts={"v12": M("a fourth widget is appended",
                     sub=[('</div></div>', '</div><div class="widget" data-widget="w4"><input data-testid="input-widget-name" type="text"></div></div>')],
                     heal=[L('[data-widget="w3"] [data-testid="input-widget-name"]')],
                     why="`nth=-1` is now widget 4 (wrong element); the widget has a data attribute")}),
    E("n22", "nth / position", "positions", [L('[data-cy="related-byId"] [data-cy="result"]:nth-child(1)')], "click",
      html='<div data-cy="related-byId"><div data-cy="result"{H}>Incident 41</div><div data-cy="result">Incident 58</div></div>',
      src="first related-result card by data-cy hooks plus a position, Playwright ts",
      muts={"v10": M("a heading div is added as the first child of the list",
                     sub=[('<div data-cy="related-byId">', '<div data-cy="related-byId"><div class="heading">Related</div>')],
                     heal=[L('[data-cy="related-byId"] [data-cy="result"]'), FIRST],
                     why="`:nth-child(1)` is the heading, which is not a result (times out); the hooks are stable, only the index is not")}),
    E("n23", "nth / position", "positions", [L('[role="menuitem"]:has-text("Projects") ~ [role="menuitem"]'), NTH(1)], "click",
      html='<nav><div role="menuitem">Today</div><div role="menuitem">Projects</div><div role="menuitem" data-project="alpha">Alpha</div>'
           '<div role="menuitem" data-project="beta"{H}>Beta</div></nav>',
      src="items following a section header in a flat menu, then the n-th, Playwright ts",
      muts={"v10": M("a `Tags` section header is inserted between Alpha and Beta",
                     sub=[('<div role="menuitem" data-project="beta"{H}>', '<div role="menuitem">Tags</div><div role="menuitem" data-project="beta"{H}>')],
                     heal=[L('[role="menuitem"][data-project="beta"]')], why="`.nth(1)` is now the `Tags` header (wrong element); the project item has a data attribute")}),

    # ------------------------------------------------------------------------------------------------ xpath axes
    E("x27", "xpath axis", "xpath", [T("My subscription", exact=True), L("xpath=ancestor::div[contains(@class, 'group/row')]")], "click",
      html='<div class="flex group/row" data-testid="subscription-row"{H}><span>My subscription</span><button>Delete</button></div>'
           '<div class="flex group/row" data-testid="subscription-row"><span>Other</span><button>Delete</button></div>',
      src="climb from a text to its table row by a tailwind group marker, Playwright ts",
      muts={"v11": M("tailwind marker `group/row` -> `group/item`", sub=[("group/row", "group/item")],
                     heal=[TID("subscription-row"), FT("My subscription")], why="the climb's class is gone; the rows have a test id")}),
    E("x28", "xpath axis", "xpath", [L("ol"), L("xpath=ancestor::div[contains(@class,'overflow-y-auto')][1]")], "click",
      html='<div class="overflow-y-auto" data-testid="log-scroll"{H}><ol><li>one</li><li>two</li><li>three</li></ol></div>',
      src="nearest scrolling container above a list by a tailwind utility, Playwright ts",
      muts={"v11": M("`overflow-y-auto` -> `overflow-auto`", sub=[("overflow-y-auto", "overflow-auto")], heal=[TID("log-scroll")],
                     why="the utility class used as a hook was renamed; the container has a test id")}),
    E("x29", "xpath axis", "xpath", [T("Agent A", exact=True), L("xpath=ancestor::*[contains(@class, 'agent-row')]/following-sibling::*[1]")], "click",
      html='<div class="agent-row"><span>Agent A</span></div><div class="error-alert" role="alert"{H}>Failed: timeout</div>',
      src="the alert rendered right after a row, by a sibling axis, Playwright ts",
      muts={"v10": M("an `agent-detail` div is inserted between the row and its alert",
                     sub=[('<div class="error-alert"', '<div class="agent-detail">details</div><div class="error-alert"')],
                     heal=[R("alert")], why="`following-sibling::*[1]` is the new detail div (wrong element)")}),
    E("x30", "xpath axis", "xpath",
      [L("xpath=//div[@role='tabpanel' and not(contains(@style, 'display: none'))]//*[starts-with(@id,'rstudio_source_text_editor')]//*[@class='ace_content' and not(ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' ace_lineWidgetContainer ')])]")],
      "click",
      html='<div role="tabpanel" style="display: none"><div id="rstudio_source_text_editor1"><div class="ace_content">hidden tab</div></div></div>'
           '<div role="tabpanel"><div id="rstudio_source_text_editor2"><div class="ace_content" data-testid="source-editor"{H}>x &lt;- 1</div>'
           '<div class="ace_lineWidgetContainer"><div class="ace_content">inline widget</div></div></div></div>',
      src="code-editor content of the active tab, excluding inline widgets, by xpath with `not()`, Playwright ts",
      muts={"v11": M("the editor adds a state class: `ace_content` -> `ace_content ace_focus`", sub=[('class="ace_content" data-testid', 'class="ace_content ace_focus" data-testid')],
                     heal=[L('[role="tabpanel"]:visible'), TID("source-editor")],
                     why="`@class='ace_content'` is an equality test and no longer holds; the editor has a test id")}),
    E("x31", "xpath axis", "xpath", [L("//tr[td[text()='Alice']]/td[count(//th[text()='Status']/preceding-sibling::th)+1]")], "click",
      html='<table><thead><tr><th>Name</th><th>Email</th><th>Status</th></tr></thead><tbody>'
           '<tr data-user="alice"><td>Alice</td><td>a@x.io</td><td data-field="status"{H}>Active</td></tr>'
           '<tr data-user="bob"><td>Bob</td><td>b@x.io</td><td data-field="status">Idle</td></tr></tbody></table>',
      src="cell by row text and header text with `count(preceding-sibling)`, Playwright ts",
      muts={"v10": M("a `Role` column is inserted before Status (header and cells)",
                     sub=[('<th>Status</th>', '<th>Role</th><th>Status</th>'), ('<td data-field="status"{H}>', '<td>admin</td><td data-field="status"{H}>'),
                          ('<td data-field="status">Idle', '<td>user</td><td data-field="status">Idle')],
                     heal="green", why="the xpath finds the column by its header, so the reorder changes nothing"),
            "v11": M("header `Status` -> `State`", sub=[('<th>Status</th>', '<th>State</th>')],
                     heal=[L('tr[data-user="alice"] td[data-field="status"]')], why="the header text is the xpath's anchor; the cell has a data-field and the row a data-user")}),
    E("x33", "xpath axis", "xpath", [L("xpath=//*[normalize-space(text())='Cover Letter']/ancestor::*[.//button[contains(.,'Attach')]][1]")], "click",
      html='<div class="upload" data-testid="upload-resume"><label>Resume</label><button>Attach</button></div>'
           '<div class="upload" data-testid="upload-cover"{H}><label>Cover Letter</label><button>Attach</button></div>',
      src="the upload block that owns a label, by a text anchor and an ancestor predicate, Playwright python",
      muts={"v11": M("copy `Cover Letter` -> `Cover letter`", sub=[("<label>Cover Letter</label>", "<label>Cover letter</label>")],
                     heal=[TID("upload-cover")], why="the text anchor is case sensitive; the block has a test id")}),
    E("x34", "xpath axis", "xpath", [L("xpath=//div[contains(@class, 'btn-peel')][not(ancestor::div[contains(@class, 'promotion-discount-card--expired')])]")], "click",
      html='<div class="promotion-discount-card promotion-discount-card--expired" data-offer="offer-1"><div class="btn-peel">Get Code</div></div>'
           '<div class="promotion-discount-card" data-offer="offer-2"><div class="btn-peel"{H}>Get Code</div></div>',
      src="the live offer's button, excluding expired cards by a negative ancestor predicate, Playwright python",
      muts={"v12": M("the state class `--expired` -> `--ended`", sub=[("promotion-discount-card--expired", "promotion-discount-card--ended")],
                     heal=[L('[data-offer="offer-2"] .btn-peel')], why="the negative predicate no longer excludes the expired card, so two elements match (strict mode error)")}),
    E("x36", "xpath axis", "xpath", [L("//div[contains(@class, 'sc-') and contains(@class, '-0 ') and contains(text(), 'Events Without Choices')]")], "click",
      html='<div class="sc-bdnylx-0 gXhnVE"{H}>Events Without Choices</div><div class="sc-bdnylx-1 kQpWzr">Events With Choices</div>',
      src="a styled-components section header matched on the generated `sc-...-0 ` suffix, Selenium python",
      muts={"v11": M("styled-components id `sc-bdnylx-0` -> `sc-hdqfvb-1` (component order changed)", sub=[("sc-bdnylx-0 gXhnVE", "sc-hdqfvb-1 gXhnVE")],
                     heal=[T("Events Without Choices", exact=True)], why="the `-0 ` suffix no longer holds; only the header text is left")}),
    E("x38", "xpath axis", "xpath", [L('//ul[@aria-hidden="false"]/../preceding-sibling::span/input')], ("fill", "adm"),
      html='<div class="k-dropdown"><span class="k-search"><input name="role-filter" type="text"{H}></span><div class="k-popup"><ul aria-hidden="false"><li>Admin</li></ul></div></div>'
           '<div class="k-dropdown"><span class="k-search"><input name="team-filter" type="text"></span><div class="k-popup"><ul aria-hidden="true"><li>Core</li></ul></div></div>',
      src="the search input of the open dropdown via parent and sibling steps, Selenium js",
      muts={"v10": M("the popup list gets a wrapper div (`ul/..` is the wrapper now)",
                     sub=[('<div class="k-popup"><ul aria-hidden="false"><li>Admin</li></ul></div>', '<div class="k-popup"><div class="k-list"><ul aria-hidden="false"><li>Admin</li></ul></div></div>')],
                     heal=[L('input[name="role-filter"]')], why="`ul/..` is the wrapper, which has no preceding span; the input has a name")}),
    E("x58", "xpath axis", "xpath", [L("//div[text()='Start date ']/following::div[contains(@class,'day picked')]/following-sibling::div[1]")], "click",
      html='<div>Start date </div><div class="days"><div class="day">14</div><div class="day picked">15</div><div class="day"{H}>16</div></div>',
      src="the day after the picked one, by `following::` and a label with a trailing space, Selenium java",
      muts={"v11": M("the label loses its trailing space: `Start date ` -> `Start date`", sub=[("<div>Start date </div>", "<div>Start date</div>")],
                     heal=[L("div.day.picked + div.day")], why="`text()='Start date '` is an exact match; the state class is the stable anchor")}),

    # ------------------------------------------------------------------------------------------------ data attributes
    E("d40", "data attribute", "data", [L("data-test=category-space-permission >> .MuiSelect-select")], "click",
      html='<div data-test="category-space-permission"><div class="MuiInputBase-root"><div class="MuiSelect-select MuiSelect-outlined" role="combobox"{H}>Admin</div></div></div>',
      src="a select inside a test-id container, with a non-default test-id attribute name, Playwright ts",
      muts={"v11": M("`data-test` -> `data-testid`", sub=[('data-test="category', 'data-testid="category')],
                     heal=[TID("category-space-permission"), L(".MuiSelect-select")], why="the attribute name changed; the default test id attribute now carries it")}),
    E("d41", "data attribute", "data", [L('select[name*="role" i], [data-testid*="role-select"], [role="listbox"]:near(:text("role"))')], ("select", "editor"),
      html='<label>Role</label><select name="user_role" data-testid="role-picker"{H}><option value="viewer">Viewer</option><option value="editor">Editor</option></select>',
      src="a union of three strategies for a role picker that may be native or custom, Playwright ts",
      muts={"v11": M("select name `user_role` -> `permission`", sub=[('name="user_role"', 'name="permission"')],
                     heal=[TID("role-picker")], why="none of the three strategies matches any more; the select has a test id")}),
    E("d42", "data attribute", "data", [L('form:nth-of-type(2) >> [data-qa="add-to-shopping-cart"]')], "click",
      html='<form><button type="button" data-qa="add-to-shopping-cart">Add</button></form>'
           '<form><button type="button" data-qa="add-to-shopping-cart"{H}>Add</button></form>'
           '<form><button type="button" data-qa="add-to-shopping-cart">Add</button></form>',
      src="a data-qa button scoped by an index-based parent form (the helper's template filled with a form selector), Playwright ts",
      muts={"v12": M("the second form's button moves into an embedded cart iframe (a payment-widget style change)",
                     html='<form><button type="button" data-qa="add-to-shopping-cart">Add</button></form>'
                          '<form><iframe title="Cart widget" src="frame-cart.html"></iframe></form>'
                          '<form><button type="button" data-qa="add-to-shopping-cart">Add</button></form>',
                     frame=("cart", '<button type="button" data-qa="add-to-shopping-cart"{H}>Add</button>'),
                     heal=[F('iframe[title="Cart widget"]'), L('[data-qa="add-to-shopping-cart"]')],
                     why="the button is inside a frame; the page-level locator no longer reaches it")}),
    E("d43", "data attribute", "data", [L('[data-qa="vacancy-serp__vacancy"]'), FIRST], "click",
      html='<article data-qa="vacancy-serp__vacancy"{H}>QA engineer</article><article data-qa="vacancy-serp__vacancy">SDET</article>',
      src="a result card on a third-party site, renamed without notice, Playwright ts",
      muts={"v11": M("`data-qa` -> `data-testid` on the cards", sub=[('data-qa="vacancy-serp__vacancy"', 'data-testid="vacancy-serp__vacancy"')],
                     heal=[TID("vacancy-serp__vacancy"), FIRST], why="same value under the test id attribute")}),
    E("d45", "data attribute", "data", [L('[data-id="0"] > [data-field="fqdn"] ')], "click",
      html='<div role="row" data-id="0"><div role="gridcell" data-field="fqdn"{H}>a.example.org</div><div role="gridcell" data-field="status">ok</div></div>'
           '<div role="row" data-id="1"><div role="gridcell" data-field="fqdn">b.example.org</div><div role="gridcell" data-field="status">ok</div></div>',
      src="the fqdn cell of the first data-grid row by row id and field, with a trailing space in the selector, Cypress",
      muts={"v12": M("the data is re-seeded: row ids start at 1000", sub=[('data-id="0"', 'data-id="1000"'), ('data-id="1"', 'data-id="1001"')],
                     heal=[L('[role="row"] [data-field="fqdn"]'), FIRST], why="the row id is data, not a position; the first row's id changed")}),

    # ------------------------------------------------------------------------------------------------ aria
    E("a46", "aria", "aria", [L('button[aria-controls="box-busca-refinada"]')], "click",
      html='<button aria-controls="box-busca-refinada" aria-expanded="false" data-testid="advanced-search-toggle"{H}>Refined search</button><div id="box-busca-refinada" hidden>filters</div>',
      src="a disclosure toggle by the id it controls, Playwright python",
      muts={"v11": M("the controlled id is renamed `box-busca-refinada` -> `box-refined-search`", sub=[("box-busca-refinada", "box-refined-search")],
                     heal=[TID("advanced-search-toggle")], why="aria-controls follows a dev-chosen id; the button has a test id")}),
    E("a47", "aria", "aria", [L('[aria-labelledby="select2-virtualmachinecataloguesyncform-data_center_id-container"]')], "click",
      html='<span class="select2-selection" role="combobox" aria-labelledby="select2-virtualmachinecataloguesyncform-data_center_id-container"{H}>'
           '<span id="select2-virtualmachinecataloguesyncform-data_center_id-container" class="select2-selection__rendered">DC1</span></span>',
      src="a Select2 combobox whose labelledby id embeds the form class and the field name, Playwright ts",
      muts={"v11": M("the form class is renamed: `virtualmachinecataloguesyncform` -> `vmcatalogsyncform`", sub=[("virtualmachinecataloguesyncform", "vmcatalogsyncform")],
                     heal=[L('span.select2-selection[aria-labelledby*="data_center_id"]')], why="the embedded form name changed; the field-name part of the id is stable")}),
    E("a48", "aria", "aria", [L('[aria-label="CSETv1:Physical Objects:no"]')], "check",
      html='<label><input type="radio" name="physical_objects" value="yes" aria-label="CSETv1:Physical Objects:yes"> yes</label>'
           '<label><input type="radio" name="physical_objects" value="no" aria-label="CSETv1:Physical Objects:no"{H}> no</label>',
      src="a taxonomy radio by an aria-label built from taxonomy and field names, Playwright ts",
      muts={"v11": M("taxonomy renamed `CSETv1` -> `CSETv2` in the labels", sub=[("CSETv1", "CSETv2")],
                     heal=[L('input[name="physical_objects"][value="no"]')], why="the label embeds the taxonomy version; name and value are stable")}),
    E("a49", "aria", "aria", [L('//span[@aria-label=" Pending "]')], "click",
      html='<span aria-label=" Pending " data-testid="msg-status"{H}>&#9719;</span>',
      src="a status icon by an aria-label with leading and trailing spaces, Selenium python",
      muts={"v11": M("the label is trimmed: ` Pending ` -> `Pending`", sub=[('aria-label=" Pending "', 'aria-label="Pending"')],
                     heal=[TID("msg-status")], why="the xpath compares the label exactly; the icon has a test id")}),
    E("a50o", "aria", "aria", [R("button", "Actions", exact=True)], "click",
      html='<button popovertarget="row-pop"{H}>Actions</button><div id="row-pop" popover="auto" role="group" aria-label="Row actions"><button data-edit="1"{H:a50}>Edit</button></div>',
      src="trigger of a row-actions popover, Playwright ts"),
    E("a50", "aria", "aria", [L('[popover="auto"][role="group"][aria-label="Row actions"]'), R("button", "Edit")], "click",
      html="", src="a popover found by the html `popover` attribute, role and label, Playwright ts",
      muts={"v11": M("aria-label `Row actions` -> `Actions for row`", also={"a50o": [('aria-label="Row actions"', 'aria-label="Actions for row"')]},
                     heal=[L("#row-pop"), R("button", "Edit")], why="the label is part of the selector; the popover has a hand-written id")}),
    E("a51", "aria", "aria", [L('button[aria-label*="settings" i]'), FIRST], "click",
      html='<button aria-label="Settings"{H}>&#9881;</button><button aria-label="Notification settings">&#128276;</button>',
      src="a settings button by a case-insensitive aria-label substring, Playwright ts", check_assert=False,
      muts={"v12": M("the Settings button is removed, and the notification button is renamed `Alerts`",
                     sub=[('<button aria-label="Settings"{H}>&#9881;</button>', ''), ('aria-label="Notification settings"', 'aria-label="Alerts"')],
                     heal=None, why="the element no longer exists: NONE, never the Alerts button")}),

    # ------------------------------------------------------------------------------------------------ text based
    E("t20", "text", "text", [L("text=Add >> nth=0")], "click",
      html='<button{H}>Add</button><button>Add-on</button><label>Address</label>',
      src="the first element containing a word (substring match) plus an index, Playwright ts",
      muts={"v10": M("an `Address` label is inserted before the buttons", sub=[('<button{H}>Add</button>', '<label>Address</label><button{H}>Add</button>')],
                     heal=[R("button", "Add", exact=True)], why="`nth=0` is the new label (the click does nothing); the button is the exact-name match")}),
    E("t53", "text", "text", [L('div[class*="_switch_"]:has(div[class*="_tit1_"]:text-is("合集"))')], "click",
      html='<div class="Form_switch_3kd9x" data-name="collection"{H}><div class="Form_tit1_a81Zq">合集</div><label class="woo-switch-main"><input class="woo-switch-input" type="checkbox"></label></div>'
           '<div class="Form_switch_3kd9x" data-name="original"><div class="Form_tit1_a81Zq">原创</div><label class="woo-switch-main"><input class="woo-switch-input" type="checkbox"></label></div>',
      src="a toggle row matched by css-module infix classes and an exact CJK label, Playwright python",
      muts={"v11": M("css-module hashes change: `_3kd9x` -> `_9ab1c`, `_a81Zq` -> `_k2Lm7`", sub=[("_3kd9x", "_9ab1c"), ("_a81Zq", "_k2Lm7")],
                     heal="green", why="the selector matches the stable infixes `_switch_` and `_tit1_`, not the hash"),
            "v12": M("the label text changes: `合集` -> `专辑`", sub=[("合集", "专辑")],
                     heal=[L('div[class*="_switch_"][data-name="collection"]')], why="the exact text is part of the selector; the row has a data-name")}),
    E("t54", "text", "text", [L("#tape tr:has(td.c-tool:text-is('Edit')):has-text('billing.py')")], "click",
      html='<table id="tape"><tr data-tool="edit"{H}><td class="c-tool">Edit</td><td>billing.py</td></tr><tr data-tool="read"><td class="c-tool">Read</td><td>billing.py</td></tr>'
           '<tr data-tool="edit"><td class="c-tool">Edit</td><td>app.py</td></tr></table>',
      src="the table row whose tool cell is exactly one word and which mentions a file, Playwright python",
      muts={"v11": M("tool label `Edit` -> `Modify`", sub=[(">Edit<", ">Modify<")], heal=[L('#tape tr[data-tool="edit"]:has-text("billing.py")')],
                     why="the exact text of the tool cell changed; the row has a data-tool")}),
    E("t55", "text", "text", [L("[role='tab']:has(i.google-symbols:text-is('image'))")], "click",
      html='<div role="tablist"><button role="tab" aria-controls="panel-video"><i class="google-symbols">videocam</i></button>'
           '<button role="tab" aria-controls="panel-images"{H}><i class="google-symbols">image</i></button></div>',
      src="a tab identified by an icon-font ligature, Playwright python",
      muts={"v11": M("icon-font update: ligature `image` -> `photo`", sub=[(">image<", ">photo<")],
                     heal=[L('[role="tab"][aria-controls="panel-images"]')], why="ligature names change between icon-font versions; the tab controls a panel")}),
    E("t57", "text", "text", [L("text=/^\\d+%$/"), FIRST], "click",
      html='<span data-testid="zoom-level"{H}>100%</span><span>20% off</span>',
      src="a zoom indicator found by a regex over its text, Playwright ts",
      muts={"v12": M("a progress `45%` is rendered before the zoom indicator", sub=[('<span data-testid="zoom-level"{H}>', '<span>45%</span><span data-testid="zoom-level"{H}>')],
                     heal=[TID("zoom-level")], why="`.first` is the progress label now (wrong element)")}),
    E("t59", "text", "text", [L("xpath=//label[contains(text(), \"What's the patient's birth date?\")]")], "click",
      html='<label for="birthdate"{H}>What\'s the patient\'s birth date?</label><input id="birthdate" type="date">',
      src="a label found by a full sentence, Selenium java (adapted to a Playwright xpath)",
      muts={"v11": M("typographic apostrophes: `What's the patient's` -> `What’s the patient’s`", sub=[("What's the patient's", "What&#8217;s the patient&#8217;s")],
                     heal=[L('label[for="birthdate"]')], why="the sentence uses ASCII apostrophes; the label points at the input by id")}),

    # ------------------------------------------------------------------------------------------------ generated classes and ids
    E("g60", "generated class", "generated", [L(".css-7g0azd"), NTH(0)], "click",
      html='<ul class="assets"><li class="css-7g0azd" data-testid="asset-item"{H}>logo.png</li><li class="css-7g0azd" data-testid="asset-item">hero.png</li></ul>',
      src="list items by an emotion hash class, Playwright ts",
      muts={"v11": M("a style edit changes the emotion hash `css-7g0azd` -> `css-1k4s9d`", sub=[("css-7g0azd", "css-1k4s9d")],
                     heal=[TID("asset-item"), NTH(0)], why="the hash derives from the style content; the items have a test id")}),
    E("g61", "generated class", "generated", [L(".css-1xc3v61-indicatorContainer")], "click",
      html='<div data-testid="region-select" class="css-b62m3t-container"><div class="css-13cymwt-control"><div class="css-hlgwow">'
           '<div class="css-1jqq78o-placeholder" id="react-select-2-placeholder"{H:g63}>Select region</div>'
           '<div class="css-19bb58m"><input id="react-select-2-input" role="combobox" aria-expanded="false" aria-haspopup="true" type="text"></div></div>'
           '<div class="css-1wy0on6"><div class="css-1xc3v61-indicatorContainer" aria-hidden="true"{H}>' + SELECT_CHEVRON + '</div></div></div></div>',
      src="the dropdown arrow of a react-select by its emotion class, Playwright ts",
      muts={"v11": M("emotion hash `css-1xc3v61` -> `css-1xzq8d1`", sub=[("css-1xc3v61-indicatorContainer", "css-1xzq8d1-indicatorContainer")],
                     heal=[L('[data-testid="region-select"] [class$="-indicatorContainer"]')], why="the hash prefix changed; the suffix `-indicatorContainer` and the test-id scope are stable")}),
    E("g63", "generated class", "generated", [L("//div[contains(@id,'react-select-2-placeholder')]")], "click", html="",
      src="the placeholder of a react-select by its instance counter, Playwright ts",
      muts={"v11": M("the instance counter shifts: `react-select-2-` -> `react-select-3-` (another select renders first)",
                     also={"g61": [("react-select-2-", "react-select-3-")]}, heal=[L('[data-testid="region-select"] [id$="-placeholder"]')], why="the counter is render order; the id suffix is stable")}),
    E("g62", "generated class", "generated", [L("#react-select-3-option-6")], "click",
      html='<div data-testid="city-select" class="css-b62m3t-container"><div class="css-13cymwt-control"><div class="css-hlgwow"><input id="react-select-3-input" role="combobox" type="text"></div></div>'
           '<div class="css-1nmdiq5-menu" id="react-select-3-listbox">' + "".join('<div class="css-10wo9uf-option" id="react-select-3-option-%d"%s>City %d</div>' % (i, "{H}" if i == 6 else "", i) for i in range(0, 9)) + '</div></div>',
      src="the 7th option of the 3rd react-select on the page, Playwright ts",
      muts={"v11": M("another select renders first: instance 3 -> 4", sub=[("react-select-3-", "react-select-4-")],
                     heal=[L('[data-testid="city-select"] [id$="-option-6"]')], why="the instance counter changed; the option's index suffix and the select's test id are stable")}),
    E("g65", "generated id", "generated", [L("#\\:Rajsq\\:-form-item")], ("fill", "Jane Doe"),
      html='<input id=":Rajsq:-form-item" name="fullName" type="text"{H}>',
      src="a form input by a server-rendered `useId` id, Playwright ts",
      muts={"v11": M("`useId` output `:Rajsq:` -> `:R1ajsq:`", sub=[(":Rajsq:", ":R1ajsq:")], heal=[L('input[name="fullName"]')],
                     why="the id follows render order; the input has a name")}),
    E("g66", "generated id", "generated", [L("#\\:r1\\:-helper-text")], "click",
      html='<div><input id=":r1:" aria-describedby=":r1:-helper-text"><p id=":r1:-helper-text"{H}>Email is required</p></div>',
      src="the helper text of an MUI field by its react 18 `useId` id, Playwright ts",
      muts={"v12": M("`:r1:` -> `:r4:` (three ids are issued earlier on the page)", sub=[(":r1:", ":r4:")], heal=[L('[id$="-helper-text"]')],
                     why="the counter changed; the `-helper-text` suffix is the part the library defines")}),
    E("g67", "generated class", "generated", [L(".MuiTreeView-root ul > div > div > li:nth-child(3) button:nth-child(2)")], "click",
      chain=".MuiTreeView-root ul > div > div > li:nth-child(3) button:nth-child(2)", text="Delete", leaf={"aria-label": "Delete file"},
      src="the delete button of the third tree item by a component-library class and two positions, Cypress",
      muts={"v11": M("the library renames `MuiTreeView-root` -> `MuiSimpleTreeView-root` (major version)",
                     chain=".MuiSimpleTreeView-root ul > div > div > li:nth-child(3) button:nth-child(2)",
                     heal=[R("button", "Delete file")], why="the root class changed; the button has an aria-label")}),

    # ------------------------------------------------------------------------------------------------ frames
    E("f71a", "frame", "frames", [F('iframe[name^="__privateStripeFrame"]'), FIRST, L('[name="number"]')], ("fill", "4242424242424242"),
      html='<iframe name="__privateStripeFrame1234" title="Secure card payment" src="frame-card.html"></iframe>', fname="card",
      fhtml='<input name="number" placeholder="Card number"{H}>',
      src="a payment-element field inside a randomly named iframe, Playwright ts",
      muts={"v11": M("the random frame suffix changes: `__privateStripeFrame1234` -> `__privateStripeFrame9876`", sub=[("1234", "9876")],
                     heal="green", why="the selector matches the stable prefix `__privateStripeFrame`"),
            "v12": M("the element generation changes the field name `number` -> `cardnumber`", fsub=[('name="number"', 'name="cardnumber"')],
                     heal=[F('iframe[name^="__privateStripeFrame"]'), FIRST, L('[name="cardnumber"]')], why="the field name differs between element generations")}),
    E("f71b", "frame", "frames", [F('iframe[name^="__privateStripeFrame"]'), FIRST, L('[name="expiry"]')], ("fill", "1230"), fname="card",
      fhtml='<input name="expiry" placeholder="MM / YY"{H}>', src="a payment-element field inside a randomly named iframe, Playwright ts",
      muts={"v12": M("`expiry` -> `exp-date`", fsub=[('name="expiry"', 'name="exp-date"')],
                     heal=[F('iframe[name^="__privateStripeFrame"]'), FIRST, L('[name="exp-date"]')], why="the field name differs between element generations")}),
    E("f73", "frame", "frames", [F("iframe[name^='ApxSecureIframe']"), R("textbox", "Card number")], ("fill", "4111111111111111"),
      html='<iframe name="ApxSecureIframe_k3j2h" title="Secure payment" src="frame-amazon.html"></iframe>', fname="amazon",
      fhtml='<label for="cn">Card number</label><input id="cn" type="text"{H}>',
      src="an Amazon-style payment iframe by a name prefix, then a textbox by name, Playwright python",
      muts={"v11": M("the frame name `ApxSecureIframe...` -> `AmazonPayFrame...`", sub=[("ApxSecureIframe_k3j2h", "AmazonPayFrame_k3j2h")],
                     heal=[F('iframe[title="Secure payment"]'), R("textbox", "Card number")], why="the prefix is gone; the frame has a title")}),
    E("f75", "frame", "frames", [F('iframe[name="editor-canvas"]'), L(".wp-block-post-title")], "click",
      html='<iframe name="editor-canvas" title="Editor canvas" src="frame-editor.html"></iframe>', fname="editor",
      fhtml='<h1 class="wp-block-post-title" contenteditable="true"{H}>Hello world</h1>',
      src="a block-editor canvas that only exists in iframed-editor mode, Playwright python",
      muts={"v12": M("the editor is no longer iframed: the title is in the page", no_frame=True,
                     html='<h1 class="wp-block-post-title" contenteditable="true"{H}>Hello world</h1>',
                     heal=[L(".wp-block-post-title")], why="there is no `editor-canvas` frame any more")}),
    E("f74", "frame", "frames-nested", [F("iframe"), FIRST, F('iframe[name="layui-layer-iframe1"]'), L("text=确认")], "click",
      html='<iframe title="Customs form" src="frame-outer.html"></iframe>', fname="outer",
      fhtml='<iframe name="layui-layer-iframe1" title="Confirm dialog" src="frame-inner.html"></iframe>',
      src="a confirm button in a layer iframe nested in the first iframe of the page, Playwright python",
      muts={"v12": M("the layer counter increments: `layui-layer-iframe1` -> `layui-layer-iframe2`", fsub=[("layui-layer-iframe1", "layui-layer-iframe2")],
                     heal=[F("iframe"), FIRST, F('iframe[title="Confirm dialog"]'), L("text=确认")], why="the name ends in a counter; the frame has a title")}),
    E("f74i", "frame", "frames-nested", None, None, fname="inner", fhtml='<button{H:f74}>确认</button>', src=""),

    # ------------------------------------------------------------------------------------------------ shadow DOM
    E("w77", "shadow dom", "shadow", [L('ion-searchbar[data-testid="search-filter-input"] input')], ("fill", "cash"),
      html='<ion-searchbar data-testid="search-filter-input" data-hit-inner="w77"></ion-searchbar>',
      src="an input inside a component library's open shadow root, test id on the host, Playwright ts"),
    E("w78", "shadow dom", "shadow", [L('ion-textarea[data-testid="group-note-textarea"] textarea')], ("fill", "Quarterly review"),
      html='<ion-textarea data-testid="group-note-textarea" data-hit-inner="w78"></ion-textarea>',
      src="a textarea inside a component library's open shadow root, Playwright ts"),
    E("w79", "shadow dom", "shadow", [L("lightning-input.pipelineInspectionToggle lightning-primitive-input-toggle")], "click",
      html='<lightning-input class="pipelineInspectionToggle" data-hit-inner="w79"></lightning-input>',
      src="a toggle reached through an internal primitive component of a vendor library, Playwright ts",
      muts={"v11": M("the vendor renames its internal component: `lightning-primitive-input-toggle` -> `lightning-input-toggle`",
                     sub=[("<lightning-input ", '<lightning-input data-inner="lightning-input-toggle" ')],
                     heal=[L('lightning-input.pipelineInspectionToggle input[type="checkbox"]')],
                     why="the internal tag changed in a vendor release; the checkbox inside it is still an input of type checkbox")}),
    E("w80", "shadow dom", "shadow", [L('section[role="dialog"] div div button:not(:disabled):nth-child(2)')], "click",
      html='<section role="dialog"><div><div><button disabled>Back</button><button data-action="accept"{H}>Accept</button></div></div></section>',
      src="the accept button of a vendor dialog by anonymous divs, a state pseudo-class and a position, Playwright ts",
      muts={"v10": M("a `Close` button is added first in the button row",
                     sub=[("<button disabled>Back</button>", "<button>Close</button><button disabled>Back</button>")],
                     heal=[L('section[role="dialog"] button[data-action="accept"]')], why="`:nth-child(2)` is now the disabled Back button (nothing matches)")}),

    # ------------------------------------------------------------------------------------------------ scoped / chained playwright
    E("c84", "scoped chain", "scoped", [L(".ant-select-dropdown:not(.ant-select-dropdown-hidden)"), LAST, L(".ant-select-item-option"), T("Marketing", exact=True)], "click",
      html='<div class="ant-select-dropdown"><div class="ant-select-item ant-select-item-option" role="option"><div class="ant-select-item-option-content"{H}>Marketing</div></div></div>'
           '<div class="ant-select-dropdown ant-select-dropdown-hidden"><div class="ant-select-item ant-select-item-option" role="option"><div class="ant-select-item-option-content">Marketing (old)</div></div></div>',
      src="an option of the visible dropdown among stale hidden copies left in the portal, Playwright ts",
      muts={"v11": M("the hidden-state class `ant-select-dropdown-hidden` -> `ant-select-dropdown-hide stale`", sub=[("ant-select-dropdown-hidden", "ant-select-dropdown-hide stale")],
                     heal=[R("option", "Marketing", exact=True)], why="the negation no longer excludes the stale copy, so `.last` is the hidden one; role+name ignores hidden elements")}),
    E("c85", "scoped chain", "scoped", [L("mat-menu"), FH([L('button:has-text("Add New Tag")')])], "click",
      html='<mat-menu data-menu="sort"><button>Sort asc</button></mat-menu><mat-menu data-menu="tag"{H}><button>Add New Tag</button></mat-menu>',
      src="the menu that contains a given button, by a relational filter, Playwright ts",
      muts={"v11": M("copy `Add New Tag` -> `New tag`", sub=[("Add New Tag", "New tag")], heal=[L('mat-menu[data-menu="tag"]')],
                     why="the filter keys on the button's text; the menu has a data attribute")}),
    E("c86", "scoped chain", "scoped", [L(".agent-option"), FH([L(".agent-option-name", has_text_re="^Alpha$")])], "click",
      html='<div class="agent-option" data-agent="alpha"{H}><span class="agent-option-name">Alpha</span></div><div class="agent-option" data-agent="beta"><span class="agent-option-name">Beta</span></div>',
      src="an option whose name cell matches an exact regex, Playwright ts",
      muts={"v11": M("a badge is added to the name: `Alpha` -> `Alpha <i>new</i>`", sub=[(">Alpha<", ">Alpha <i>new</i><")],
                     heal=[L('.agent-option[data-agent="alpha"]')], why="`^Alpha$` is anchored; the option has a data-agent")}),
    E("c87", "scoped chain", "scoped", [L("div", has_text="E2E Sync Store"), FH([L("text=Local Disk")]), FIRST], "click",
      html='<div class="store-card" data-testid="store-card"{H}><h4>E2E Sync Store</h4><span>Local Disk</span></div><div class="store-card" data-testid="store-card"><h4>Archive</h4><span>S3</span></div>',
      src="the card that holds two texts; `div` matches every ancestor so `.first` is needed, Playwright ts",
      muts={"v10": M("the cards are wrapped in a `stores` div", sub=[('<div class="store-card" data-testid="store-card"{H}>', '<div class="stores"><div class="store-card" data-testid="store-card"{H}>'),
                                                                       ('<span>S3</span></div>', '<span>S3</span></div></div>')],
                     heal=[TID("store-card"), FT("E2E Sync Store")], why="the wrapper holds both texts and is first in document order (wrong element)")}),
    E("c88", "scoped chain", "scoped", [T("Row A", exact=True), L("xpath=ancestor::*[@data-testid='on-call-direct-page']"), TID("on-call-direct-page-label")], "click",
      html='<div data-testid="on-call-direct-page"><span>Row A</span><b data-testid="on-call-direct-page-label"{H}>Page</b></div>',
      src="a label reached by climbing from a matched part and descending by test id, Playwright ts",
      muts={"v11": M("the container's test id is renamed `on-call-direct-page` -> `oncall-page-card`", sub=[('data-testid="on-call-direct-page"', 'data-testid="oncall-page-card"')],
                     heal=[TID("on-call-direct-page-label")], why="the climb's anchor is gone; the label has its own test id")}),
    E("c89", "scoped chain", "scoped", [L(".ant-table-row"), FIRST, L('button:has(svg[data-icon="pen-to-square"])')], "click",
      html='<table><tbody><tr class="ant-table-row"><td>Welcome</td><td><button aria-label="Edit template"{H}><svg data-icon="pen-to-square" viewBox="0 0 8 8" width="12" height="12"><path d="M0 0h8v8z"/></svg></button></td></tr></tbody></table>',
      src="the edit button of the first row, identified by its icon's data-icon name, Playwright ts",
      muts={"v11": M("icon library update: `pen-to-square` -> `edit`", sub=[("pen-to-square", "edit")], heal=[L(".ant-table-row"), FIRST, R("button", "Edit template")],
                     why="the icon was renamed; the button has an accessible name")}),
    E("c90", "scoped chain", "scoped", [L("select"), FH([L("option[value='pending']")]), FIRST], ("select", "approved"),
      html='<select name="status"{H}><option value="pending">Pending</option><option value="approved">Approved</option></select>',
      src="a status filter found by one of its options, Playwright ts",
      muts={"v12": M("a priority select that also offers `pending` is added before it", sub=[('<select name="status"{H}>', '<select name="priority"><option value="pending">Pending</option><option value="urgent">Urgent</option></select><select name="status"{H}>')],
                     heal=[L('select[name="status"]')], why="`.first` is the new priority select (wrong element)")}),
    E("c91", "scoped chain", "scoped", [L('button[role=switch]:right-of(:text("Versioning"))')], "click",
      html='<div class="rail"><span>Versioning</span><button role="switch" aria-checked="false" data-testid="versioning-switch"{H}>off</button></div>',
      src="a switch to the right of a label (a geometry selector), Playwright ts",
      muts={"v10": M("the row stacks vertically (the switch is under its label)", sub=[('<div class="rail">', '<div class="rail col">')],
                     heal=[TID("versioning-switch")], why="`:right-of` depends on layout; the switch has a test id")}),
    E("c93", "scoped chain", "scoped", [T("Ban user until"), L(".."), L(".DatePicker-wrapper input")], ("fill", "2026-12-01"),
      html='<div class="ban-row"><label>Ban user until</label><div class="DatePicker-wrapper"><input name="banUntil" type="text"{H}></div></div>',
      src="a field reached through a parent hop from its label, Playwright ts",
      muts={"v10": M("the label is wrapped in a span", sub=[("<label>Ban user until</label>", '<span class="lbl"><label>Ban user until</label></span>')],
                     heal=[L('input[name="banUntil"]')], why="`..` is the span now and has no date picker inside")}),

    # ------------------------------------------------------------------------------------------------ component libraries
    E("m94", "component library", "components", [L(".MuiDataGrid-menu > div > .MuiList-root"), L("xpath=./*"), NTH(3)], "click",
      html='<div class="MuiDataGrid-menu"><div><ul class="MuiList-root"><li role="menuitem">Sort ascending</li><li role="menuitem">Sort descending</li><li role="menuitem">Hide</li><li role="menuitem"{H}>Filter</li></ul></div></div>',
      src="the 4th item of a data-grid column menu, Cypress (`.children().eq(3)` as a Playwright xpath step)",
      muts={"v10": M("the Pro edition adds a `Pin` item before Filter", sub=[('<li role="menuitem"{H}>Filter', '<li role="menuitem">Pin</li><li role="menuitem"{H}>Filter')],
                     heal=[R("menuitem", "Filter")], why="`nth(3)` is Pin now (wrong element)")}),
    E("m97", "component library", "components", [L("#menu-cardRegion ul.MuiMenu-list li"), NTH(2)], "click",
      html='<div id="menu-cardRegion" class="MuiPopover-root"><div class="MuiPaper-root"><ul class="MuiList-root MuiMenu-list" role="listbox"><li role="option" data-value="NA">North America</li><li role="option" data-value="SA">South America</li><li role="option" data-value="EU"{H}>Europe</li></ul></div></div>',
      src="an option of a select popup rendered in a portal, id taken from the field name, Playwright ts",
      muts={"v11": M("the field is renamed: popup id `menu-cardRegion` -> `menu-region`", sub=[("menu-cardRegion", "menu-region")], heal=[L('li[data-value="EU"]')],
                     why="the popup id derives from the field name; the option has a data-value")}),
    E("a100", "component library", "components", [L('[data-cy=run-detail-header] .ant-tabs-nav-list div:nth-child(2)')], "click",
      html='<div data-cy="run-detail-header"><div class="ant-tabs-nav-list"><div class="ant-tabs-tab" data-node-key="1"><div role="tab">Trigger</div></div><div class="ant-tabs-tab" data-node-key="2"{H}><div role="tab">Test</div></div></div></div>',
      src="the second tab of a run page by a data-cy header and a position in a design-system tab list, Cypress",
      muts={"v10": M("v5 adds an `add` button as the first child of the tab list", sub=[('<div class="ant-tabs-nav-list">', '<div class="ant-tabs-nav-list"><div class="ant-tabs-nav-add">+</div>')],
                     heal=[L('[data-cy=run-detail-header] [data-node-key="2"]')], why="`div:nth-child(2)` is the first tab now (wrong element)")}),
    E("rs101a", "component library", "components", [L("#country input")], ("fill", "Far"),
      html='<div id="country"><div class="react-select__control"><div class="react-select__value-container"><input class="react-select__input" type="text"{H}></div></div>'
           '<div class="react-select__menu"><div class="react-select__option"{H:rs101b}>Farajaland</div><div class="react-select__option">Falkland</div></div></div>',
      src="a react-select input scoped by the field's id, Playwright ts"),
    E("rs101b", "component library", "components", [L("#country .react-select__option"), NTH(0)], "click", html="",
      src="the first option of a react-select (rendered as plain divs) by the class prefix, Playwright ts",
      muts={"v11": M("`classNamePrefix` renamed `react-select` -> `rs`, and v5 adds `role=option`",
                     also={"rs101a": [("react-select__", "rs__"), ('class="rs__option"', 'class="rs__option" role="option"')]}, heal=[L("#country"), R("option"), NTH(0)], why="the prefix is gone; options carry a role now")}),
    E("ag103", "component library", "components", [L('.ag-row[row-index="3"] .ag-cell[col-id="name"]')], "click", html=ag_html(),
      src="a grid cell by the display row index and the column id, Playwright ts (the grid vendor's own harness)",
      muts={"v12": M("the grid is sorted: display order 40 41 42 44 43 (row ids keep their data)", html=ag_html(order=[40, 41, 42, 44, 43]),
                     heal=[L('.ag-row[row-id="43"] .ag-cell[col-id="name"]')],
                     why="`row-index` is the display position, so index 3 is another row now (wrong cell, the assertion fails); `row-id` follows the data")}),
    E("ag104", "component library", "components", [L(".ag-center-cols-container .ag-row[role='row'] .ag-cell[col-id='name']"), NTH(1)], "click", html="",
      src="name cells of the unpinned (center) columns, Selenium python (adapted to a Playwright nth)",
      muts={"v10": M("the Name column is pinned left", also_html={"ag103": ag_html(pinned=True)},
                     heal=[L('.ag-row[row-id="41"] .ag-cell[col-id="name"]')], why="pinned cells live in `.ag-pinned-left-cols-container`, so the center container has no name cells")}),
    E("ag105", "component library", "components", [L(".ag-row", has_text="Needs you"), L("[col-id=buyer]")], "click", html="",
      src="the buyer cell of the row whose status text is `Needs you`, Playwright python",
      muts={"v11": M("copy `Needs you` -> `Action required`", also={"ag103": [("Needs you", "Action required")]},
                     heal=[L('.ag-row[row-id="44"] [col-id="buyer"]')], why="the row is found by its status text; the row id is stable")}),
    E("s109", "component library", "components", [L("#addAssetModal-assets + .select2 .select2-selection")], "click",
      html='<select id="addAssetModal-assets" hidden></select><span class="select2 select2-container"><span class="selection"><span class="select2-selection" aria-labelledby="select2-addAssetModal-assets-container"{H}>Choose asset</span></span></span>',
      src="a Select2 widget injected as the sibling of the hidden select, by an adjacent-sibling selector, Playwright ts",
      muts={"v10": M("a hint span is inserted between the select and the widget", sub=[('<span class="select2 select2-container">', '<span class="hint">pick one</span><span class="select2 select2-container">')],
                     heal=[L('[aria-labelledby="select2-addAssetModal-assets-container"]')], why="`+` needs the widget to be the immediate sibling")}),
    E("s111", "component library", "components", [L(".react-datepicker__day--001:not(.react-datepicker__day--outside-month)")], "click",
      html='<div class="react-datepicker__week">' + dp_day("react-datepicker__day--031 react-datepicker__day--outside-month", 31, "Thursday, October 31st, 2024")
           + dp_day("react-datepicker__day--001", 1, "Friday, November 1st, 2024", "0", "{H}")
           + dp_day("react-datepicker__day--002", 2, "Saturday, November 2nd, 2024") + '</div>'
           '<div class="react-datepicker__week">' + dp_day("react-datepicker__day--030", 30, "Saturday, November 30th, 2024")
           + dp_day("react-datepicker__day--001 react-datepicker__day--outside-month", 1, "Sunday, December 1st, 2024") + '</div>',
      src="the 1st of the displayed month, excluding the next month's leading cell, Playwright ts",
      muts={"v11": M("`--outside-month` -> `--outside`", sub=[("--outside-month", "--outside")],
                     heal=[L(".react-datepicker__day--001:not(.react-datepicker__day--outside)")], why="the negated modifier was renamed, so both `--001` cells match (strict mode error)")}),
    E("s113", "component library", "components", [L("[data-headlessui-portal]"), R("option", "Beta", exact=True)], "click",
      html='<div data-headlessui-portal=""><ul role="listbox"><li role="option">Alpha</li><li role="option"{H}>Beta</li></ul></div>',
      src="an option inside a headless-ui portal root, Playwright ts",
      muts={"v11": M("the portal marker `data-headlessui-portal` -> `data-headlessui-portal-root`", sub=[("data-headlessui-portal", "data-headlessui-portal-root")],
                     heal=[R("option", "Beta", exact=True)], why="the root attribute is renamed; the option's role and name are enough")}),
]
