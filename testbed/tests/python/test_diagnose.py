import os

from playwright.sync_api import Page, expect

TIMEOUT = int(os.environ.get("QA_ACTION_TIMEOUT_MS", "5000"))

BASE = os.environ.get("QA_BASE_URL", "http://127.0.0.1:8765/drift-base/")
if not BASE.endswith("/"):
    BASE += "/"


def test_save_profile(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "index.html")
    page.locator("#display-name").fill("Jane Doe")
    page.get_by_role("button", name="Save changes").click()
    expect(page.get_by_test_id("summary")).to_have_text("saved: profile newsletter=no", timeout=TIMEOUT)


def test_save_short_name(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "index.html")
    page.locator("#display-name").fill("Jo")
    page.get_by_role("button", name="Save changes").click()
    expect(page.get_by_test_id("summary")).to_contain_text("saved", timeout=TIMEOUT)


def test_delete_account(page: Page):
    page.set_default_timeout(TIMEOUT)
    page.goto(BASE + "index.html")
    page.get_by_role("button", name="Delete account").click()
    expect(page.get_by_test_id("summary")).to_have_text("deleted: account", timeout=TIMEOUT)
