#!/usr/bin/env python3
"""
===============================================================================
Diagnose Spec Builder (site/diag-* variants, their CHANGES.md and golden-diagnose.json)
===============================================================================
Regenerates the diagnose spec's variants from site/drift-base, the "Account settings" page. Each variant breaks the page in one way
that is NOT locator drift: every locator in tests/python/test_diagnose.py still matches, and the test fails on an assertion or a
wait. A variant is a full copy of the base with strict patches (a patch whose text is not found exactly once stops the build).
golden-diagnose.json holds the cases run_diagnose.py reads: which variant, which test, which earlier heals were applied to the
test (`prior_heals`: `applied` false is a heal a person already reverted, `file` another is a heal in a file that is not sent),
and the key (`expect`): the cause a correct diagnosis names, the prior heal to revert (an index into prior_heals) and the edit.

Features:
    diag-reworded  the save message is worded differently            -> stale_expectation, edit the expected text
    diag-noop      the Save button's handler never matches its id    -> app_bug, no edit (an edit would hide the bug)
    diag-slow      the save message appears 4 s after the click      -> timing, raise the wait
    diag-strict    Save refuses a display name under 3 characters    -> test_data_or_env, no edit
    diag-confirm   Delete asks "Confirm" in a dialog first           -> flow_changed, no edit (a new step is a new call)
    drift-dup      (existing) a second "Save changes" button         -> earlier_heal_wrong_element when a heal chose the second one
    drift-gone     (existing) the Delete button is removed           -> not_applicable: the locator no longer matches, that is a heal

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
BASE = os.path.join(SITE, "drift-base")

SAVE_SAY = "say('saved: profile newsletter=' + (box && box.checked ? 'yes' : 'no'));"
SAVE_CALL = 'page.get_by_role("button", name="Save changes").click()'
WRONG_SAVE = 'page.get_by_test_id("save-notifications").click()'
DELETE_CALL = 'page.get_by_role("button", name="Delete account").click()'
EXPECT_SAVED = '"saved: profile newsletter=no"'
TIMING_PATCH = {"from": EXPECT_SAVED + ", timeout=TIMEOUT)", "to": EXPECT_SAVED + ", timeout=10000)"}     # unique in the suite
DIALOG = '''  <div id="confirm" role="dialog" aria-label="Confirm deletion" hidden>
    <p>Delete this account?</p>
    <div class="actions"><button type="button" id="confirm-delete">Confirm</button> <button type="button" id="cancel-delete">Cancel</button></div>
  </div>
'''

VARIANTS = {
    "diag-reworded": {
        "title": "the save message is reworded",
        "text": "The status line after Save now reads `Settings saved (newsletter: no)` instead of `saved: profile newsletter=no`. "
                "The page works; the test still expects the old words.",
        "edits": [("app.js", [(SAVE_SAY, "say('Settings saved (newsletter: ' + (box && box.checked ? 'yes' : 'no') + ')');")])],
    },
    "diag-noop": {
        "title": "Save does nothing",
        "text": "The Save handler is registered under the action name `save-profile`, but the button's id is `save`, so clicking it "
                "does nothing and the status stays `idle`. A broken page, not a wrong test.",
        "edits": [("app.js", [("if (action === 'save') {", "if (action === 'save-profile') {")])],
    },
    "diag-slow": {
        "title": "the save message is late",
        "text": "Save answers correctly but the status line changes 4 seconds after the click; the suites wait 1.5 seconds.",
        "edits": [("app.js", [(SAVE_SAY, "var text = 'saved: profile newsletter=' + (box && box.checked ? 'yes' : 'no');\n"
                                         "      setTimeout(function () { say(text); }, 4000);")])],
    },
    "diag-strict": {
        "title": "a short display name is refused",
        "text": "Save now refuses a display name under 3 characters and says `error: display name needs at least 3 characters`. "
                "A test that types a 2-character name no longer saves.",
        "edits": [("app.js", [("      var box = document.querySelector('[data-pref=newsletter]');\n      " + SAVE_SAY,
                               "      var name = document.getElementById('display-name').value;\n"
                               "      if (name.trim().length < 3) { say('error: display name needs at least 3 characters'); return; }\n"
                               "      var box = document.querySelector('[data-pref=newsletter]');\n      " + SAVE_SAY)])],
    },
    "diag-confirm": {
        "title": "Delete asks for confirmation",
        "text": "Delete account opens a dialog (`Confirm deletion`) and nothing is deleted until its Confirm button is clicked: a new "
                "step in the journey.",
        "edits": [("index.html", [('  <p class="status"', DIALOG + '  <p class="status"')]),
                  ("app.js", [("    } else if (action === 'delete') {\n      say('deleted: account');",
                               "    } else if (action === 'delete') {\n      document.getElementById('confirm').hidden = false;\n"
                               "    } else if (action === 'confirm-delete') {\n      document.getElementById('confirm').hidden = true;\n"
                               "      say('deleted: account');")])],
    },
}

WRONG_HEAL = {"from": SAVE_CALL, "to": WRONG_SAVE, "applied": True, "anchor": SAVE_CALL}
REVERTED_HEAL = {"from": 'page.locator("#name")', "to": 'page.get_by_label("Display name")', "applied": False,
                 "anchor": 'page.locator("#display-name").fill('}
ELSEWHERE_HEAL = {"from": 'page.locator("#save-button")', "to": 'page.get_by_role("button", name="Save changes")', "applied": False,
                  "file": "pages/profile_page.py", "line": 12}

CASES = [
    {"id": "stale_expectation_reworded", "variant": "diag-reworded", "test": "test_save_profile", "prior_heals": [],
     "expect": {"cause": "stale_expectation", "revert_idx": None,
                "patch": {"from": EXPECT_SAVED, "to": '"Settings saved (newsletter: no)"'}}},
    {"id": "app_bug_dead_button", "variant": "diag-noop", "test": "test_save_profile", "prior_heals": [],
     "expect": {"cause": "app_bug", "revert_idx": None, "patch": None}},
    {"id": "timing_late_render", "variant": "diag-slow", "test": "test_save_profile", "prior_heals": [],
     "expect": {"cause": "timing", "revert_idx": None, "patch": TIMING_PATCH}},
    {"id": "data_name_too_short", "variant": "diag-strict", "test": "test_save_short_name", "prior_heals": [],
     "expect": {"cause": "test_data_or_env", "revert_idx": None, "patch": None}},
    {"id": "flow_confirmation_step", "variant": "diag-confirm", "test": "test_delete_account", "prior_heals": [],
     "expect": {"cause": "flow_changed", "revert_idx": None, "patch": None}},                  # a new step is a new call: no edit
    {"id": "earlier_heal_chose_the_other_button", "variant": "drift-dup", "test": "test_save_profile", "prior_heals": [WRONG_HEAL],
     "expect": {"cause": "earlier_heal_wrong_element", "revert_idx": 0, "patch": None}},
    {"id": "reverted_heal_is_no_cause", "variant": "diag-noop", "test": "test_save_profile", "prior_heals": [REVERTED_HEAL],
     "expect": {"cause": "app_bug", "revert_idx": None, "patch": None}},
    {"id": "heal_elsewhere_is_no_cause", "variant": "diag-slow", "test": "test_save_profile", "prior_heals": [ELSEWHERE_HEAL],
     "expect": {"cause": "timing", "revert_idx": None, "patch": TIMING_PATCH}},
    {"id": "locator_gone_is_a_heal", "variant": "drift-gone", "test": "test_delete_account", "prior_heals": [],
     "expect": {"status": "not_applicable"}},
]


def build():
    for name, spec in VARIANTS.items():
        out = os.path.join(SITE, name)
        if os.path.exists(out):
            shutil.rmtree(out)
        shutil.copytree(BASE, out)
        for fname, patches in spec["edits"]:
            path = os.path.join(out, fname)
            text = open(path, encoding="utf-8").read()
            for old, new in patches:
                assert text.count(old) == 1, f"{name}: {old!r} found {text.count(old)} times"
                text = text.replace(old, new)
            open(path, "w", encoding="utf-8").write(text)
        lines = [f"# {name} - {spec['title']}", "", spec["text"], "",
                 "No locator in `tests/python/test_diagnose.py` breaks: the failure is an assertion or a wait. The cases that run "
                 "on this variant are in `golden-diagnose.json`.", ""]
        for c in (c for c in CASES if c["variant"] == name):
            lines.append(f"- `{c['test']}` ({c['id']}): a correct diagnosis says {json.dumps(c['expect'], sort_keys=True)}")
        open(os.path.join(out, "CHANGES.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    with open(os.path.join(HERE, "golden-diagnose.json"), "w", encoding="utf-8") as f:
        json.dump({"cases": CASES}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("built", ", ".join(VARIANTS), "and golden-diagnose.json")


if __name__ == "__main__":
    build()
