"""
===============================================================================
Frame hop healing tests (the iframe the step enters is renamed or gone; the element in it is not)
===============================================================================
The three steps the real-locator bench could not heal, rebuilt as inventories:
    f73  frame_locator("iframe[name^='ApxSecureIframe']").get_by_role("textbox", name="Card number")  -> the frame was renamed
    f74  frame_locator("iframe").first.frame_locator('iframe[name="layui-layer-iframe1"]').locator("text=...")  -> inner renamed
    f75  frame_locator('iframe[name="editor-canvas"]').locator(".wp-block-post-title")  -> the iframe is gone, the title is in the page
         (a bare class is no proof: the listing cannot say nothing else has it; the same step on `button.primary` heals)
plus the cases that must stay needs_human: two frames that could hold the element, a hop the service cannot read, a
list cut short, an element nothing shows is the step's.

Author: PMAC
===============================================================================
"""
import pytest

from voyageur.frame_heal import frame_selectors, heal_hop, unstable_name
from voyageur.gates import verify_pick
from voyageur.locator_policy import render_chain
from voyageur.locator_steps import try_parse
from voyageur.schemas import HealRequest, ResultRequest
from voyageur.stages import analyze, write_for
from tests.unit.scenarios import FakeModel, session

pytestmark = pytest.mark.unit

RECEIVER = "page"
CARD_CALL = """page.frame_locator("iframe[name^='ApxSecureIframe']").get_by_role("textbox", name="Card number")"""
NESTED_CALL = """page.frame_locator("iframe").first.frame_locator('iframe[name="layui-layer-iframe1"]').locator("text=确认")"""
EDITOR_CALL = """page.frame_locator('iframe[name="editor-canvas"]').locator("button.primary")"""
TITLE_CALL = """page.frame_locator('iframe[name="editor-canvas"]').locator(".wp-block-post-title")"""


def e(ref, tag, **kw):
    return {"ref": ref, "tag": tag, **kw}


def box(x, y, w=164, h=30):
    return {"x": x, "y": y, "width": w, "height": h}


STRIPE = e("f0", "iframe", name_attr="__privateStripeFrame9876", title="Secure card payment", box=box(23, 55, 1234, 130))
AMAZON = e("f1", "iframe", name_attr="AmazonPayFrame_k3j2h", title="Secure payment", box=box(23, 213, 1234, 130))
STRIPE_NUMBER = e("a0", "input", name_attr="number", placeholder="Card number", frame="__privateStripeFrame9876")
CARD = e("a1", "input", type="text", id="cn", label="Card number", frame="AmazonPayFrame_k3j2h", box=box(119, 224))
CARD_FP = {"tag": "input", "type": "text", "label": "Card number", "id": "cn", "box": box(95, 10)}
CARD_PAGE = [STRIPE, AMAZON, STRIPE_NUMBER, CARD]


def resolved(call, items, fp=None, action="fill", language="python", complete=True):
    return heal_hop(call, [dict(i) for i in items], complete, fp, action, language, "data-testid", 0)


# ---- the renamed frame ---------------------------------------------------------------------------------------------------
def test_a_renamed_frame_is_found_by_the_element_in_it_that_the_recording_proves():
    out = resolved(CARD_CALL, CARD_PAGE, CARD_FP)
    assert out.hop is not None and out.scope.frame == "AmazonPayFrame_k3j2h"
    assert out.hop.prefixes[0] == """page.frame_locator("iframe[title='Secure payment']")"""
    assert out.hop.inner == """.get_by_role("textbox", name="Card number")"""
    assert [x["ref"] for x in out.scope.elements] == ["a1"]
    assert out.scope.prefix == out.hop.prefixes[0]


def test_the_typescript_hop_is_written_in_typescript():
    call = """this.page.frameLocator("iframe[name^='ApxSecureIframe']").getByRole('textbox', { name: 'Card number' })"""
    out = resolved(call, CARD_PAGE, CARD_FP, language="typescript")
    assert out.hop.prefixes[0] == """this.page.frameLocator('iframe[title="Secure payment"]')"""
    assert out.hop.inner == """.getByRole('textbox', { name: 'Card number' })"""


