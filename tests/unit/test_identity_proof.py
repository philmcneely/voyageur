"""
===============================================================================
Identity proof tests (a pick is a heal only with positive proof; frames; the model's "none")
===============================================================================
The wrong-element heals the live acceptance run kept, rebuilt with inventories modelled on the orders testbed pages:
    v7  button.sc-fzXfKy.jQdBvX            -> the "Actions for order 1040" button
    v8  #tab-history                       -> the "Open menu for order 1040" button
    v8  frame_locator("#payment-frame") .. -> the page-level input[name=zip]
    v9  [data-cy=export-csv] (removed)     -> [data-cy=export-pdf]
plus one test per proof path and per contradiction, the frame-scoped heal and the `ref: null` reply.

Author: PMAC
===============================================================================
"""
import pytest

from voyageur.frames import frame_hop, resolve_frame, scope_of
from voyageur.gates import verify_pick
from voyageur.pick import build_prompt, parse_pick
from voyageur.proof import broken_facts, prove, words
from voyageur.schemas import HealRequest, ResultRequest
from tests.unit.scenarios import FakeModel, session
from tests.unit.scenarios import request as base_request

pytestmark = pytest.mark.unit


def box(x, y, w=80, h=30):
    return {"x": x, "y": y, "width": w, "height": h}


def e(ref, tag="button", **kw):
    return {"ref": ref, "tag": tag, **kw}


def check(el, items, call, fp=None, action="click", neighbours=(), complete=False):
    """verify_pick for `el` as the element of `call` among `items` (everything listed was shown). `complete`: the listing
    holds every element of the page (the rules that read "no other element" need it)."""
    return verify_pick(el, {i["ref"] for i in items}, items, fp, action, neighbours, (), call, complete=complete)


# ---- the orders pages ------------------------------------------------------------------------------------------------
def row_menus(prefix="Actions for order"):
    return [e(f"m{i}", name=f"{prefix} {1040 + i}", aria_label=f"{prefix} {1040 + i}", data_cy="row-menu",
              row=f"order-row-{1040 + i}", box=box(900, 100 + 40 * i, 30, 30), text="\u22ef") for i in range(6)]


CANCEL = e("c1", name="Cancel order", text="Cancel order", box=box(20, 600, 100, 30), **{"class": "sc-dkzDqf kLmNoP"})
TABS = [e("t1", role="tab", name="History", text="History", testid="tab-history-2", id="tab-hist")]
EXPORT_PDF = e("x2", name="Export PDF", text="Export PDF", data_cy="export-pdf", data_format="pdf", box=box(700, 20, 90, 30))
EXPORT_CSV_THEN = {"tag": "button", "name": "Export CSV", "text": "Export CSV", "data_cy": "export-csv",
                   "box": box(700, 20, 90, 30)}               # where the pdf button sits once csv is gone


# ---- v9: a removed target is not a neighbour -----------------------------------------------------------------------
def test_v9_export_csv_removed_pick_of_export_pdf_is_another_element_without_a_fingerprint():
    page = [EXPORT_PDF, *row_menus()]
    why = check(EXPORT_PDF, page, 'page.locator("[data-cy=export-csv]")')
    assert why == "different_element" and "data-cy" in why.why and "export-pdf" in why.why


def test_v9_export_csv_removed_pick_of_export_pdf_is_another_element_with_a_fingerprint_even_in_the_freed_place():
    page = [EXPORT_PDF, *row_menus()]
    why = check(EXPORT_PDF, page, 'page.locator("[data-cy=export-csv]")', fp=EXPORT_CSV_THEN)
    assert why == "different_element" and "export-csv" in why.why     # the same place is proof, the hook contradicts it


def test_a_hook_that_changed_with_the_same_copy_is_not_a_contradiction():
    renamed = e("x1", name="Export CSV", text="Export CSV", data_cy="export-as-csv", box=box(700, 20, 90, 30))
    assert check(renamed, [renamed, EXPORT_PDF], 'page.locator("[data-cy=export-csv]")', fp=EXPORT_CSV_THEN) is None


# ---- v7 / v8: a look-alike with nothing in common --------------------------------------------------------------------
def test_v7_hashed_class_button_is_not_the_first_row_menu_button():
    page = [CANCEL, *row_menus()]
    why = check(page[1], page, 'page.locator("button.sc-fzXfKy.jQdBvX")')
    assert why == "no_identity_proof" and "Actions for order 1040" in why.why


def test_v7_a_renamed_css_only_locator_is_proven_when_the_pick_is_the_only_visible_element_of_its_tag_and_says_the_same():
    cancel = e("c1", name="Cancel", text="Cancel", box=box(20, 600, 100, 30), **{"class": "sc-dkzDqf kLmNoP"})
    only = [cancel, e("i1", "input", type="text", name="Search"), e("l1", "a", role="link", name="Cancel")]
    call = 'page.locator("button[aria-label=\'Cancel\'].sc-fzXfKy.jQdBvX")'
    assert check(cancel, only, call) is None
    assert prove(cancel, None, call, only)[0][0] == "renamed"


def test_renamed_a_lone_element_of_the_tag_alone_is_no_proof():
    only = [CANCEL, e("i1", "input", type="text", name="Search")]
    assert check(CANCEL, only, 'page.locator("button.sc-fzXfKy.jQdBvX")') == "no_identity_proof"
    lone = e("c9", name="Cancel", text="Cancel", box=box(20, 600))
    assert check(lone, [lone], 'page.locator("button.btn-primary")') == "no_identity_proof"


