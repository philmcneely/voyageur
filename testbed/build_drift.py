#!/usr/bin/env python3
"""
===============================================================================
Drift Spec Builder (site/drift-* variants, their CHANGES.md and golden-drift.json)
===============================================================================
Regenerates the drift spec's variants from site/drift-base. The spec is a one-page "Account settings" site with three tests
(tests/python/test_drift.py, tests/ts/drift.spec.ts) and one variant for each situation the service has to get right: a renamed
id, a French page, a duplicate button, a removed test id and a removed target. Each variant is a full copy of the base with strict
patches (a patch whose text is not found exactly once stops the build). golden-drift.json holds, per variant, every broken locator
expression, a Playwright expression for the element a correct heal must end on (null when the element is gone) and what the
service is expected to do; run_acceptance.py reads it.

Features:
    drift-renid   an id is renamed, the test id stays              -> healed to the test id from the fingerprint, no model call
    drift-fr      the page is French (html lang=fr), same markup   -> a language branch for the copy-only checkbox, test ids elsewhere
    drift-dup     a second "Save changes" button is added          -> healed to the original button
    drift-noid    a test id is removed, the id stays               -> healed to the id, one tier down, flagged
    drift-gone    a button is removed outright                     -> needs_human, never a nearby element

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

SAVE = 'page.get_by_role("button", name="Save changes")'
DELETE = 'page.get_by_role("button", name="Delete account")'
CHECK = 'page.get_by_role("checkbox", name="Email me the newsletter")'
BY_ID = 'page.locator("#display-name")'
BY_TESTID = 'page.get_by_test_id("save-settings")'

FRENCH = [
    ('<html lang="en">', '<html lang="fr">'),
    ("<title>Account settings</title>", "<title>Paramètres du compte</title>"),
    ("<h1>Account settings</h1>", "<h1>Paramètres du compte</h1>"),
    ("<h2>Profile</h2>", "<h2>Profil</h2>"),
    ('<label for="display-name">Display name</label>', '<label for="display-name">Nom d\'affichage</label>'),
    ("> Email me the newsletter</label>", "> Recevoir la lettre d'information</label>"),
    ("Save changes</button>", "Enregistrer</button>"),
    ("<h2>Danger zone</h2>", "<h2>Zone dangereuse</h2>"),
    ("Delete account</button>", "Supprimer le compte</button>"),
]

SECOND_SAVE = '''  <section id="notifications" class="panel">
    <h2>Notifications</h2>
    <div class="actions"><button type="button" id="save-notifications" data-testid="save-notifications">Save changes</button></div>
  </section>
'''

VARIANTS = {
    "drift-renid": {
        "title": "an id is renamed",
        "text": "The display-name input's id changes from `display-name` to `profile-name` (the label's `for` follows). Its "
                "test id `display-name-input` and name are unchanged, so the same element still has a better locator than the "
                "new id.",
        "edits": [("index.html", [('id="display-name" name="display_name"', 'id="profile-name" name="display_name"'),
                                  ('for="display-name"', 'for="profile-name"')])],
        "steps": [{"test": "test_profile_settings", "old": BY_ID, "new": 'page.get_by_test_id("display-name-input")',
                   "target": 'page.get_by_test_id("display-name-input")',
                   "expect": {"outcome": "healed", "settled_at": 2, "tier_movement": "up", "branch": False}}],
    },
    "drift-fr": {
        "title": "French",
        "text": "The page is translated to French and nothing else changes: `<html lang=\"fr\">`, every label, heading and button "
                "text, the same markup, ids, test ids and machine values. Copy locators break; the checkbox has no id or test "
                "id, but its plain data-pref attribute is stable in every language, so it heals onto that (a higher tier than "
                "copy) and needs no branch; the buttons have ids and test ids and need no branch either. The language branch "
                "itself is covered by the french_page_language_branch contract fixture and the signup v6 variant.",
        "edits": [("index.html", FRENCH)],
        "steps": [
            {"test": "test_profile_settings", "old": CHECK, "new": 'page.locator("[data-pref=newsletter]")',
             "target": 'page.locator("[data-pref=newsletter]")',
             "expect": {"outcome": "healed", "tier_movement": "up", "branch": False}},
            {"test": "test_profile_settings", "old": SAVE, "new": BY_TESTID, "target": 'page.locator("#save")',
             "expect": {"outcome": "healed", "tier_movement": "up", "branch": False}},
            {"test": "test_delete_account", "old": DELETE, "new": 'page.get_by_test_id("delete-account")',
             "target": 'page.locator("#delete")',
             "expect": {"outcome": "healed", "tier_movement": "up", "branch": False}}],
    },
    "drift-dup": {
        "title": "a duplicate button",
        "text": "A Notifications section is added with a second button whose text is also \"Save changes\" (its own id and test "
                "id). `get_by_role(\"button\", name=\"Save changes\")` now matches two elements. The right heal is the profile "
                "button, the one the test's assertion (`saved: profile ...`) is about.",
        "edits": [("index.html", [('  <p class="status"', SECOND_SAVE + '  <p class="status"')])],
        "steps": [{"test": "test_profile_settings", "old": SAVE, "new": BY_TESTID, "target": 'page.locator("#save")',
                   "expect": {"outcome": "healed", "settled_at": 2, "tier_movement": "up", "branch": False}}],
    },
    "drift-noid": {
        "title": "a test id is removed",
        "text": "The `data-testid=\"save-settings\"` attribute is removed from the Save button and nothing else changes: the id "
                "`save` and the text stay. No test id is left to use, so the heal moves down one tier to the id, and the "
                "decision says so.",
        "edits": [("index.html", [('<button type="button" id="save" data-testid="save-settings">',
                                   '<button type="button" id="save">')])],
        "steps": [{"test": "test_save_by_test_id", "old": BY_TESTID, "new": 'page.locator("#save")',
                   "target": 'page.locator("#save")',
                   "expect": {"outcome": "healed", "tier_movement": "down", "below_floor": True, "branch": False}}],
    },
    "drift-gone": {
        "title": "the target is removed",
        "text": "The Delete account button (and its section) is removed. Nothing on the page is the element the test meant; the "
                "service must end `needs_human` and never offer the nearest button.",
        "edits": [("index.html", [('''  <section id="danger" class="panel">
    <h2>Danger zone</h2>
    <div class="actions"><button type="button" id="delete" data-testid="delete-account" class="danger">Delete account</button></div>
  </section>
''', "")])],
        "steps": [{"test": "test_delete_account", "old": DELETE, "new": None, "target": None,
                   "expect": {"outcome": "needs_human"}}],
    },
}


def build():
    golden = {"tests": {"test_profile_settings": "profile settings", "test_save_by_test_id": "save by test id",
                        "test_delete_account": "delete account"}, "variants": {}}
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
                 "Test lines are looked up by locator text in `tests/python/test_drift.py`. A line that is not listed still passes.", ""]
        for s in spec["steps"]:
            goal = "needs_human (the element is gone)" if s["target"] is None else f"heal onto `{s['target']}`"
            lines.append(f"- `{s['old']}` in `{s['test']}`: {goal}; expected {json.dumps(s['expect'], sort_keys=True)}")
        open(os.path.join(out, "CHANGES.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
        golden["variants"][name] = spec["steps"]
    with open(os.path.join(HERE, "golden-drift.json"), "w", encoding="utf-8") as f:
        json.dump(golden, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("built", ", ".join(VARIANTS), "and golden-drift.json")


if __name__ == "__main__":
    build()