def test_without_a_recording_the_step_s_own_words_find_the_frame():
    out = resolved(CARD_CALL, [STRIPE, AMAZON, CARD], None)
    assert out.hop is not None and out.scope.frame == "AmazonPayFrame_k3j2h"


def test_the_same_copy_in_another_frame_is_no_proof_when_the_recording_names_the_element_by_an_id():
    other = e("a1", "input", type="text", id="zip", label="ZIP code", frame="AmazonPayFrame_k3j2h")
    out = resolved(CARD_CALL, [STRIPE, AMAZON, STRIPE_NUMBER, other], CARD_FP)
    assert out.hop is None
    plain = {k: v for k, v in CARD_FP.items() if k != "id"}                 # a recording with no stable attribute to lack
    assert resolved(CARD_CALL, [STRIPE, AMAZON, STRIPE_NUMBER, other], plain).hop is not None


def test_two_frames_that_could_hold_the_element_end_unresolved_and_say_so():
    twin = e("a2", "input", type="text", id="cn2", label="Card number", frame="__privateStripeFrame9876")
    out = resolved(CARD_CALL, CARD_PAGE + [twin], {**CARD_FP, "id": ""})
    assert out.hop is None and "more than one" in out.why


def test_an_element_nothing_shows_is_the_steps_leaves_the_frame_unresolved():
    other = e("a1", "input", type="text", id="zip", label="ZIP code", frame="AmazonPayFrame_k3j2h")
    out = resolved(CARD_CALL, [STRIPE, AMAZON, other], CARD_FP)
    assert out.hop is None and out.scope is None


def test_a_list_cut_short_heals_nothing_a_missing_frame_could_be_the_part_that_was_cut():
    assert resolved(CARD_CALL, CARD_PAGE, CARD_FP, complete=False).hop is None


def test_a_hop_the_service_cannot_read_is_left_alone_it_may_still_resolve():
    call = """page.frame_locator("iframe.payment > .inner").get_by_role("textbox", name="Card number")"""
    assert resolved(call, CARD_PAGE, CARD_FP).hop is None
    call = """page.frame_locator("iframe[src*='pay']").get_by_role("textbox", name="Card number")"""
    assert resolved(call, CARD_PAGE, CARD_FP).hop is None


def test_a_frame_the_hop_still_names_is_not_healed():
    call = """page.frame_locator("iframe[title='Secure payment']").get_by_role("textbox", name="Zip")"""
    assert resolved(call, CARD_PAGE, CARD_FP).hop is None


def test_a_frame_with_nothing_stable_to_name_it_by_is_not_written():
    bare = e("f1", "iframe", box=box(23, 213, 1234, 130))
    lone = e("a1", "input", type="text", id="cn", label="Card number", frame="iframe")
    out = resolved(CARD_CALL, [STRIPE, bare, STRIPE_NUMBER, lone], CARD_FP)
    assert out.hop is None


def test_two_frames_sharing_one_key_cannot_be_told_apart_so_none_is_written():
    twin = e("f2", "iframe", name_attr="AmazonPayFrame_k3j2h", title="Other")
    out = resolved(CARD_CALL, [STRIPE, AMAZON, twin, STRIPE_NUMBER, CARD], CARD_FP)
    assert out.hop is None


# ---- the nested frame ----------------------------------------------------------------------------------------------------
OUTER = e("f0", "iframe", title="Customs form", box=box(23, 17, 1234, 130))
INNER = e("f1", "iframe", name_attr="layui-layer-iframe2", title="Confirm dialog", frame="iframe", box=box(38, 28, 1204, 130))
CONFIRM = e("b0", "button", name="确认", text="确认", frame="layui-layer-iframe2", box=box(53, 39, 50, 30))
CONFIRM_FP = {"tag": "button", "name": "确认", "text": "确认", "box": box(14, 10, 50, 30)}


def test_a_renamed_inner_frame_keeps_the_hops_before_it():
    out = resolved(NESTED_CALL, [OUTER, INNER, CONFIRM], CONFIRM_FP, action="click")
    assert out.hop is not None and out.scope.frame == "layui-layer-iframe2"
    assert out.hop.prefixes[0] == """page.frame_locator("iframe").first.frame_locator("iframe[title='Confirm dialog']")"""
    assert out.hop.inner == """.locator("text=确认")"""