def test_the_renamed_rule_needs_the_old_values_to_be_gone():
    still = e("c2", name="Cancel order", box=box(20, 600), **{"class": "sc-fzXfKy jQdBvX"})
    assert check(still, [still], 'page.locator("button.sc-fzXfKy.jQdBvX")') == "no_identity_proof"


def test_v8_tab_history_is_not_the_open_menu_button():
    page = [*row_menus("Open menu for order"), *TABS]
    why = check(page[0], page, 'page.locator("#tab-history")')
    assert why == "no_identity_proof"


def test_v8_the_tab_whose_test_id_extends_the_old_id_is_proven():
    page = [*row_menus("Open menu for order"), *TABS]
    assert check(TABS[0], page, 'page.locator("#tab-history")') is None


# ---- no fingerprint: each proof path ---------------------------------------------------------------------------------
def test_hook_stem_the_old_id_is_inside_the_new_test_id():
    name = e("n1", "input", type="text", testid="display-name-input")
    assert prove(name, None, 'page.locator("#display-name")', [name])[0][0] == "hook_stem"


def test_hook_stem_a_stem_counts_as_the_word_username_user():
    user = e("u1", "input", type="text", id="user")
    assert prove(user, None, 'page.locator("#username")', [user])[0][0] == "hook_stem"


def test_hook_stem_the_old_hook_value_spelled_out_in_the_copy():
    csv = e("x1", name="Export CSV", id="dl-btn")
    assert prove(csv, None, 'page.locator("[data-cy=export-csv]")', [csv])[0][0] == "hook_stem"


def test_hook_stem_words_that_only_overlap_prove_nothing():
    pdf = e("x2", name="Export PDF", id="pdf")
    assert prove(pdf, None, 'page.locator("#export-csv")', [pdf])[0] is None


def test_copy_the_same_role_and_name_proves_with_a_compatible_kind():
    save = e("s1", name="Save changes", id="save-btn")
    assert prove(save, None, 'page.get_by_role("button", name="Save changes")', [save])[0][0] == "copy"
    link = e("s2", "a", role="link", name="Save changes")
    assert prove(link, None, 'page.get_by_role("button", name="Save changes")', [link])[0] is None


def test_copy_a_single_shared_word_proves_nothing():
    send = e("s1", name="Send changes")
    assert prove(send, None, 'page.get_by_role("button", name="Save changes")', [send])[0] is None


def test_copy_a_whole_name_inside_the_only_element_that_says_it_proves():
    date = e("x1", "input", type="text", label="Expiry date")
    assert prove(date, None, 'page.get_by_label("Expiry")', [date])[0][0] == "copy"
    twin = e("x2", "input", type="text", label="Expiry month")
    assert prove(date, None, 'page.get_by_label("Expiry")', [date, twin])[0] is None


def test_row_the_row_text_of_a_filter_proves_the_checkbox_in_it():
    box_ = e("k1", "input", type="checkbox", name="#1044", row="Echo GmbH paid")
    other = e("k2", "input", type="checkbox", name="#1042", row="Acme paid")
    call = 'page.get_by_role("row").filter(has_text="Echo GmbH").get_by_role("checkbox")'
    assert prove(box_, None, call, [box_, other])[0][0] == "row"
    assert prove(other, None, call, [box_, other])[0] is None


def test_a_call_that_does_not_parse_has_no_facts_and_proves_nothing():
    el = e("a1", id="x")
    assert not broken_facts("not a locator").known()
    assert check(el, [el], "not a locator") == "no_identity_proof"


# ---- with a fingerprint: each proof path -----------------------------------------------------------------------------
FP = {"tag": "button", "id": "save", "testid": "save-settings", "name": "Save changes", "text": "Save changes",
      "box": box(100, 200)}


def test_fp_identity_a_test_id_unchanged_while_the_id_and_copy_changed():
    now = e("b1", testid="save-settings", id="save-2", name="Enregistrer", box=box(400, 400))
    assert prove(now, FP, "", [now])[0][0] == "identity"


def test_fp_copy_the_same_copy_somewhere_else_with_a_compatible_kind():
    now = e("b1", name="Save changes", box=box(400, 400))
    assert prove(now, FP, "", [now])[0][0] == "copy"
    link = e("b2", "a", role="link", name="Save changes", box=box(400, 400))
    assert prove(link, FP, "", [link])[0] is None


def test_fp_place_overlapping_box_with_changed_copy():
    now = e("b1", name="Store", box=box(102, 201), hit=True)
    assert prove(now, FP, "", [now])[0][0] == "place"
    elsewhere = e("b2", name="Store", box=box(600, 600))
    assert prove(elsewhere, FP, "", [elsewhere])[0] is None


def test_fp_place_incompatible_kind_in_the_same_place_is_no_proof():
    now = e("b1", "input", type="text", name="Store", box=box(100, 200))
    assert prove(now, FP, "", [now])[0] is None


def test_fp_place_neighbour_consistent_position_after_a_layout_shift():
    nb = {"tag": "button", "testid": "cancel", "name": "Cancel", "box": box(200, 200)}
    cancel = e("b9", testid="cancel", name="Cancel", box=box(260, 260))         # everything moved 60px right and down
    now = e("b1", name="Store", box=box(160, 260), top_at_center=True)
    assert prove(now, FP, "", [now, cancel], [nb])[0][0] == "place"
    off = e("b3", name="Store", box=box(160, 330))
    assert prove(off, FP, "", [off, cancel], [nb])[0] is None


