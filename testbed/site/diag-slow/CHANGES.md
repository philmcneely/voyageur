# diag-slow - the save message is late

Save answers correctly but the status line changes 4 seconds after the click; the suites wait 1.5 seconds.

No locator in `tests/python/test_diagnose.py` breaks: the failure is an assertion or a wait. The cases that run on this variant are in `golden-diagnose.json`.

- `test_save_profile` (timing_late_render): a correct diagnosis says {"cause": "timing", "patch": {"from": "\"saved: profile newsletter=no\", timeout=TIMEOUT)", "to": "\"saved: profile newsletter=no\", timeout=10000)"}, "revert_idx": null}
- `test_save_profile` (heal_elsewhere_is_no_cause): a correct diagnosis says {"cause": "timing", "patch": {"from": "\"saved: profile newsletter=no\", timeout=TIMEOUT)", "to": "\"saved: profile newsletter=no\", timeout=10000)"}, "revert_idx": null}
