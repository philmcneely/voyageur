import os

from playwright.sync_api import Page, expect

TIMEOUT = int(os.environ.get("QA_ACTION_TIMEOUT_MS", "5000"))

BASE = os.environ.get("QA_BASE_URL", "http://127.0.0.1:8765/orders-base/")
if not BASE.endswith("/"):
    BASE += "/"


def test_orders_grid_and_toolbar(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "index.html")
    page.get_by_test_id("orders-table").locator("tbody tr").nth(3).locator("td").nth(2).locator("button").click()
    expect(page.get_by_test_id("summary")).to_contain_text("status.1043: paid")
    page.locator("//table//tr[3]/td[2]").click()
    expect(page.get_by_test_id("summary")).to_contain_text("selected: 1042")
    page.locator("#app > div > div:nth-child(2) > div.grid > div:nth-child(3) > span.cell").click()
    expect(page.get_by_test_id("summary")).to_contain_text("selected: 1041")
    page.get_by_label("Actions for order 1043").click()
    page.get_by_role("menuitem", name="More").click()
    page.get_by_role("menuitem", name="Archive").click()
    expect(page.get_by_test_id("summary")).to_contain_text("archived: 1043")
    page.get_by_role("row").filter(has_text="Echo GmbH").get_by_role("checkbox").check()
    expect(page.get_by_test_id("summary")).to_contain_text("checked: 1044")
    page.locator("div.toolbar div.filters > button:nth-of-type(2)").click()
    expect(page.get_by_test_id("summary")).to_contain_text("filter: paid")
    expect(page.get_by_test_id("summary")).to_contain_text("visible: 1042,1043,1044")
    page.locator("[data-qa='order-row-1042'] [data-qa='status']").click()
    expect(page.get_by_test_id("summary")).to_contain_text("status.1042: shipped")
    page.locator("[data-cy=export-csv]").click()


def test_side_panel_and_dialog(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "index.html")
    page.locator("#tab-history").click()
    page.locator("[role=tabpanel][aria-labelledby=tab-history] li").first.click()
    expect(page.get_by_test_id("summary")).to_contain_text("event: e301")
    page.get_by_role("tab", name="Notes").click()
    page.locator("#panel-notes > textarea").fill("Call before delivery")
    page.get_by_role("button", name="Save note").click()
    expect(page.get_by_test_id("summary")).to_contain_text("note: Call before delivery")
    page.locator("button.sc-fzXfKy.jQdBvX").click()
    expect(page.get_by_test_id("summary")).to_contain_text("dialog: open")
    page.locator("[role=dialog] select[name=reason]").select_option("duplicate")
    expect(page.get_by_test_id("summary")).to_contain_text("reason: duplicate")
    page.locator("[aria-label='Close dialog']").click()
    expect(page.get_by_test_id("summary")).to_contain_text("dialog: closed")
    page.locator("button.sc-fzXfKy.jQdBvX").click()
    page.get_by_test_id("cancel-dialog").get_by_role("button", name="Confirm cancel").click()
    expect(page.get_by_test_id("summary")).to_contain_text("cancelled: 1040")
    expect(page.get_by_test_id("summary")).to_contain_text("dialog: closed")


def test_payment_frame_and_shadow(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "index.html")
    page.locator("input[name=zip]").fill("73301")
    page.frame_locator("#payment-frame").locator("input[name=card]").fill("4242 4242 4242 4242")
    page.frame_locator("#payment-frame").get_by_label("Expiry").fill("12/30")
    page.frame_locator("#payment-frame").get_by_role("button", name="Pay now").click()
    expect(page.get_by_test_id("summary")).to_contain_text("payment: ok")
    expect(page.get_by_test_id("summary")).to_contain_text("card_last4: 4242")
    expect(page.get_by_test_id("summary")).to_contain_text("exp: 12/30")
    expect(page.get_by_test_id("summary")).to_contain_text("zip: 73301")
    page.locator("order-flag input[name=reason]").fill("Possible fraud")
    page.locator("order-flag button.toggle").click()
    expect(page.get_by_test_id("summary")).to_contain_text("flag: on")
    expect(page.get_by_test_id("summary")).to_contain_text("flag_reason: Possible fraud")