def test_the_hop_is_named_among_the_frames_of_its_parent_only():
    other_parent = e("f8", "iframe", name_attr="other", title="Other form")
    sibling = e("f9", "iframe", name_attr="other-inner", title="Confirm dialog", frame="other")
    out = resolved(NESTED_CALL, [OUTER, other_parent, INNER, sibling, CONFIRM], CONFIRM_FP, action="click")
    assert out.hop is not None and "Confirm dialog" in out.hop.prefixes[0]


# ---- the frame that is gone ----------------------------------------------------------------------------------------------
TITLE = e("h0", "button", **{"class": "primary"}, box=box(23, 389, 1234, 39))
TITLE_FP = {"tag": "button", "class": "primary", "box": box(14, 28, 1204, 39)}
POST = e("h0", "h1", **{"class": "wp-block-post-title"}, box=box(23, 389, 1234, 39))
POST_FP = {"tag": "h1", "class": "wp-block-post-title", "box": box(14, 28, 1204, 39)}


def test_an_element_that_left_its_frame_is_found_by_the_steps_own_selector():
    out = resolved(EDITOR_CALL, [STRIPE, AMAZON, STRIPE_NUMBER, CARD, TITLE], TITLE_FP, action="click")
    assert out.hop is not None and out.scope is None
    assert out.hop.prefixes == ["page"] and out.hop.inner == """.locator("button.primary")"""


def test_the_selector_alone_proves_nothing_without_the_recording():
    out = resolved(EDITOR_CALL, [STRIPE, TITLE], None, action="click")
    assert out.hop is None


def test_a_selector_two_listed_elements_share_proves_neither():
    twin = e("h1", "button", **{"class": "primary other"}, box=box(23, 500, 1234, 39))
    out = resolved(EDITOR_CALL, [STRIPE, TITLE, twin], TITLE_FP, action="click")
    assert out.hop is None


def test_an_element_of_another_kind_that_carries_the_class_is_not_the_recorded_one():
    div = e("d0", "a", **{"class": "primary"}, box=box(23, 389, 1234, 39))
    assert resolved(EDITOR_CALL, [STRIPE, div], TITLE_FP, action="click").hop is None


def test_the_page_and_a_frame_both_holding_the_element_is_ambiguous():
    inside = e("h5", "button", **{"class": "primary"}, frame="__privateStripeFrame9876")
    out = resolved(EDITOR_CALL, [STRIPE, TITLE, inside], TITLE_FP, action="click")
    assert out.hop is None and "more than one" in out.why


def test_the_kept_selector_is_a_proof_for_the_gate_not_a_hole_in_it():
    inner = """.locator("button.primary")"""

    def gate(el, items, fp=TITLE_FP, kept=inner, complete=True):
        return verify_pick(el, {i["ref"] for i in items}, items, fp, "click", (), (), EDITOR_CALL, kept=kept, complete=complete)
    assert gate(TITLE, [TITLE], kept="") == "no_identity_proof"
    assert gate(TITLE, [TITLE]) is None
    assert gate(TITLE, [TITLE], fp=None) == "no_identity_proof"
    other_kind = e("h2", "a", **{"class": "primary"})
    assert gate(other_kind, [other_kind]) == "no_identity_proof"
    twin = e("h3", "button", **{"class": "primary"})
    assert gate(TITLE, [TITLE, twin]) == "no_identity_proof"
    assert gate(TITLE, [TITLE], kept=""".locator("button")""") == "no_identity_proof"      # a bare tag names no element


def test_the_listing_must_hold_every_element_the_selector_can_match():
    inner = """.locator(".primary")"""                              # a bare class: elements the probe does not list may have it

    def gate(el, items, kept, complete=True):
        return verify_pick(el, {i["ref"] for i in items}, items, TITLE_FP, "click", (), (), EDITOR_CALL, kept=kept, complete=complete)
    decoy = e("d9", "button", **{"class": "primary"}, hidden=True)
    assert gate(TITLE, [TITLE], inner) == "no_identity_proof"
    assert gate(TITLE, [TITLE, decoy], inner) == "no_identity_proof"
    scoped = """.locator("button.primary")"""
    assert gate(TITLE, [TITLE], scoped) is None
    assert gate(TITLE, [TITLE], scoped, complete=False) == "no_identity_proof"          # the listing was cut short
    cut = dict(TITLE, copy_cut=True)
    assert gate(cut, [cut], scoped) == "no_identity_proof"                              # copy-holding elements were left out