def test_fp_contradiction_beats_every_proof():
    fp = {**FP, "data_cy": "save-btn"}
    now = e("b1", name="Store", data_cy="save-all", box=box(100, 200))
    assert prove(now, fp, "", [now]) == (None, "its data-cy is 'save-all', the step's was 'save-btn'")


# ---- the core: refused picks, the next answer, none ---------------------------------------------------------------------
PAGE = "class P:\n    def go(self):\n        self.page.locator('#tab-history').click()\n"
ERR = "waiting for locator('#tab-history')"


def request(inventory, line="self.page.locator('#tab-history').click()", error=ERR, **over):
    page = f"class P:\n    def go(self):\n        {line}\n"
    body = {"client": {"framework": "x", "language": "python", "version": "1"},
            "test": {"id": "t::t", "title": "t"}, "failure": {"error": error, "stack": []},
            "source": {"roots": ["pages"], "files": [{"path": "pages/p.py", "content": page}]},
            "page": {"url": "http://app.example/", "language": {"html_lang": "en"}, "inventory": inventory},
            "options": {"max_rounds": 3}, "run": {"id": "r", "repo": "app"}}
    body.update(over)
    return HealRequest.model_validate(body)


def ok(ref):
    return ResultRequest.model_validate({"count": 1, "hit": {"ref": ref}, "test_outcome": "passed"})


def test_an_unproven_pick_is_refused_and_the_models_next_answer_is_used():
    inv = [dict(m, ref=f"e{i}") for i, m in enumerate(row_menus("Open menu for order"))] + [dict(TABS[0], ref="e9")]
    model = FakeModel('{"ref": "e0"}', '{"ref": "e9"}')
    r = session(model).start(request(inv))
    assert r.status == "try" and r.candidate.target_ref == "e9" and len(model.calls) == 2
    assert "e0 | " not in model.calls[1]["prompt"]


def test_without_a_next_answer_the_heal_ends_needs_human_no_identity_proof():
    inv = [dict(m, ref=f"e{i}") for i, m in enumerate(row_menus("Open menu for order"))]
    r = session(FakeModel('{"ref": "e0"}', '{"ref": "e1"}')).start(request(inv))
    assert r.status == "needs_human" and r.reason == "no_identity_proof"


def test_v9_export_csv_removed_ends_needs_human_not_export_pdf():
    inv = [dict(EXPORT_PDF, ref="e0"), dict(CANCEL, ref="e1")]
    r = session(FakeModel('{"ref": "e0"}', '{"ref": "e1"}')).start(
        request(inv, "self.page.locator('[data-cy=export-csv]').click()", "waiting for locator('[data-cy=export-csv]')"))
    assert r.status == "needs_human" and r.candidate is None


def test_a_null_ref_is_the_model_saying_none_of_them_and_ends_target_missing():
    inv = [dict(EXPORT_PDF, ref="e0"), dict(CANCEL, ref="e1")]
    model = FakeModel('{"ref": null, "why": "the csv button was removed"}')
    r = session(model).start(
        request(inv, "self.page.locator('[data-cy=export-csv]').click()", "waiting for locator('[data-cy=export-csv]')"))
    assert r.status == "needs_human" and r.reason == "target_missing" and len(model.calls) == 1


def test_parse_pick_accepts_a_null_ref_and_still_refuses_a_missing_one():
    p = parse_pick('{"ref": null, "why": "removed"}', {"e1"})
    assert p.ref is None and p.none and p.why == "removed"
    assert not parse_pick('{"why": "x"}', {"e1"}).none
    assert parse_pick('{"ref": "e9"}', {"e1"}).reason == "ref_not_on_list"


def test_the_prompt_offers_none_explicitly():
    prompt = build_prompt([(1, "page.locator('#x').click()", True)], "en", ["e1 | tag=\"button\""])
    assert '{"ref": null' in prompt and "none of the listed elements" in prompt


# ---- frames ------------------------------------------------------------------------------------------------------------
IFRAME = e("f0", "iframe", id="payment-frame", name_attr="payment", title="Secure card entry", box=box(20, 300, 400, 190))
OTHER_IFRAME = e("f1", "iframe", id="ads", name_attr="ads", box=box(20, 600, 300, 100))
ZIP = e("p1", "input", type="text", name_attr="zip", id=":r8:", label="ZIP code", box=box(20, 520, 200, 30))
CARD = e("a1", "input", type="text", name_attr="card-number", id="cc", label="Card number", frame="payment",
         box=box(40, 330, 200, 30))
EXP = e("a2", "input", type="text", name_attr="exp", id="exp", label="Expiry date", placeholder="MM/YY", frame="payment",
        box=box(40, 380, 200, 30))
AD_INPUT = e("g1", "input", type="text", name_attr="card", id="adcard", label="Card", frame="ads", box=box(30, 610, 100, 30))
FRAME_PAGE = [IFRAME, OTHER_IFRAME, ZIP, CARD, EXP, AD_INPUT]


def test_frame_hop_reads_the_last_hop_and_not_a_hop_that_ends_the_call():
    call = 'self.page.frame_locator("#payment-frame").locator("input[name=card]")'
    assert frame_hop(call) == ('self.page.frame_locator("#payment-frame")', "#payment-frame", True)
    assert frame_hop('self.page.frame_locator("#payment-frame")') is None
    assert frame_hop("self.page.locator('#a').locator('#b')") is None
    assert frame_hop("this.page.frameLocator('#f').getByLabel('x')")[:2] == ("this.page.frameLocator('#f')", "#f")
    cf = frame_hop("page.locator('#pay').content_frame.locator('x')")
    assert cf[1] == "#pay" and cf[2] is False


