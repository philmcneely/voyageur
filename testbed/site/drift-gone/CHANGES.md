# drift-gone - the target is removed

The Delete account button (and its section) is removed. Nothing on the page is the element the test meant; the service must end `needs_human` and never offer the nearest button.

Test lines are looked up by locator text in `tests/python/test_drift.py`. A line that is not listed still passes.

- `page.get_by_role("button", name="Delete account")` in `test_delete_account`: needs_human (the element is gone); expected {"outcome": "needs_human"}
