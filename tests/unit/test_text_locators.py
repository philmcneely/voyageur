"""Text elements (headings, labels, paragraphs, list items, named landmarks) get a proper locator: a stable attribute when the element
has one, else role + accessible name (a heading with its level), else exact text, each written only when the request proves it unique,
and a reason when it cannot. The page is the testbed's drift-fr (French), as the probe lists it."""
import base64
import io

import pytest
from PIL import Image

from voyageur.branches import plan
from voyageur.inventory import Inventory, _role_of, heading_level
from voyageur.locator_policy import write
from voyageur.locator_steps import try_parse
from voyageur.marks import run_overlay
from voyageur.schemas import OverlayResponse, parse_overlay_request
from voyageur.uniqueness import aria_count
from tests.unit.scenarios import FakeModel, request, session

pytestmark = pytest.mark.unit

BOX = {"x": 10, "y": 10, "width": 200, "height": 20}


def el(tag, **kw):
    """One probe item as the probe lists it: the keys a locator is written from, a box, and nothing the probe leaves out."""
    return {"tag": tag, "box": dict(BOX), **kw}


# the testbed's drift-fr page, in the probe's order: 4 headings / labels that have no hook of their own and 8 that do
H1 = el("h1", name="Paramètres du compte", text="Paramètres du compte", copy_only=True, pw_text="Paramètres du compte")
PROFILE = el("section", id="profile", testid="profile-panel", **{"class": "panel"},
             name="Profil Nom d'affichage Recevoir la lettre d'information Enregistrer",
             text="Profil Nom d'affichage Recevoir la lettre d'information Enregistrer")
H2_PROFILE = el("h2", name="Profil", text="Profil", copy_only=True, pw_text="Profil")
LABEL_FOR = el("label", for_attr="display-name", name="Nom d'affichage", text="Nom d'affichage", pw_text="Nom d'affichage")
NAME_INPUT = el("input", type="text", id="display-name", testid="display-name-input", name_attr="display_name",
                label="Nom d'affichage")
LABEL_CHECK = el("label", **{"class": "check"}, name="Recevoir la lettre d'information", text="Recevoir la lettre d'information",
                 copy_only=True, pw_text="Recevoir la lettre d'information")
CHECKBOX = el("input", type="checkbox", data_pref="newsletter", label="Recevoir la lettre d'information")
SAVE = el("button", type="button", id="save", testid="save-settings", name="Enregistrer", text="Enregistrer")
DANGER = el("section", id="danger", **{"class": "panel"}, name="Zone dangereuse Supprimer le compte",
            text="Zone dangereuse Supprimer le compte")
H2_DANGER = el("h2", name="Zone dangereuse", text="Zone dangereuse", copy_only=True, pw_text="Zone dangereuse")
DELETE = el("button", type="button", id="delete", testid="delete-account", **{"class": "danger"}, name="Supprimer le compte",
            text="Supprimer le compte")
SUMMARY = el("p", id="summary", testid="summary", **{"class": "status"}, name="idle", text="idle")
DRIFT_FR = [H1, PROFILE, H2_PROFILE, LABEL_FOR, NAME_INPUT, LABEL_CHECK, CHECKBOX, SAVE, DANGER, H2_DANGER, DELETE, SUMMARY]
DRIFT_FR_ARIA = ('- main:\n  - heading "Paramètres du compte" [level=1]\n  - heading "Profil" [level=2]\n  - text: Nom d\'affichage\n'
                 '  - textbox "Nom d\'affichage"\n  - checkbox "Recevoir la lettre d\'information"\n  - text: Recevoir la lettre d\'information\n'
                 '  - button "Enregistrer"\n  - heading "Zone dangereuse" [level=2]\n  - button "Supprimer le compte"\n  - paragraph: idle\n')


def png(w=300, h=300):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def overlay(items, aria=None, counts=True, language="python", **page):
    """What POST /v1/overlay answers for `items` laid out one under another, with or without the aria snapshot and the probe's copy
    counts (a client that sends neither is the one the probe's counts were added for)."""
    inventory = []
    for i, item in enumerate(items):
        inventory.append({**item, "box": {"x": 10, "y": 8 + 22 * i, "width": 200, "height": 18}})
    body = {"client": {"framework": "x", "language": language},
            "page": {"url": "http://app.example/", "inventory": inventory, "inventory_more": 0,
                     "viewport": {"w": 300, "h": 300, "dpr": 1},
                     "screenshot": {"media_type": "image/png", "data": png(), "width": 300, "height": 300}, **page}}
    if counts:
        body["page"].update(inventory_copy_more=0, inventory_copy_withheld=0, inventory_copy_withheld_data=0)
    if aria is not None:
        body["page"]["aria_snapshot"] = aria
    out = run_overlay(parse_overlay_request(body))
    OverlayResponse.model_validate(out)
    return out