def test_a_bare_class_is_not_enough_to_drop_the_hop():
    out = resolved(TITLE_CALL, [STRIPE, AMAZON, POST], POST_FP, action="click")
    assert out.hop is None


def test_a_copy_cut_listing_places_nothing_by_the_selector():
    cut = dict(TITLE, copy_cut=True)
    assert resolved(EDITOR_CALL, [STRIPE, cut], TITLE_FP, action="click").hop is None


# ---- weak proofs ----------------------------------------------------------------------------------------------------------
HEADER = e("p0", "button", name="Pay", text="Pay", box=box(900, 10, 80, 30))
PAY_CALL = """page.frame_locator("iframe[name='checkout']").get_by_role("button", name="Pay")"""
PAY_FP = {"tag": "button", "name": "Pay", "text": "Pay", "box": box(10, 200, 80, 30)}


def test_a_same_copy_element_in_the_page_does_not_stand_for_a_frame_that_is_gone():
    out = resolved(PAY_CALL, [STRIPE, HEADER], PAY_FP, action="click")
    assert out.hop is None and "hop is not dropped" in out.why


def test_a_strong_proof_in_the_page_still_drops_the_hop():
    moved = dict(HEADER, id="pay-now")
    out = resolved(PAY_CALL, [STRIPE, moved], dict(PAY_FP, id="pay-now"), action="click")
    assert out.hop is not None and out.scope is None


def test_a_same_copy_field_in_an_unrelated_frame_is_not_the_renamed_frame():
    other = e("f5", "iframe", name_attr="ads", title="Sponsored")
    pay = e("b0", "button", name="Pay", text="Pay", frame="ads")
    out = resolved(PAY_CALL, [other, pay], PAY_FP, action="click")
    assert out.hop is None and "no word of the old selector" in out.why


def test_the_same_copy_in_a_frame_that_keeps_a_word_of_the_old_name_is_the_renamed_frame():
    renamed = e("f5", "iframe", name_attr="checkout-v2", title="Checkout")
    pay = e("b0", "button", name="Pay", text="Pay", frame="checkout-v2")
    out = resolved(PAY_CALL, [renamed, pay], PAY_FP, action="click")
    assert out.hop is not None and out.scope.frame == "checkout-v2"


def test_the_same_copy_places_nothing_while_copy_in_options_or_grid_cells_was_withheld():
    # an option or a grid cell with the same copy in another frame is not listed: "exactly one place holds it" cannot be said
    renamed = e("f5", "iframe", name_attr="checkout-v2", title="Checkout")
    pay = e("b0", "button", name="Pay", text="Pay", frame="checkout-v2")
    page = [dict(renamed), dict(pay)]
    assert heal_hop(PAY_CALL, [dict(i) for i in page], True, PAY_FP, "click", "python", "data-testid", 0, 0).hop is not None
    out = heal_hop(PAY_CALL, [dict(i) for i in page], True, PAY_FP, "click", "python", "data-testid", 0, 2)
    assert out.hop is None
    strong = dict(pay, id="pay-now")                       # a recording's unchanged attribute is not the weak proof
    kept = heal_hop(PAY_CALL, [dict(renamed), dict(strong)], True, dict(PAY_FP, id="pay-now"), "click", "python", "data-testid", 0, 2)
    assert kept.hop is not None


def test_without_a_recording_a_field_named_the_same_in_an_unrelated_frame_is_not_accepted():
    call = """page.frame_locator("iframe[name='billing']").locator("[name='cardnumber']")"""
    unrelated = e("f5", "iframe", name_attr="ads", title="Sponsored")
    field = e("a0", "input", name_attr="cardnumber", type="text", frame="ads")
    assert resolved(call, [unrelated, field], None).hop is None
    kin = e("f5", "iframe", name_attr="billing-form-2", title="Billing")
    field = e("a0", "input", name_attr="cardnumber", type="text", frame="billing-form-2")
    assert resolved(call, [kin, field], None).hop is not None


