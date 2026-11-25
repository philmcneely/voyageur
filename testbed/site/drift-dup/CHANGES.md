# drift-dup - a duplicate button

A Notifications section is added with a second button whose text is also "Save changes" (its own id and test id). `get_by_role("button", name="Save changes")` now matches two elements. The right heal is the profile button, the one the test's assertion (`saved: profile ...`) is about.

Test lines are looked up by locator text in `tests/python/test_drift.py`. A line that is not listed still passes.

- `page.get_by_role("button", name="Save changes")` in `test_profile_settings`: heal onto `page.locator("#save")`; expected {"branch": false, "outcome": "healed", "settled_at": 2, "tier_movement": "up"}
