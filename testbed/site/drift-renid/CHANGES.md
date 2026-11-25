# drift-renid - an id is renamed

The display-name input's id changes from `display-name` to `profile-name` (the label's `for` follows). Its test id `display-name-input` and name are unchanged, so the same element still has a better locator than the new id.

Test lines are looked up by locator text in `tests/python/test_drift.py`. A line that is not listed still passes.

- `page.locator("#display-name")` in `test_profile_settings`: heal onto `page.get_by_test_id("display-name-input")`; expected {"branch": false, "outcome": "healed", "settled_at": 2, "tier_movement": "up"}