def test_without_a_recording_the_hop_is_never_dropped_to_the_page():
    call = """page.frame_locator("iframe[name='billing']").locator("[name='cardnumber']")"""
    field = e("a0", "input", name_attr="cardnumber", type="text")
    assert resolved(call, [field], None).hop is None


# ---- a hop selector is read in linear time ---------------------------------------------------------------------------------
def test_a_long_run_of_attribute_groups_is_not_read_exponentially():
    import time
    from voyageur.frames import hop_absent
    for selector in ('[id="a"]' * 200 + "X", '[id="a"]' * 24 + "X", "[id=" + "a" * 5000 + "]"):
        t = time.perf_counter()
        assert hop_absent(selector, [STRIPE], True) is False
        assert time.perf_counter() - t < 0.05
    assert hop_absent("""iframe[name="nope"][title='x']""", [STRIPE], True) is True


# ---- the frame selector --------------------------------------------------------------------------------------------------
def selectors(frame, siblings, tia="data-testid", language="python"):
    return [s.selector for s in frame_selectors(frame, siblings, tia, language)]


def test_a_frame_is_named_by_the_most_stable_attribute_that_is_unique_among_its_siblings():
    f = e("f", "iframe", id="pay", name_attr="payment", title="Secure payment", data_qa="pay-frame", testid="pay-tid")
    assert selectors(f, [f])[0] == "[data-testid='pay-tid']"
    assert selectors(f, [f], tia="data-qa")[0] == "[data-qa='pay-frame']"
    f2 = e("f", "iframe", id="pay", name_attr="payment", title="Secure payment")
    assert selectors(f2, [f2]) == ["#pay", "iframe[name='payment']", "iframe[title='Secure payment']"]


def test_a_name_that_ends_in_a_counter_or_a_random_suffix_comes_after_the_title():
    for name in ("layui-layer-iframe2", "AmazonPayFrame_k3j2h", "__privateStripeFrame9876"):
        assert unstable_name(name)
        f = e("f", "iframe", name_attr=name, title="Dialog")
        assert selectors(f, [f]) == ["iframe[title='Dialog']", f"iframe[name='{name}']"]
    for name in ("payment", "editor-canvas", "stripe_checkout_app"):
        assert not unstable_name(name)


def test_a_generated_id_is_never_a_frame_selector_and_a_shared_attribute_is_skipped():
    f = e("f", "iframe", id=":r4:", title="Dialog")
    twin = e("g", "iframe", title="Dialog")
    assert selectors(f, [f, twin]) == []
    assert selectors(f, [f]) == ["iframe[title='Dialog']"]


def test_the_typescript_selector_is_quoted_the_other_way():
    f = e("f", "iframe", title="Secure payment")
    assert selectors(f, [f], language="typescript") == ['iframe[title="Secure payment"]']


# ---- the chain renderer --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("py, ts", [
    ("""page.frame_locator("iframe").first.get_by_role("textbox", name="Card number", exact=True)""",
     """page.frameLocator('iframe').first().getByRole('textbox', { name: 'Card number', exact: true })"""),
    ("""page.locator("li").filter(has_text="Ann").nth(2)""", """page.locator('li').filter({ hasText: 'Ann' }).nth(2)"""),
    ("""page.get_by_text("x").and_(page.locator("b"))""", """page.getByText('x').and(page.locator('b'))"""),
])
def test_a_chain_is_rendered_in_the_other_language_and_parses_back_equal(py, ts):
    chain = try_parse(py, "python")
    assert render_chain(chain, "typescript", "page") == ts
    assert render_chain(try_parse(ts, "typescript"), "python", "page") == py
    assert render_chain(try_parse(ts, "typescript"), "typescript", "page") == ts


def test_a_regex_has_no_python_spelling():
    chain = try_parse("page.getByText(/ok/i)", "typescript")
    assert render_chain(chain, "python", "page") is None


# ---- writing -------------------------------------------------------------------------------------------------------------
CARD_ERROR = """waiting for locator("iframe[name^='ApxSecureIframe']").content_frame.get_by_role("textbox", name="Card number")"""
NESTED_ERROR = ("""waiting for locator("iframe").first.content_frame.locator("iframe[name=\\"layui-layer-iframe1\\"]")"""
                """.content_frame.locator("text=确认")""")