def locators(out):
    return [e["locator"] for e in out["legend"]]


STABLE = {1: 'page.get_by_test_id("profile-panel")', 3: "page.locator(\"label[for='display-name']\")",
          4: 'page.get_by_test_id("display-name-input")', 6: "page.locator(\"input[data-pref='newsletter']\")",
          7: 'page.get_by_test_id("save-settings")', 8: 'page.locator("#danger")', 10: 'page.get_by_test_id("delete-account")',
          11: 'page.get_by_test_id("summary")'}


# ---- the matrix of what a request carries, on the real page --------------------------------------------------------------
def test_with_the_aria_snapshot_and_the_copy_counts_every_text_element_of_drift_fr_has_a_locator():
    out = overlay(DRIFT_FR, DRIFT_FR_ARIA)
    got = locators(out)
    assert all(e["status"] == "matched" for e in out["legend"]), [(e["status"], e["locator"]) for e in out["legend"]]
    assert got[0] == 'page.get_by_role("heading", name="Paramètres du compte", level=1, exact=True)'
    assert got[2] == 'page.get_by_role("heading", name="Profil", level=2, exact=True)'
    assert got[9] == 'page.get_by_role("heading", name="Zone dangereuse", level=2, exact=True)'
    assert got[5] == 'page.get_by_text("Recevoir la lettre d\'information", exact=True)'     # a label no role names
    for i, code in STABLE.items():                       # a stable attribute still wins over any copy
        assert got[i] == code
    kinds = {e["number"]: (e["kind"], e["tier"], e["copy_based"]) for e in out["legend"]}
    assert kinds[1] == ("role", 4, True) and kinds[3] == ("role", 4, True) and kinds[6] == ("text", 7, True)
    assert kinds[2] == ("test_id", 0, False) and kinds[4] == ("css", 3, False) and kinds[10] == ("role", 4, True)


def test_without_the_copy_counts_the_headings_are_proved_by_the_aria_snapshot_and_the_rest_says_why_not():
    out = overlay(DRIFT_FR, DRIFT_FR_ARIA, counts=False)
    got = locators(out)
    assert got[0].startswith('page.get_by_role("heading", name="Paramètres du compte"') and got[2].startswith("page.get_by_role(")
    assert got[9] == 'page.get_by_role("heading", name="Zone dangereuse", level=2, exact=True)'
    assert got[5] is None                                # the wrapping label: only its text names it, and nothing proves that text unique
    e = out["legend"][5]
    assert e["status"] == "no_stable_locator" and e["reason"] == "copy_counts_missing" and "copy counts" in e["why"]
    assert e["kind"] is None and e["tier"] is None and not e["copy_based"]
    assert sum(1 for x in out["legend"] if x["status"] == "no_stable_locator") == 1
    for i, code in STABLE.items():
        assert got[i] == code
    assert all(x["reason"] is None and x["why"] is None for x in out["legend"] if x["locator"])


def test_with_the_copy_counts_and_no_snapshot_text_is_written_as_before_and_stays_copy_based():
    out = overlay(DRIFT_FR, None)
    got = locators(out)
    assert got[0] == 'page.get_by_text("Paramètres du compte", exact=True)' and got[2] == 'page.get_by_text("Profil", exact=True)'
    assert got[5] == 'page.get_by_text("Recevoir la lettre d\'information", exact=True)'
    assert [out["legend"][i]["copy_based"] for i in (0, 2, 5, 9)] == [True] * 4
    assert all(e["status"] == "matched" for e in out["legend"])


def test_with_neither_the_four_say_what_would_prove_them():
    out = overlay(DRIFT_FR, None, counts=False)
    for i in (0, 2, 5, 9):
        e = out["legend"][i]
        assert e["status"] == "no_stable_locator" and e["locator"] is None
        assert e["reason"] == ("copy_counts_missing" if i == 5 else "aria_snapshot_missing")
        assert "copy counts" in e["why"] and ("page.aria_snapshot" in e["why"]) == (i != 5)     # a label has no role to prove
    assert out["summary"]["by_status"] == {"matched": 8, "no_stable_locator": 4}
    for i, code in STABLE.items():
        assert locators(out)[i] == code


def test_typescript_clients_get_the_heading_with_its_level_in_their_own_spelling():
    out = overlay(DRIFT_FR, DRIFT_FR_ARIA, language="typescript")
    assert locators(out)[2] == "page.getByRole('heading', { name: 'Profil', level: 2, exact: true })"
    assert locators(out)[5] == "page.getByText('Recevoir la lettre d\\'information', { exact: true })"