def test_the_hop_selector_resolves_to_the_listed_frame_by_id_name_or_title():
    flat = [dict(x) for x in FRAME_PAGE]
    assert resolve_frame("#payment-frame", flat) == "payment"
    assert resolve_frame("iframe[name=payment]", flat) == "payment"
    assert resolve_frame("iframe[title='Secure card entry']", flat) == "payment"
    assert resolve_frame("#nowhere", flat) is None


def test_the_scope_holds_only_the_elements_of_that_frame():
    s = scope_of('page.frame_locator("#payment-frame").locator("input[name=card]")', [dict(x) for x in FRAME_PAGE], True)
    assert [x["ref"] for x in s.elements] == ["a1", "a2"] and all("frame" not in x for x in s.elements)
    assert scope_of('page.locator("input[name=zip]")', [dict(x) for x in FRAME_PAGE], True) is None


def frame_inventory():
    return [dict(x, ref=f"e{i}") for i, x in enumerate(FRAME_PAGE)]      # e0 iframe, e2 zip, e3 card, e4 exp, e5 ad input


def frame_request(line, error):
    return request(frame_inventory(), line, error)


def test_an_in_frame_step_is_offered_only_the_elements_of_its_frame_and_heals_with_the_same_hop():
    line = "self.page.frame_locator('#payment-frame').locator('input[name=card]').fill('4242')"
    err = "waiting for locator(\"input[name=card]\")"
    model = FakeModel('{"ref": "e3", "why": "the card number box"}')
    sess = session(model)
    r = sess.start(frame_request(line, err))
    prompt = model.calls[0]["prompt"]
    assert "e3 | " in prompt and "e4 | " in prompt
    assert "e2 | " not in prompt and "e5 | " not in prompt and "e0 | " not in prompt       # zip, the ad frame's input, iframe
    assert r.status == "try" and r.candidate.target_ref == "e3"
    assert r.candidate.code.startswith("self.page.frame_locator('#payment-frame').locator(")
    assert r.candidate.patch.from_ == "self.page.frame_locator('#payment-frame').locator('input[name=card]')"
    assert r.candidate.locator["kind"] != "by_language"
    assert r.candidate.locator["chain"][0]["m"] == "frame_locator"
    assert sess.result(r.heal_id, ok("e3")).status == "healed"


def test_a_page_level_element_is_never_offered_for_an_in_frame_step():
    line = "self.page.frame_locator('#payment-frame').locator('input[name=card]').fill('4242')"
    err = "waiting for locator(\"input[name=card]\")"
    model = FakeModel('{"ref": "e2", "why": "the zip box"}')                # e2 is page-level: not on the list
    r = session(model).start(frame_request(line, err))
    assert r.status == "needs_human" and r.reason == "no_pick"


def test_a_frame_that_cannot_be_resolved_offers_nothing():
    line = "self.page.frame_locator('#gone').locator('input[name=card]').fill('4242')"
    model = FakeModel('{"ref": "e3"}')
    r = session(model).start(frame_request(line, "waiting for locator(\"input[name=card]\")"))
    assert r.status == "needs_human" and r.reason == "target_missing" and model.calls == []


def test_a_fingerprint_for_an_in_frame_step_is_matched_inside_the_frame_only():
    line = "self.page.frame_locator('#payment-frame').locator('input[name=card]').fill('4242')"
    last = {"fingerprint": {"tag": "input", "attrs": {"name": "zip"}}, "language": "en"}      # only the page-level zip has it
    model = FakeModel('{"ref": null}')
    r = session(model).start(request(frame_inventory(), line, "waiting for locator(\"input[name=card]\")", last_pass=last))
    assert r.settled_at != 2 and not (r.candidate and "zip" in r.candidate.code)


def test_v8_the_in_frame_label_step_picks_by_shared_copy():
    line = "self.page.frame_locator('#payment-frame').get_by_label('Expiry').fill('12/30')"
    model = FakeModel('{"ref": "e4"}')
    r = session(model).start(frame_request(line, "waiting for get_by_label(\"Expiry\")"))
    assert r.status == "try" and r.candidate.target_ref == "e4" and r.candidate.code.startswith("self.page.frame_locator(")


def test_the_typescript_spelling_of_the_hop_is_kept():
    from voyageur.frames import prefixed
    from voyageur.locator_policy import Written
    w = Written("css", 3, {"python": 'self.page.locator("input[name=\'a\']")', "typescript": "this.page.locator(\"input[name='a']\")"})
    out = prefixed(w, 'self.page.frame_locator("#f")', "self.page", "python")
    assert out.code["python"].startswith('self.page.frame_locator("#f").locator(')
    assert out.code["typescript"].startswith('this.page.frameLocator("#f").locator(')
    assert out.neutral[0]["m"] == "frame_locator"


# ---- tightened rules (security review: proofs that accept the wrong element on honest pages) ---------------------------
def test_digit_only_words_prove_nothing_btn_1_is_not_page_1_of_5():
    page_btn = e("p1", name="Page 1 of 5", text="Page 1 of 5", box=box(10, 10))
    other = e("p2", name="Next", text="Next", box=box(100, 10))
    assert check(page_btn, [page_btn, other], 'page.locator("#btn-1")') == "no_identity_proof"
    assert words("btn-1") == set() and words("Page 1 of 5") == {"page"}


