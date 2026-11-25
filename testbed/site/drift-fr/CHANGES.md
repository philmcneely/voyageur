# drift-fr - French

The page is translated to French and nothing else changes: `<html lang="fr">`, every label, heading and button text, the same markup, ids, test ids and machine values. Copy locators break; the checkbox has no id or test id, but its plain data-pref attribute is stable in every language, so it heals onto that (a higher tier than copy) and needs no branch; the buttons have ids and test ids and need no branch either. The language branch itself is covered by the french_page_language_branch contract fixture and the signup v6 variant.

Test lines are looked up by locator text in `tests/python/test_drift.py`. A line that is not listed still passes.

- `page.get_by_role("checkbox", name="Email me the newsletter")` in `test_profile_settings`: heal onto `page.locator("[data-pref=newsletter]")`; expected {"branch": false, "outcome": "healed", "tier_movement": "up"}
- `page.get_by_role("button", name="Save changes")` in `test_profile_settings`: heal onto `page.locator("#save")`; expected {"branch": false, "outcome": "healed", "tier_movement": "up"}
- `page.get_by_role("button", name="Delete account")` in `test_delete_account`: heal onto `page.locator("#delete")`; expected {"branch": false, "outcome": "healed", "tier_movement": "up"}