EDITOR_ERROR = """waiting for locator("iframe[name=\\"editor-canvas\\"]").content_frame.locator("button.primary")"""


def request(items, line, error, fingerprint=None):
    body = {"client": {"framework": "x", "language": "python", "version": "1"},
            "test": {"id": "t::t", "title": "t"},
            "failure": {"error": error, "stack": [{"file": "pages/p.py", "line": 3}]},
            "source": {"roots": ["pages"], "files": [{"path": "pages/p.py", "content":
                       f"class P:\n    def go(self):\n        {line}\n"}]},
            "page": {"url": "http://app.example/", "language": {"html_lang": "en"},
                     "inventory": [dict(x, ref=f"e{i}") for i, x in enumerate(items)]},
            "options": {"max_rounds": 3}, "run": {"id": "r", "repo": "app"}}
    if fingerprint:
        moved = ("class", "id", "name_attr")
        fp = {k: v for k, v in fingerprint.items() if k not in moved}
        fp["attrs"] = {("name" if k == "name_attr" else k): fingerprint[k] for k in moved if fingerprint.get(k)}
        body["last_pass"] = {"fingerprint": fp, "language": "en"}
    return HealRequest.model_validate(body)


def ok(ref):
    return ResultRequest.model_validate({"count": 1, "hit": {"ref": ref}, "test_outcome": "passed"})


def test_analyze_settles_the_renamed_frames_element_from_the_recording_without_a_model():
    line = """self.page.frame_locator("iframe[name^='ApxSecureIframe']").get_by_role("textbox", name="Card number").fill('4111')"""
    req = request(CARD_PAGE, line, CARD_ERROR, fingerprint=CARD_FP)
    an = analyze(req)
    assert an.hop is not None and an.scope.frame == "AmazonPayFrame_k3j2h"
    assert an.matched is not None and an.matched[0] == "e3"


def test_the_renamed_frame_heals_with_the_new_hop_and_the_steps_own_inner_locator():
    line = """self.page.frame_locator("iframe[name^='ApxSecureIframe']").get_by_role("textbox", name="Card number").fill('4111')"""
    model = FakeModel()
    sess = session(model)
    r = sess.start(request(CARD_PAGE, line, CARD_ERROR, fingerprint=CARD_FP))
    assert r.status == "try" and model.calls == [] and r.settled_at == 2
    assert r.candidate.target_ref == "e3"
    assert r.candidate.code == """self.page.frame_locator("iframe[title='Secure payment']").get_by_role("textbox", name="Card number")"""
    assert r.candidate.patch.from_ == """self.page.frame_locator("iframe[name^='ApxSecureIframe']").get_by_role("textbox", name="Card number")"""
    assert sess.result(r.heal_id, ok("e3")).status == "healed"


def test_a_failed_first_candidate_falls_back_to_a_locator_written_for_the_element_in_its_new_frame():
    line = """self.page.frame_locator("iframe[name^='ApxSecureIframe']").get_by_role("textbox", name="Card number").fill('4111')"""
    sess = session(FakeModel())
    r = sess.start(request(CARD_PAGE, line, CARD_ERROR, fingerprint=CARD_FP))
    r2 = sess.result(r.heal_id, ResultRequest.model_validate({"count": 0, "test_outcome": "failed"}))
    assert r2.status == "try" and r2.candidate.code == """self.page.frame_locator("iframe[title='Secure payment']").locator("#cn")"""


def test_the_nested_frame_heals_through_the_model_pick_inside_the_renamed_frame():
    line = """self.page.frame_locator("iframe").first.frame_locator('iframe[name="layui-layer-iframe1"]').locator("text=确认").click()"""
    model = FakeModel('{"ref": "e2", "why": "the confirm button"}')
    r = session(model).start(request([OUTER, INNER, CONFIRM], line, NESTED_ERROR, fingerprint=CONFIRM_FP))
    assert r.status == "try" and r.candidate.target_ref == "e2"
    assert r.candidate.code == ("""self.page.frame_locator("iframe").first.frame_locator("iframe[title='Confirm dialog']")"""
                                """.locator("text=确认")""")