def test_a_number_counts_only_beside_a_matching_word():
    ok = e("o1", name="Order 1 details", testid="order-1-details", box=box(10, 10))
    wrong = e("o2", name="Order 2 details", testid="order-2-details", box=box(10, 50))
    call = 'page.locator("#order-1")'
    assert check(ok, [ok, wrong], call) is None
    assert check(wrong, [ok, wrong], call) == "no_identity_proof"


def test_copy_one_shared_word_is_no_proof_delete_account_vs_delete_all_backups():
    wrong = e("d2", name="Delete all backups", text="Delete all backups")
    keep = e("d1", name="Delete account", text="Delete account")
    call = 'page.get_by_role("button", name="Delete account")'
    assert check(wrong, [wrong, keep], call) == "no_identity_proof"
    assert check(keep, [wrong, keep], call) is None


def test_copy_two_shared_meaningful_words_prove():
    now = e("s1", name="Save changes now")
    assert prove(now, None, 'page.get_by_role("button", name="Save changes")', [now])[0][0] == "copy"


def test_copy_stopwords_and_short_words_do_not_count_as_shared_words():
    now = e("s1", name="Save the changes to all")
    assert prove(now, None, 'page.get_by_role("button", name="Discard the changes to all")', [now])[0] is None


def test_copy_stems_need_whole_tokens_of_five_letters_and_a_five_letter_common_prefix():
    near = e("r1", name="Create repository")
    assert prove(near, None, 'page.get_by_role("button", name="Create report")', [near])[0] is None
    stem = e("m1", name="Manager of customer")
    assert prove(stem, None, 'page.get_by_role("button", name="Manage customers")', [stem])[0][0] == "copy"


def test_fp_place_with_completely_different_copy_needs_the_top_at_centre_flag():
    unknown = e("b1", name="Store", box=box(102, 201))
    assert prove(unknown, FP, "", [unknown])[0] is None
    covered = e("b2", name="Store", box=box(102, 201), hit=False)
    assert prove(covered, FP, "", [covered])[0] is None
    top = e("b3", name="Store", box=box(102, 201), hit=True)
    assert prove(top, FP, "", [top])[0][0] == "place"


def test_fp_place_flag_absent_is_enough_when_the_copy_is_partly_the_same_or_missing():
    part = e("b1", name="Save", box=box(102, 201))
    assert prove(part, FP, "", [part])[0][0] == "place"
    blank = e("b2", box=box(102, 201))
    assert prove(blank, FP, "", [blank])[0][0] == "place"


def test_fp_place_a_covered_element_is_refused_even_with_overlapping_copy():
    part = e("b1", name="Save", box=box(102, 201), top_at_center=False)
    assert prove(part, FP, "", [part])[0] is None


# ---- a translated page: the copy is expected to differ completely, place still proves --------------------------------
NEWS_THEN = {"tag": "input", "type": "checkbox", "role": "checkbox", "name": "Email me the newsletter",
             "label": "Email me the newsletter", "box": box(40, 300, 20, 20)}
NEWS_NOW = e("n1", "input", type="checkbox", role="checkbox", name="Recevoir la lettre d'information",
             label="Recevoir la lettre d'information", box=box(40, 300, 20, 20))


def test_translated_page_same_box_and_kind_is_place_proof_with_no_shared_word():
    assert prove(NEWS_NOW, NEWS_THEN, "", [NEWS_NOW], language_changed=True)[0][0] == "place"
    assert verify_pick(NEWS_NOW, {"n1"}, [NEWS_NOW], NEWS_THEN, "check", (), (), "", language_changed=True) is None


def test_the_same_box_swap_with_the_language_unchanged_is_no_proof():
    assert prove(NEWS_NOW, NEWS_THEN, "", [NEWS_NOW])[0] is None
    assert verify_pick(NEWS_NOW, {"n1"}, [NEWS_NOW], NEWS_THEN, "check", (), (), "") == "no_identity_proof"


def test_translated_page_the_top_check_and_the_kind_still_apply():
    covered = dict(NEWS_NOW, top_at_center=False)
    assert prove(covered, NEWS_THEN, "", [covered], language_changed=True)[0] is None
    other_kind = e("n2", "button", name="Recevoir", box=box(40, 300, 20, 20))
    assert prove(other_kind, NEWS_THEN, "", [other_kind], language_changed=True)[0] is None
    moved = dict(NEWS_NOW, box=box(500, 600, 20, 20))
    assert prove(moved, NEWS_THEN, "", [moved], language_changed=True)[0] is None


# ---- renumbered generated ids (react-select-3-option-6 -> react-select-4-option-6) -------------------------------------
def options(widget, idx, **kw):
    return [e(f"o{widget}{i}", tag="div", name=f"City {i}", text=f"City {i}", id=f"react-select-{widget}-option-{i}",
              row=f"react-select-{widget}-listbox", box=box(10, 40 * i), **kw) for i in idx]


def test_without_a_fingerprint_a_renumbered_id_alone_is_not_proof():
    # removed after the 2026-11-07 security review: with no recording, a renumbered widget and a removed row's lone
    # surviving neighbour (menu-1042 -> menu-1043) look the same; the fingerprint path (renumbered together) heals it
    page = options(4, range(9))
    assert check(page[6], page, 'page.locator("#react-select-3-option-6")') is not None


def test_a_sibling_option_is_not_proven_by_a_renumbered_id():
    page = options(4, range(9))
    assert check(page[3], page, 'page.locator("#react-select-3-option-6")') is not None


def test_one_number_changed_is_not_proof_when_several_elements_qualify():
    page = options(3, [0, 1, 2, 3, 4, 5, 7])                  # option 6 removed: 3-option-7 and others differ by one number
    assert check(page[-1], page, 'page.locator("#react-select-3-option-6")') is not None


