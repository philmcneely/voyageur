# diag-reworded - the save message is reworded

The status line after Save now reads `Settings saved (newsletter: no)` instead of `saved: profile newsletter=no`. The page works; the test still expects the old words.

No locator in `tests/python/test_diagnose.py` breaks: the failure is an assertion or a wait. The cases that run on this variant are in `golden-diagnose.json`.

- `test_save_profile` (stale_expectation_reworded): a correct diagnosis says {"cause": "stale_expectation", "patch": {"from": "\"saved: profile newsletter=no\"", "to": "\"Settings saved (newsletter: no)\""}, "revert_idx": null}
