# diag-confirm - Delete asks for confirmation

Delete account opens a dialog (`Confirm deletion`) and nothing is deleted until its Confirm button is clicked: a new step in the journey.

No locator in `tests/python/test_diagnose.py` breaks: the failure is an assertion or a wait. The cases that run on this variant are in `golden-diagnose.json`.

- `test_delete_account` (flow_confirmation_step): a correct diagnosis says {"cause": "flow_changed", "patch": null, "revert_idx": null}