def test_the_element_that_left_its_frame_heals_with_the_steps_own_selector_on_the_page():
    line = """self.page.frame_locator('iframe[name="editor-canvas"]').locator("button.primary").click()"""
    model = FakeModel('{"ref": "e4", "why": "the primary button"}')
    sess = session(model)
    r = sess.start(request([STRIPE, AMAZON, STRIPE_NUMBER, CARD, TITLE], line, EDITOR_ERROR, fingerprint=TITLE_FP))
    assert r.status == "try" and r.candidate.target_ref == "e4"
    assert r.candidate.code == """self.page.locator("button.primary")"""
    assert r.candidate.patch.from_ == """self.page.frame_locator('iframe[name="editor-canvas"]').locator("button.primary")"""
    assert sess.result(r.heal_id, ok("e4")).status == "healed"


def test_without_the_recording_the_moved_element_is_not_healed():
    line = """self.page.frame_locator('iframe[name="editor-canvas"]').locator("button.primary").click()"""
    model = FakeModel('{"ref": "e4", "why": "the primary button"}')
    r = session(model).start(request([STRIPE, AMAZON, STRIPE_NUMBER, CARD, TITLE], line, EDITOR_ERROR))
    assert r.status == "needs_human" and r.candidate is None


def test_a_frame_with_nothing_in_it_the_step_can_use_still_ends_target_missing_with_the_reason():
    line = """self.page.frame_locator('iframe[name="gone"]').locator("#zip").click()"""
    model = FakeModel('{"ref": "e0"}')
    error = """waiting for locator("iframe[name=\\"gone\\"]").content_frame.locator("#zip")"""
    r = session(model).start(request([STRIPE, AMAZON], line, error, fingerprint={"tag": "input", "attrs": {"id": "zip"}}))
    assert r.status == "needs_human" and r.reason == "target_missing" and model.calls == []
    assert "heal the frame locator first" in r.why


def test_write_for_offers_the_kept_inner_first_in_both_languages():
    out = resolved(CARD_CALL, CARD_PAGE, CARD_FP)
    res = write_for(None, "a1", "python", RECEIVER, "role", "data-testid", CARD_CALL, out.scope, out.hop)
    first = res.candidates[0]
    assert first.code["python"] == """page.frame_locator("iframe[title='Secure payment']").get_by_role("textbox", name="Card number")"""
    ts = first.code["typescript"]
    assert ts.startswith("page.frameLocator(") and ts.endswith(""".getByRole('textbox', { name: 'Card number' })""")
    assert try_parse(ts, "typescript") is not None
    assert first.neutral[0]["m"] == "frame_locator" and first.copy_based
    assert [c.code["python"] for c in res.candidates[1:]] == [
        """page.frame_locator("iframe[title='Secure payment']").locator("#cn")""",
        """page.frame_locator("iframe[name='AmazonPayFrame_k3j2h']").get_by_role("textbox", name="Card number")"""]


# ---- copy_more reaches the frame code ---------------------------------------------------------------------------------
def test_the_probes_copy_more_reaches_the_scope_of_a_step_in_a_frame_and_of_a_healed_hop():
    from voyageur.frames import scope_of
    page = [dict(x) for x in (STRIPE, STRIPE_NUMBER)]
    call = """page.frame_locator("iframe[name^='__privateStripe']").locator("[name='number']")"""
    assert scope_of(call, page, True).inventory.copy_more == 0
    assert scope_of(call, page, True, 4).inventory.copy_more == 4              # no copy_cut item inside this frame says so
    healed = heal_hop(CARD_CALL, [dict(i) for i in CARD_PAGE], True, CARD_FP, "fill", "python", "data-testid", 5)
    assert healed.hop is not None and healed.scope.inventory.copy_more == 5


def test_the_selector_proof_is_off_when_the_probe_left_copy_holding_elements_out():
    assert resolved(EDITOR_CALL, [STRIPE, TITLE], TITLE_FP, action="click").hop is not None
    cut = heal_hop(EDITOR_CALL, [dict(STRIPE), dict(TITLE)], True, TITLE_FP, "click", "python", "data-testid", 2)
    assert cut.hop is None
