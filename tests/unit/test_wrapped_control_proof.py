"""
===============================================================================
Wrapped control proof (the step acted on a bare layout wrapper; the control inside it is the element now)
===============================================================================
`...form > div:nth-child(4) > div.pf-c-form__group-control > div` ends on an attribute-less wrapper around a search input; after a
rebuild the model picks the input. The recorded element is a div with no copy, hook or role of its own, so it has no identity
or copy to compare and its tag differs from the input's: neither identity, copy nor place (which needs a compatible kind)
could prove the pick, although the input fills the wrapper's box and nothing else is listed in it. The same holds for a
custom element (a vendor toggle's internal component) that wraps the one input of the page that is in its box.

The proof is narrow: a bare wrapper (a container tag or a custom element, no copy, no hook, no role), a control that sits
inside the wrapper's box, and no other listed element inside that box.

Author: PMAC
===============================================================================
"""
import pytest

from voyageur.gates import verify_pick
from voyageur.proof import prove

pytestmark = pytest.mark.unit


def box(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


CALL = 'page.locator("#app-layout-page > section > div:nth-child(4) > div.pf-c-form__group-control > div")'
WRAPPER = {"tag": "div", "row": "app-layout-page", "box": box(41, 194.5, 1198, 29.6)}
SEARCH = {"ref": "e4", "tag": "input", "type": "search", "placeholder": "Search APIs", "name_attr": "search",
          "row": "app-layout-page", "row_attr": "id", "top_at_center": True, "box": box(41, 194.5, 164, 29.6)}
PAGE = {"ref": "e3", "tag": "div", "id": "app-layout-page", "top_at_center": True, "box": box(23, 180.5, 1234, 65.6)}
OTHER = {"ref": "e6", "tag": "input", "type": "text", "name_attr": "q", "top_at_center": True, "box": box(23, 400, 164, 30)}


def check(el, items, fp, call=CALL, action="click", complete=True):
    return verify_pick(el, {i["ref"] for i in items}, items, fp, action, (), (), call, False, complete=complete)


def test_the_lone_control_inside_a_bare_wrapper_is_the_element():
    items = [PAGE, SEARCH, OTHER]
    assert check(SEARCH, items, WRAPPER) is None
    proof = prove(SEARCH, WRAPPER, CALL, items, complete=True)[0]
    assert proof is not None and proof[0] == "within"


def test_a_custom_element_wrapper_and_the_control_in_its_shadow_root():
    host = {"tag": "lightning-primitive-input-toggle", "box": box(23, 186.5, 1234, 19.6)}
    toggle = {"ref": "e8", "tag": "input", "type": "checkbox", "role": "switch", "label": "On",
              "shadow": "lightning-input >> lightning-input-toggle", "top_at_center": True, "box": box(27, 189.9, 1200.3, 13)}
    search = {"ref": "e6", "tag": "input", "type": "search", "placeholder": "Search", "shadow": "ion-searchbar",
              "top_at_center": True, "box": box(23, 55.8, 1234, 33.6)}
    call = 'page.locator("lightning-input.pipelineInspectionToggle lightning-primitive-input-toggle")'
    assert check(toggle, [search, toggle], host, call) is None


# The probe lists an element that only holds copy (copy_only). The label text of the control in the wrapper is one: it says what
# the control's own `label` says, in the same shadow root, and it is no second element the step could have meant.
HOST = {"tag": "lightning-primitive-input-toggle", "box": box(23, 186.5, 1234, 19.6)}
TOGGLE = {"ref": "e8", "tag": "input", "type": "checkbox", "role": "switch", "label": "On",
          "shadow": "lightning-input >> lightning-input-toggle", "top_at_center": True, "box": box(27, 189.9, 1200.3, 13)}
TOGGLE_CALL = 'page.locator("lightning-input.pipelineInspectionToggle lightning-primitive-input-toggle")'
ON_TEXT = {"ref": "e9", "tag": "span", "name": "On", "text": "On", "copy_only": True, "top_at_center": True,
           "shadow": "lightning-input >> lightning-input-toggle", "box": box(1238.3, 186.6, 18.7, 19.6)}


def test_the_control_stays_proven_when_its_own_label_text_is_listed_in_the_wrapper():
    search = {"ref": "e6", "tag": "input", "type": "search", "placeholder": "Search", "shadow": "ion-searchbar",
              "top_at_center": True, "box": box(23, 55.8, 1234, 33.6)}
    items = [search, TOGGLE, ON_TEXT]
    assert check(TOGGLE, items, HOST, TOGGLE_CALL) is None
    assert prove(TOGGLE, HOST, TOGGLE_CALL, items, complete=True)[0][0] == "within"


def test_other_copy_in_the_wrapper_still_proves_nothing():
    other = dict(ON_TEXT, name="Off", text="Off")                                         # not the control's own label
    assert check(TOGGLE, [TOGGLE, other], HOST, TOGGLE_CALL) == "no_identity_proof"
    elsewhere = dict(ON_TEXT, shadow="")                                                   # the same words, but not in its shadow root
    assert check(TOGGLE, [TOGGLE, elsewhere], HOST, TOGGLE_CALL) == "no_identity_proof"
    framed = dict(ON_TEXT, frame="iframe[name=x]")
    assert check(TOGGLE, [TOGGLE, framed], HOST, TOGGLE_CALL) == "no_identity_proof"


def test_a_listed_control_with_the_same_label_text_in_the_wrapper_still_proves_nothing():
    twin = dict(ON_TEXT, copy_only=False, tag="button")                                    # a control, not label text
    assert check(TOGGLE, [TOGGLE, twin], HOST, TOGGLE_CALL) == "no_identity_proof"


def test_two_listed_elements_in_the_wrapper_prove_nothing():
    second = dict(OTHER, ref="e7", box=box(300, 195, 100, 29))
    items = [PAGE, SEARCH, second]
    assert check(SEARCH, items, WRAPPER) == "no_identity_proof"
    assert check(second, items, WRAPPER) == "no_identity_proof"


def test_a_wrapper_with_copy_is_not_bare():
    labelled = dict(WRAPPER, text="Search")
    assert check(SEARCH, [PAGE, SEARCH], labelled) == "no_identity_proof"


def test_a_wrapper_with_a_hook_or_role_is_not_bare():
    assert check(SEARCH, [PAGE, SEARCH], dict(WRAPPER, testid="search-group")) == "no_identity_proof"
    assert check(SEARCH, [PAGE, SEARCH], dict(WRAPPER, role="group")) == "no_identity_proof"
    assert check(SEARCH, [PAGE, SEARCH], dict(WRAPPER, id="search-wrap")) == "no_identity_proof"


def test_a_recorded_control_is_not_a_wrapper():
    button = dict(WRAPPER, tag="button")
    assert check(SEARCH, [PAGE, SEARCH], button) == "no_identity_proof"


def test_a_control_not_inside_the_wrapper_is_not_proven():
    far = dict(SEARCH, box=box(41, 300, 164, 30))
    assert check(far, [PAGE, far], WRAPPER) == "no_identity_proof"
    wide = dict(SEARCH, box=box(41, 194.5, 1300, 29.6))
    assert check(wide, [PAGE, wide], WRAPPER) == "no_identity_proof"


def test_a_control_covered_by_another_element_is_not_proven():
    covered = dict(SEARCH, top_at_center=False)
    assert check(covered, [PAGE, covered], WRAPPER) == "no_identity_proof"


def test_a_pick_that_is_not_a_form_control_or_widget_is_not_proven():
    text = {"ref": "e9", "tag": "span", "top_at_center": True, "box": box(41, 194.5, 80, 29.6)}
    assert check(text, [PAGE, text], WRAPPER) == "no_identity_proof"
    link = {"ref": "e9", "tag": "a", "top_at_center": True, "box": box(41, 194.5, 80, 29.6)}
    assert check(link, [PAGE, link], WRAPPER) == "no_identity_proof"
    note = {"ref": "e9", "tag": "span", "role": "note", "top_at_center": True, "box": box(41, 194.5, 80, 29.6)}
    assert check(note, [PAGE, note], WRAPPER) == "no_identity_proof"
    widget = {"ref": "e9", "tag": "div", "role": "combobox", "top_at_center": True, "box": box(41, 194.5, 80, 29.6)}
    assert check(widget, [PAGE, widget], WRAPPER) is None


def test_the_probe_must_say_the_control_is_on_top():
    unsaid = {k: v for k, v in SEARCH.items() if k != "top_at_center"}
    assert check(unsaid, [PAGE, unsaid], WRAPPER) == "no_identity_proof"


def test_an_incomplete_listing_proves_nothing():
    assert check(SEARCH, [PAGE, SEARCH], WRAPPER, complete=False) == "no_identity_proof"


def test_a_wrapper_without_a_box_proves_nothing():
    assert check(SEARCH, [PAGE, SEARCH], {k: v for k, v in WRAPPER.items() if k != "box"}) == "no_identity_proof"


# ---- the size relation: a big container with one widget left is not the widget's wrapper ----------------------------------
# The wrapper must be hugged by the widget: it fills at least 80% of the wrapper along one axis (a row the widget is as tall as,
# a column it is as wide as) and at least 10% of its area. s4's row is 1198 x 29.6 around a 164 x 29.6 input (full height, 13.7% of
# the area); w79's host is 1234 x 19.6 around a 1200 x 13 toggle. A form, section or list that merely contains the widget is neither.
def test_the_recorded_geometries_that_must_keep_healing_pass_the_size_relation():
    assert check(SEARCH, [PAGE, SEARCH, OTHER], WRAPPER) is None                                    # s4: 164 x 29.6 in 1198 x 29.6


def test_a_large_container_with_one_widget_left_in_it_is_not_proven():
    section = dict(WRAPPER, tag="section", box=box(41, 100, 800, 600))
    lone = dict(SEARCH, box=box(41, 100, 164, 29.6))
    assert check(lone, [PAGE, lone], section) == "no_identity_proof"                                 # 0.8% of the area, 20% of the width


def test_a_tall_narrow_wrapper_whose_widget_fills_its_width_but_a_sliver_of_its_area_is_not_proven():
    form = dict(WRAPPER, tag="form", box=box(41, 100, 250, 800))
    lone = dict(SEARCH, box=box(41, 100, 240, 30))
    assert check(lone, [PAGE, lone], form) == "no_identity_proof"                                    # 96% of the width, 3.6% of the area


def test_a_row_whose_remaining_widget_is_a_small_part_of_it_is_not_proven():
    row = dict(WRAPPER, tag="ul", box=box(41, 100, 1200, 30))
    lone = {"ref": "e9", "tag": "button", "top_at_center": True, "box": box(41, 100, 80, 30)}        # full height, 6.7% of the area
    assert check(lone, [PAGE, lone], row) == "no_identity_proof"
    fills = dict(lone, box=box(41, 100, 130, 30))                                                   # 10.8%
    assert check(fills, [PAGE, fills], row) is None


# ---- a listing with copy-holding elements left out cannot say "the only one" ---------------------------------------------
def test_the_gate_of_a_heal_counts_a_cut_copy_listing_as_incomplete():
    from voyageur.schemas import ResultRequest  # noqa: F401  (the request helper below builds a HealRequest)
    from tests.unit.scenarios import FakeModel, session
    from tests.unit.test_identity_proof import request as ip_request
    inv = [dict(PAGE, ref="e0"), dict(SEARCH, ref="e1"), dict(OTHER, ref="e2")]
    line = "self.page.locator('#app-layout-page > section > div:nth-child(4) > div.pf-c-form__group-control > div').click()"
    err = 'waiting for locator("#app-layout-page > section > div:nth-child(4) > div.pf-c-form__group-control > div")'
    last = {"fingerprint": {"tag": "div", "row": "app-layout-page", "box": WRAPPER["box"]}, "language": "en"}

    def heal(copy_more):
        req = ip_request(inv, line, err, last_pass=last)
        req.page.inventory_copy_more = copy_more
        return session(FakeModel('{"ref": "e1"}')).start(req)
    assert heal(0).status == "try"
    assert heal(3).status == "needs_human" and heal(3).reason == "no_identity_proof"