def test_a_renumbered_id_needs_the_old_id_gone():
    page = options(3, [6]) + options(4, [6])
    assert check(page[1], page, 'page.locator("#react-select-3-option-6")') is not None


# ---- a renamed row container ------------------------------------------------------------------------------------------
def test_a_renamed_row_does_not_refuse_the_element_that_kept_its_unique_test_id():
    label = e("b1", tag="b", name="Page", text="Page", testid="on-call-direct-page-label", row="oncall-page-card",
              box=box(63, 386, 33, 16))
    page = [label, e("x", tag="div", name="Row APage", testid="oncall-page-card", box=box(0, 380, 400, 30))]
    fp = {"tag": "b", "name": "Page", "text": "Page", "row": "on-call-direct-page", "testid": "on-call-direct-page-label",
          "box": box(63, 386, 33, 16)}
    assert check(label, page, 'page.get_by_test_id("on-call-direct-page-label")', fp=fp) is None


def test_a_removed_rows_button_is_still_not_its_neighbours_shared_hook():
    menus = row_menus()
    fp = {"tag": "button", "name": "Actions for order 1046", "data_cy": "row-menu", "row": "order-row-1046",
          "box": box(900, 340, 30, 30)}
    assert check(menus[5], menus, 'page.locator("[data-cy=row-menu]").nth(6)', fp=fp) is not None


def test_the_old_row_still_on_the_page_keeps_the_refusal():
    label = e("b1", tag="b", name="Page", testid="lbl", row="row-b", box=box(0, 0))
    page = [label, e("y", tag="i", name="x", row="row-a", box=box(0, 50))]
    fp = {"tag": "b", "name": "Page", "row": "row-a", "testid": "lbl", "box": box(0, 0)}
    assert check(label, page, 'page.get_by_test_id("lbl")', fp=fp) == "different_element"


def test_a_widget_renumbered_as_a_whole_keeps_its_option():
    page = options(4, range(9), **{"class": "css-option"})
    fp = {"tag": "div", "name": "City 6", "text": "City 6", "row": "react-select-3-listbox", "id": "react-select-3-option-6",
          "box": box(10, 240)}
    assert check(page[6], page, 'page.locator("#react-select-3-option-6")', fp=fp) is None


def test_a_renumbered_row_with_different_copy_is_still_another_element():
    menus = [e(f"m{n}", name=f"Actions for order {n}", id=f"menu-{n}", row=f"order-row-{n}", box=box(900, 100)) for n in (1043, 1044)]
    fp = {"tag": "button", "name": "Actions for order 1042", "id": "menu-1042", "row": "order-row-1042", "box": box(900, 100)}
    assert check(menus[0], menus, 'page.locator("#menu-1042")', fp=fp) is not None


def test_a_renamed_panel_keeps_the_item_with_its_unique_data_value_and_copy():
    li = e("l1", tag="li", name="Payment captured", text="Payment captured", data_event="e301", row=":r5:", box=box(875, 263))
    page = [li, e("l2", tag="li", name="Order placed", text="Order placed", data_event="e300", row=":r5:", box=box(875, 300))]
    fp = {"tag": "li", "name": "Payment captured", "text": "Payment captured", "row": "panel-history", "data_event": "e301",
          "box": box(875, 263)}
    assert check(li, page, 'page.locator("[role=tabpanel][aria-labelledby=tab-history] li").first', fp=fp) is None


def test_a_reindexed_small_number_is_no_identity_for_a_renamed_row():
    rows = [e(f"m{i}", name="⋯", text="⋯", data_index=str(i), row=f"row-{i + 10}", box=box(900, 100 + 40 * i))
            for i in range(3)]
    fp = {"tag": "button", "name": "⋯", "text": "⋯", "data_index": "2", "row": "row-99", "box": box(900, 180)}
    assert check(rows[2], rows, 'page.locator("[data-index=\'2\'] button")', fp=fp) is not None


# ---- security review 2026-11-07: a lone survivor of a removed row is not the removed row's element -----------------------
def test_without_a_fingerprint_a_lone_one_number_neighbour_proves_nothing():
    page = [e("i4", tag="div", name="Invoice", id="invoice-4", box=box(0, 0))]
    assert check(page[0], page, 'page.locator("#invoice-3")') is not None
    menus = [e("m", name="⋯", id="menu-1043", box=box(0, 0))]
    assert check(menus[0], menus, 'page.locator("#menu-1042")') is not None


def _row_case(now_box, **extra):
    then = {"tag": "button", "name": "Delete", "text": "Delete", "row": "order-12", "box": box(900, 100), **extra}
    now = e("b13", name="Delete", text="Delete", row="order-13", box=now_box, **extra)
    return now, then


@pytest.mark.parametrize("extra", [{"testid": "del-btn"}, {"data_order_ref": "ord-a"}])
def test_a_surviving_neighbour_rows_unique_hook_elsewhere_on_the_page_is_another_element(extra):
    now, then = _row_case(box(900, 140), **extra)            # the row below: not the recorded place
    assert check(now, [now], 'page.get_by_test_id("del-btn")', fp=then) is not None


def test_a_renumbered_neighbour_row_elsewhere_is_another_element():
    then = {"tag": "button", "name": "Delete", "row": "order-12", "id": "b-12", "box": box(900, 100)}
    now = e("b13", name="Delete", row="order-13", id="b-13", box=box(900, 140))
    assert check(now, [now], 'page.locator("#b-12")', fp=then) is not None