# ---- the writer: one element ------------------------------------------------------------------------------------------
def inv(*items, aria="", complete=True, withheld=0, withheld_data=0):
    return Inventory([dict(i) for i in items], complete, aria, 0, withheld, withheld_data)


def test_a_heading_is_role_plus_name_plus_level_in_both_languages_and_round_trips():
    i = inv(H1, H2_PROFILE, aria=DRIFT_FR_ARIA, withheld=None, withheld_data=None)
    out = write(i.elements[1], i, "python", "page", "text")
    c = out.candidates[0]
    assert c.kind == "role" and c.copy_based and c.tier == 4
    assert c.code["python"] == 'page.get_by_role("heading", name="Profil", level=2, exact=True)'
    assert c.code["typescript"] == "page.getByRole('heading', { name: 'Profil', level: 2, exact: true })"
    assert try_parse(c.code["python"], "python") and try_parse(c.code["typescript"], "typescript")
    assert c.neutral[0]["m"] == "get_by_role" and c.neutral[0]["o"]["level"] == 2


def test_a_stable_attribute_beats_role_and_name_for_a_heading():
    for attrs, kind in (({"testid": "page-title"}, "testid"), ({"id": "title"}, "id")):
        h = el("h2", name="Profil", text="Profil", copy_only=True, pw_text="Profil", **attrs)
        i = inv(h, aria='- heading "Profil" [level=2]')
        assert write(i.elements[0], i, "python", "page", "text").candidates[0].kind == kind


def test_a_heading_is_not_written_when_the_snapshot_cannot_prove_it_and_the_reason_is_the_snapshot():
    i = inv(H1, H2_PROFILE, aria="", withheld=None, withheld_data=None)
    out = write(i.elements[1], i, "python", "page", "text")
    assert out.candidates == [] and out.reason == "no_stable_locator" and out.gap == "aria_snapshot_missing"
    assert "page.aria_snapshot" in out.why and "copy counts" in out.why


def test_two_headings_with_one_name_and_level_are_not_written_and_the_reason_is_not_unique():
    twin = dict(H2_PROFILE, box=dict(BOX, y=40))
    i = inv(H2_PROFILE, twin, aria='- heading "Profil" [level=2]\n- heading "Profil" [level=2]')
    out = write(i.elements[0], i, "python", "page", "text")
    assert out.candidates == [] and out.gap == "copy_not_unique" and "2 elements" in out.why
    assert "Profil" not in out.why                       # a reason names no page copy


def test_the_level_tells_two_headings_of_one_name_apart():
    low = dict(H2_PROFILE, tag="h3", box=dict(BOX, y=40))
    i = inv(H2_PROFILE, low, aria='- heading "Profil" [level=2]\n- heading "Profil" [level=3]')
    assert write(i.elements[0], i, "python", "page", "text").candidates[0].code["python"] == \
        'page.get_by_role("heading", name="Profil", level=2, exact=True)'
    assert write(i.elements[1], i, "python", "page", "text").candidates[0].code["python"] == \
        'page.get_by_role("heading", name="Profil", level=3, exact=True)'


def test_a_heading_whose_level_is_not_known_is_written_without_one():
    div = el("div", role="heading", name="Profil", text="Profil", copy_only=True, pw_text="Profil")
    i = inv(div, aria='- heading "Profil" [level=2]')
    assert write(i.elements[0], i, "python", "page", "text").candidates[0].code["python"] == \
        'page.get_by_role("heading", name="Profil", exact=True)'
    assert heading_level(div) is None and heading_level(H2_PROFILE) == 2 and heading_level(dict(H2_PROFILE, role="tab")) is None


def test_the_role_a_tag_implies_for_text_elements():
    assert _role_of(H1) == "heading" and _role_of(el("h6")) == "heading"
    assert _role_of(el("nav", aria_label="Main", name="Main")) == "navigation"
    assert _role_of(el("section", aria_label="Account", name="Account")) == "region"
    assert _role_of(el("section", name="Profil Nom")) == ""          # a section no author named is no region
    assert _role_of(el("p", name="idle")) == "" and _role_of(el("li", name="Item")) == ""     # no name from their content


