# diag-noop - Save does nothing

The Save handler is registered under the action name `save-profile`, but the button's id is `save`, so clicking it does nothing and the status stays `idle`. A broken page, not a wrong test.

No locator in `tests/python/test_diagnose.py` breaks: the failure is an assertion or a wait. The cases that run on this variant are in `golden-diagnose.json`.

- `test_save_profile` (app_bug_dead_button): a correct diagnosis says {"cause": "app_bug", "patch": null, "revert_idx": null}
- `test_save_profile` (reverted_heal_is_no_cause): a correct diagnosis says {"cause": "app_bug", "patch": null, "revert_idx": null}