# ---- live run 2026-11-07: the wrapper of the recorded element, in the very same box, is not the element ---------------------
def test_the_container_the_recorded_element_sat_in_is_not_the_element():
    same = box(23, 17, 1234, 19.6)
    wrapper = e("w", tag="div", name="Admin", text="Admin", testid="category-space-permission", box=same, top_at_center=True)
    inner = e("i", tag="div", role="combobox", **{"class": "MuiSelect-select MuiSelect-outlined"}, row="category-space-permission",
              box=same, top_at_center=True)
    fp = {"tag": "div", "role": "combobox", "row": "category-space-permission", "class": "MuiSelect-select MuiSelect-outlined",
          "box": same}
    call = 'page.locator("data-test=category-space-permission >> .MuiSelect-select")'
    assert check(wrapper, [wrapper, inner], call, fp=fp) is not None
    assert check(inner, [wrapper, inner], call, fp=fp) is None


# ---- scoped proof (no fingerprint): the part of the selector before the last combinator names the scope -------------------
NOTE = dict(tag="textarea", placeholder="Internal note", name_attr="note", box=box(10, 10))


def test_the_only_element_of_its_tag_in_the_named_scope_is_proven():
    pick = e("t", row="panel-notes", **NOTE)
    page = [pick, e("p", tag="div", id="panel-notes", box=box(0, 0, 400, 200)), e("s", tag="input", placeholder="Search", box=box(0, 300))]
    assert check(pick, page, 'page.locator("#panel-notes > textarea")', action="fill") is None


def test_two_of_the_tag_in_the_scope_prove_nothing():
    a, b = e("t1", row="panel-notes", **NOTE), e("t2", tag="textarea", placeholder="Reason", row="panel-notes", box=box(10, 60))
    assert check(b, [a, b], 'page.locator("#panel-notes > textarea")', action="fill") is not None


def test_a_removed_buttons_sibling_with_another_class_is_not_proven_by_scope():
    edit = e("ed", name="Edit", **{"class": "btn edit"}, row="row-5", box=box(0, 0))
    assert check(edit, [edit], 'page.locator("#row-5 > button.delete")') is not None


def test_a_renamed_scope_is_proven_by_the_only_element_of_its_tag_sharing_the_scopes_word():
    pick = e("t", row=":r6:", **NOTE)
    page = [pick, e("s", tag="input", placeholder="Search", box=box(0, 300))]
    assert check(pick, page, 'page.locator("#panel-notes > textarea")', action="fill") is None


def test_a_renamed_scope_proves_nothing_when_the_element_shares_no_word_with_it():
    pick = e("t", tag="textarea", placeholder="Shipping address", row=":r6:", box=box(10, 10))
    assert check(pick, [pick], 'page.locator("#panel-notes > textarea")', action="fill") is not None


def test_the_only_button_in_the_named_custom_elements_shadow_root_is_proven():
    toggle = e("f", role="switch", name="Flag for review", **{"class": "_toggle_41f98"}, shadow="order-flag", box=box(0, 0))
    other = e("o", name="Save", box=box(0, 100))
    assert check(toggle, [toggle, other], 'page.locator("order-flag button.toggle")') is None
    assert check(other, [toggle, other], 'page.locator("order-flag button.toggle")') is not None


def test_none_after_an_unproven_pick_reports_the_missing_proof_not_a_missing_target():
    page = "class P:\n    def go(self):\n        self.page.locator('button.sc-fzXfKy.jQdBvX').click()\n"
    inv = [{"tag": "button", "name": "Cancel order", "text": "Cancel order", "class": "sc-dkzDqf kLmNoP",
            "box": {"x": 20, "y": 600, "width": 100, "height": 30}},
           {"tag": "input", "placeholder": "Search", "box": {"x": 20, "y": 10, "width": 100, "height": 30}}]
    body = base_request(inventory=inv, failure={"error": "waiting for locator('button.sc-fzXfKy.jQdBvX')", "stack": []},
                   source={"roots": ["pages"], "files": [{"path": "pages/p.py", "content": page}]})
    model = FakeModel('{"ref": "e0"}', '{"ref": null, "why": "gone"}')
    r = session(model).start(HealRequest.model_validate(body))
    assert r.status == "needs_human" and r.reason == "no_identity_proof", (r.reason, r.why)


# ---- scoped proof, security review 2026-11-07 (each of these accepted before the fix) ---------------------------------------
def _b(ref, **kw):
    return e(ref, box=box(0, 40 * int(ref[-1]) if ref[-1].isdigit() else 0), **kw)


@pytest.mark.parametrize("call, pick, others", [
    ('page.locator("#r1 > button")', dict(name="Save", row="r1", hidden=True), [dict(name="Cancel", row="r1")]),
    ('page.locator("#r1 > button:not(.delete)")', dict(name="Delete", row="r1", **{"class": "btn delete"}), []),
    ('page.locator("div:not(#r1) > button")', dict(name="Cancel", row="r1"), []),
    ('page.locator("#r1 > button[type=submit]")', dict(name="Cancel", row="r1"), []),
    ("page.locator(\"#r1 > button:has-text('Delete')\")", dict(name="Cancel", row="r1"), []),
    ('page.locator("#r1 > button").nth(1)', dict(name="Cancel", row="r1"), []),
    ('page.locator("#main > textarea#reason")', dict(tag="textarea", placeholder="Notes", row="main"), []),
    ('page.locator("#r1 > button.active")', dict(name="Off", row="r1", **{"class": "btn inactive"}), []),
    ('page.locator("#r1 > button.edit")', dict(name="Pay", row="r1", **{"class": "credit"}), []),
    ('page.locator("#table #row-5 button")', dict(name="Delete", row="table"), []),
    ('page.locator("#main > textarea")', dict(tag="textarea", placeholder="Main notes for driver", row=":r9:"), []),
    ('page.locator("#r1 > button")', dict(name="Pay", row="r1", frame="pay"), []),
])
def test_scoped_proof_refuses_what_the_selector_did_not_say(call, pick, others):
    p = e("p0", **{"tag": "button", **pick}, box=box(0, 0))
    rest = [e(f"o{i}", **{"tag": "button", **o}, box=box(0, 50 + 40 * i)) for i, o in enumerate(others)]
    assert prove(p, None, call, [p, *rest])[0] is None


