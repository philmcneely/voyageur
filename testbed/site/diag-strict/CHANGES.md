# diag-strict - a short display name is refused

Save now refuses a display name under 3 characters and says `error: display name needs at least 3 characters`. A test that types a 2-character name no longer saves.

No locator in `tests/python/test_diagnose.py` breaks: the failure is an assertion or a wait. The cases that run on this variant are in `golden-diagnose.json`.

- `test_save_short_name` (data_name_too_short): a correct diagnosis says {"cause": "test_data_or_env", "patch": null, "revert_idx": null}