def test_a_named_landmark_is_role_and_the_author_given_name_never_its_copy():
    nav = el("nav", aria_label="Main", name="Main", text="Home Pricing", copy_only=True, pw_text="HomePricing")
    i = inv(nav, aria='- navigation "Main":\n  - link "Home"')
    assert write(i.elements[0], i, "python", "page", "text").candidates[0].code["python"] == \
        'page.get_by_role("navigation", name="Main", exact=True)'
    unnamed = el("nav", name="Home Pricing", text="Home Pricing", copy_only=True, pw_text="HomePricing")
    j = inv(unnamed, aria='- navigation:\n  - link "Home"')
    assert not any(c.kind == "role" for c in write(j.elements[0], j, "python", "page", "text").candidates)


def test_a_paragraph_and_a_list_item_are_text_never_role_because_their_content_is_no_name():
    p = el("p", name="Your session has expired", text="Your session has expired", copy_only=True, pw_text="Your session has expired")
    li = el("li", name="Milk", text="Milk", copy_only=True, pw_text="Milk")
    i = inv(p, li, aria="- paragraph: Your session has expired\n- list:\n  - listitem: Milk")
    assert write(i.elements[0], i, "python", "page", "text").candidates[0].code["python"] == \
        'page.get_by_text("Your session has expired", exact=True)'
    assert write(i.elements[1], i, "python", "page", "text").candidates[0].code["python"] == 'page.get_by_text("Milk", exact=True)'


def test_text_is_never_written_on_a_listing_that_cannot_prove_it_and_the_reason_says_so():
    p = el("p", name="Hello", text="Hello", copy_only=True, pw_text="Hello")
    for kw, gap in (({"withheld": None, "withheld_data": None}, "copy_counts_missing"),
                    ({"withheld_data": 3}, "listing_incomplete"), ({"complete": False}, "listing_incomplete")):
        i = inv(p, aria="", **kw)
        out = write(i.elements[0], i, "python", "page", "text")
        assert out.candidates == [] and out.gap == gap, (kw, out.gap)
    twin = dict(p, box=dict(BOX, y=40))
    i = inv(p, twin, aria="")
    out = write(i.elements[0], i, "python", "page", "text")
    assert out.candidates == [] and out.gap == "copy_not_unique"


def test_an_element_with_no_copy_and_no_stable_attribute_has_nothing_to_prove():
    i = inv(el("div", **{"class": "a b"}), el("div", **{"class": "a b"}), aria="")
    out = write(i.elements[0], i, "python", "page", "chain")
    assert out.candidates == [] and out.gap == "nothing_stable" and out.why


def test_a_role_that_has_no_place_in_the_tree_is_never_written():
    h = el("h2", role="presentation", name="Profil", text="Profil", copy_only=True, pw_text="Profil")
    i = inv(h, aria='- text: Profil')
    assert not any(c.kind == "role" for c in write(i.elements[0], i, "python", "page", "text").candidates)


# ---- the oracle: level, and what getByText can match ---------------------------------------------------------------------
def test_the_snapshot_count_reads_the_level_and_ignores_a_name_that_looks_like_an_option():
    aria = '- heading "Profil" [level=2]\n- heading "Profil" [level=3]\n- heading "level=3 exact=True" [level=2]'
    assert aria_count('page.get_by_role("heading", name="Profil", exact=True)', aria) == 2
    assert aria_count('page.get_by_role("heading", name="Profil", level=2, exact=True)', aria) == 1
    assert aria_count("page.getByRole('heading', { name: 'Profil', level: 3, exact: true })", aria) == 1
    assert aria_count('page.get_by_role("heading", name="Profil", level=4, exact=True)', aria) == 0
    # a name that holds the words "level=3" or "exact=True" is a name, not an option
    assert aria_count('page.get_by_role("heading", name="level=3 exact=True", exact=True)', aria) == 1
    assert aria_count('page.get_by_role("heading", name="level", level=2)', aria) == 1


def test_the_listing_answers_a_heading_role_with_its_level_too():
    i = inv(H2_PROFILE, dict(H2_PROFILE, tag="h3", box=dict(BOX, y=40)), aria="")
    both = i.matched('page.get_by_role("heading", name="Profil", exact=True)')
    assert [e["tag"] for e in both] == ["h2", "h3"]
    one = i.matched('page.get_by_role("heading", name="Profil", level=3, exact=True)')
    assert [e["tag"] for e in one] == ["h3"]


def test_a_labelled_checkbox_is_not_a_match_of_get_by_text_for_its_label():
    i = inv(LABEL_CHECK, CHECKBOX, aria=DRIFT_FR_ARIA)
    assert i.count('page.get_by_text("Recevoir la lettre d\'information", exact=True)') == 1
    out = write(i.elements[0], i, "python", "page", "text")
    assert out.candidates[0].code["python"] == 'page.get_by_text("Recevoir la lettre d\'information", exact=True)'


