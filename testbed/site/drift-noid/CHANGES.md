# drift-noid - a test id is removed

The `data-testid="save-settings"` attribute is removed from the Save button and nothing else changes: the id `save` and the text stay. No test id is left to use, so the heal moves down one tier to the id, and the decision says so.

Test lines are looked up by locator text in `tests/python/test_drift.py`. A line that is not listed still passes.

- `page.get_by_test_id("save-settings")` in `test_save_by_test_id`: heal onto `page.locator("#save")`; expected {"below_floor": true, "branch": false, "outcome": "healed", "tier_movement": "down"}