def test_scoped_proof_stays_cheap_with_repeated_hooks():
    import time
    call = 'page.locator("' + "#a" * 490 + ' > textarea")'
    items = [e(f"i{n}", tag="div", id=f"x{n}", row=f"y{n}", box=box(0, n)) for n in range(500)]
    pick = e("t", tag="textarea", row=":r1:", box=box(0, 0))
    t = time.perf_counter()
    for _ in range(50):
        prove(pick, None, call, [pick, *items])
    assert time.perf_counter() - t < 2.0


# ---- a row whose generated id was regenerated (React useId :r2u: -> :r5k:) ------------------------------------------------
def radio_page(row="accordion-item-:r5k:"):
    holder = e("h", tag="div", id=row, box=box(0, 260, 400, 60), text="Yes", name="Yes")
    yes = e("y", tag="span", name="Yes", text="Yes", row=row, row_attr="id", box=box(23, 277, 23, 16), copy_only=True)
    return holder, yes


RADIO_THEN = {"tag": "span", "name": "Yes", "text": "Yes", "row": "accordion-item-:r2u:", "box": box(23, 277, 23, 16)}


def test_a_row_with_a_regenerated_generated_id_keeps_the_element_with_the_same_copy_in_the_same_place():
    holder, yes = radio_page()
    assert check(yes, [holder, yes], 'page.locator("[id=\\"accordion-item-\\\\:r2u\\\\:\\"] > .a")', fp=RADIO_THEN, complete=True) is None


def test_a_regenerated_row_id_is_no_proof_when_the_copy_changed_or_the_place_moved():
    holder, yes = radio_page()
    assert check(dict(yes, name="No", text="No"), [holder, dict(yes, name="No", text="No")], "page.locator('.a')",
                 fp=RADIO_THEN, complete=True) == "different_element"
    moved = dict(yes, box=box(300, 500, 23, 16))
    assert check(moved, [holder, moved], "page.locator('.a')", fp=RADIO_THEN, complete=True) == "different_element"


def test_a_regenerated_row_id_is_no_proof_when_another_row_holds_the_same_copy():
    holder, yes = radio_page()
    twin = e("t", tag="span", name="Yes", text="Yes", row="accordion-item-:r9z:", row_attr="id", box=box(23, 500, 23, 16))
    assert check(yes, [holder, yes, twin], "page.locator('.a')", fp=RADIO_THEN, complete=True) == "different_element"


def test_a_row_renamed_to_a_hand_written_id_is_still_another_row():
    holder, yes = radio_page("billing-section")
    assert check(yes, [holder, yes], "page.locator('.a')", fp=RADIO_THEN, complete=True) == "different_element"


def test_a_regenerated_row_id_needs_a_listing_that_holds_every_element():
    holder, yes = radio_page()
    call = 'page.locator("[id=\\"accordion-item-\\\\:r2u\\\\:\\"] > .a")'
    assert check(yes, [holder, yes], call, fp=RADIO_THEN, complete=True) is None
    assert check(yes, [holder, yes], call, fp=RADIO_THEN, complete=False) == "different_element"       # a twin may be unlisted


def test_a_regenerated_row_id_with_the_same_copy_elsewhere_or_with_no_box_is_refused_by_the_rule_itself():
    from voyageur.gates import _regenerated_row
    holder, yes = radio_page()
    assert _regenerated_row(RADIO_THEN, yes, [holder], True) is True
    assert _regenerated_row(RADIO_THEN, dict(yes, box=box(300, 500, 23, 16)), [holder], True) is False        # copy matches, elsewhere
    assert _regenerated_row(RADIO_THEN, {k: v for k, v in yes.items() if k != "box"}, [holder], True) is False
    assert _regenerated_row({k: v for k, v in RADIO_THEN.items() if k != "box"}, yes, [holder], True) is False
    elsewhere = dict(yes, box=box(300, 500, 23, 16))
    assert check(elsewhere, [holder, elsewhere], "page.locator('.a')", fp=RADIO_THEN) == "different_element"
    assert check({k: v for k, v in yes.items() if k != "box"}, [holder, yes], "page.locator('.a')",
                 fp=RADIO_THEN) == "different_element"


def test_a_regenerated_row_id_is_no_proof_while_short_copy_was_withheld():
    # an option or a grid cell with the same copy in another row is not listed: "no other row holds it" cannot be said
    holder, yes = radio_page()
    page = [holder, yes]
    items = {i["ref"] for i in page}
    args = (yes, items, page, RADIO_THEN, "click", (), (), "page.locator('.a')", False, "", True)
    assert verify_pick(*args) is None
    assert verify_pick(*args, copy_short=True) == "different_element"