def test_a_control_named_by_its_content_is_still_a_match_of_get_by_text():
    div = el("div", role="checkbox", name="Recevoir", text="Recevoir", pw_text="Recevoir")
    label = el("label", name="Recevoir", text="Recevoir", copy_only=True, pw_text="Recevoir")
    i = inv(label, div, aria='- checkbox "Recevoir"\n- text: Recevoir')
    assert i.count('page.get_by_text("Recevoir", exact=True)') == 2
    assert write(i.elements[0], i, "python", "page", "text").candidates == []


# ---- the language rule --------------------------------------------------------------------------------------------------
def test_a_french_heading_is_a_branch_beside_the_english_one_never_a_translation():
    i = inv(H2_PROFILE, aria='- heading "Profil" [level=2]')
    cand = write(i.elements[0], i, "python", "self.page", "role").candidates[0]
    broken = 'self.page.get_by_role("heading", name="Profile", level=2, exact=True)'
    p = plan(cand, "python", "self.page", broken, "en", "fr", None)
    assert p.needed and p.branches == {"en": broken, "fr": 'self.page.get_by_role("heading", name="Profil", level=2, exact=True)'}
    assert p.code == f'by_language(self.page, {{"en": {broken}, "fr": {p.branches["fr"]}}})'


def test_the_english_heading_comes_from_the_passing_run_when_the_broken_locator_names_no_copy():
    i = inv(H2_PROFILE, aria='- heading "Profil" [level=2]')
    cand = write(i.elements[0], i, "python", "self.page", "id").candidates[0]
    p = plan(cand, "python", "self.page", 'self.page.locator("#profile-title")', "en", "fr", {"tag": "h2", "name": "Profile"})
    assert p.needed and p.branches["en"] == 'self.page.get_by_role("heading", name="Profile", level=2, exact=True)'
    assert p.branches["fr"].startswith('self.page.get_by_role("heading", name="Profil"')
    none = plan(cand, "python", "self.page", 'self.page.locator("#profile-title")', "en", "fr", None)
    assert none.needed and none.code is None and none.reason == "language_branch_unavailable"      # old copy unknown: not guessed


def test_a_heal_of_a_translated_heading_offers_role_and_name_in_both_languages():
    page = "class P:\n    def go(self):\n        self.page.get_by_role('heading', name='Profile', level=2).click()\n"
    inventory = [dict(H2_PROFILE, ref="e0", top_at_center=True, box={"x": 10, "y": 10, "width": 200, "height": 20})]
    body = request(inventory=inventory, last_pass={"fingerprint": {"tag": "h2", "name": "Profile", "text": "Profile", "box": {"x": 10, "y": 10, "width": 200, "height": 20}}, "language": "en"},
                   failure={"error": 'waiting for get_by_role("heading", name="Profile", level=2)', "stack": []},
                   source={"roots": ["pages"], "files": [{"path": "pages/p.py", "content": page}]})
    body["page"] = {"url": "http://app.example/", "language": {"html_lang": "fr"}, "inventory": inventory,
                    "aria_snapshot": '- heading "Profil" [level=2]', "inventory_copy_withheld": 0, "inventory_copy_withheld_data": 0}
    from voyageur.schemas import HealRequest
    r = session(FakeModel('{"ref": "e0"}')).start(HealRequest.model_validate(body))
    assert r.status == "try" and r.candidate.locator["kind"] == "by_language" and set(r.candidate.branches) == {"en", "fr"}
    assert r.candidate.branches["en"] == "self.page.get_by_role('heading', name='Profile', level=2)"
    assert r.candidate.branches["fr"] == 'self.page.get_by_role("heading", name="Profil", level=2, exact=True)'


def test_a_heal_that_cannot_prove_a_copy_locator_says_what_is_missing_in_why():
    page = "class P:\n    def go(self):\n        self.page.get_by_role('heading', name='Profile', level=2).click()\n"
    inventory = [dict(H2_PROFILE, ref="e0", top_at_center=True, box={"x": 10, "y": 10, "width": 200, "height": 20})]
    body = request(inventory=inventory, failure={"error": 'waiting for get_by_role("heading", name="Profile", level=2)', "stack": []},
                   source={"roots": ["pages"], "files": [{"path": "pages/p.py", "content": page}]})
    body["page"] = {"url": "http://app.example/", "language": {"html_lang": "en"}, "inventory": inventory}
    from voyageur.schemas import HealRequest
    r = session(FakeModel('{"ref": "e0"}')).start(HealRequest.model_validate(body))
    assert r.status == "needs_human" and r.reason == "no_stable_locator"
    assert "page.aria_snapshot" in r.why and "Profil" not in r.why
