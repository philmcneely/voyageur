"""Text filter: a stable locator two elements share, narrowed by a text the broken call itself named."""
import pytest

from voyageur.inventory import Inventory
from voyageur.locator_steps import try_parse
from voyageur.text_filter import call_texts, text_filtered

pytestmark = pytest.mark.unit

BOX = {"x": 0, "y": 0, "width": 9, "height": 9}
MINE = {"ref": "e1", "tag": "div", "testid": "subscription-row", "class": "flex", "text": "My subscription Delete", "pw_text": "My subscriptionDelete", "box": BOX}
OTHER = {"ref": "e2", "tag": "div", "testid": "subscription-row", "class": "flex", "text": "Other Delete", "pw_text": "OtherDelete", "box": BOX}
CALL = 'page.get_by_text("My subscription", exact=True).locator("xpath=ancestor::div[contains(@class, \'group/row\')]")'


def run(target, *others, call=CALL, language="python", receiver="page", complete=True):
    inv = Inventory([dict(target), *[dict(o) for o in others]], complete, "")
    return text_filtered(inv.elements[0], inv, language, receiver, "chain", "data-testid", call)


def test_a_shared_test_id_is_narrowed_by_the_text_the_broken_call_named():
    out = run(MINE, OTHER)
    assert out is not None
    c = out.candidates[0]
    assert c.code["python"] == 'page.get_by_test_id("subscription-row").filter(has_text="My subscription")'
    assert c.code["typescript"] == "page.getByTestId('subscription-row').filter({ hasText: 'My subscription' })"
    assert c.copy_based and c.kind == "text"
    assert try_parse(c.code["python"], "python") and try_parse(c.code["typescript"], "typescript")


def test_a_typescript_client_gets_typescript_first_on_its_own_receiver():
    call = "this.page.getByText('My subscription', { exact: true }).locator('xpath=ancestor::div[1]')"
    out = run(MINE, OTHER, call=call, language="typescript", receiver="this.page")
    assert out.candidates[0].code["typescript"] == \
        "this.page.getByTestId('subscription-row').filter({ hasText: 'My subscription' })"
    assert out.candidates[0].code["python"] == 'self.page.get_by_test_id("subscription-row").filter(has_text="My subscription")'


def test_a_data_attribute_base_and_a_text_inside_a_selector():
    call = "page.locator(\"#tape tr:has(td.c-tool:text-is('Edit')):has-text('billing.py')\")"
    mine = {"ref": "e1", "tag": "tr", "data_tool": "edit", "text": "Modify billing.py", "pw_text": "Modifybilling.py", "box": BOX}
    edit2 = {"ref": "e2", "tag": "tr", "data_tool": "edit", "text": "Modify app.py", "pw_text": "Modifyapp.py", "box": BOX}
    read = {"ref": "e3", "tag": "tr", "data_tool": "read", "text": "Read billing.py", "pw_text": "Readbilling.py", "box": BOX}
    out = run(mine, edit2, read, call=call)
    assert [c.code["python"] for c in out.candidates] == ['page.locator("tr[data-tool=\'edit\']").filter(has_text="billing.py")']


def test_no_candidate_when_the_text_is_in_both_elements():
    assert run(MINE, dict(OTHER, pw_text="My subscription (old)Delete")) is None


def test_no_candidate_when_another_element_text_is_incomplete_or_missing():
    assert run(MINE, dict(OTHER, pw_cut=True)) is None
    other = dict(OTHER)
    del other["pw_text"]
    assert run(MINE, other) is None                       # an older probe: no pw_text, nothing can be ruled out


def test_no_candidate_when_the_listing_is_not_complete():
    assert run(MINE, OTHER, complete=False) is None


def test_no_candidate_when_the_target_does_not_hold_the_text():
    assert run(dict(MINE, pw_text="Your planDelete"), OTHER) is None


def test_no_candidate_without_a_shared_stable_locator():
    assert run(dict(MINE, testid="", **{"class": ""}), dict(OTHER, testid="", **{"class": ""})) is None


def test_a_hidden_twin_still_counts():
    assert run(MINE, dict(OTHER, hidden=True, pw_text="My subscription")) is None


@pytest.mark.parametrize("call, want", [
    ('page.locator("div", has_text="E2E Sync Store").filter(has=page.locator("text=Local Disk")).first',
     ["E2E Sync Store", "Local Disk"]),
    ('page.locator(".agent-option").filter(has=page.locator(".agent-option-name", has_text=re.compile(r"^Alpha$")))', []),
    ("page.locator('[data-qa=\"order\"] > li:nth-child(2)')", []),
    ('page.get_by_role("button", name="Save")', ["Save"]),
    ('page.locator("css=div >> text=\'Hello there\'")', ["Hello there"]),
])
def test_call_texts_are_the_copy_the_call_names_not_its_selectors_or_regexes(call, want):
    assert call_texts(call) == want


def test_a_role_word_from_the_call_does_not_narrow():
    call = 'page.get_by_role("button", name="Delete").locator("xpath=..")'
    assert run(MINE, OTHER, call=call) is None          # "Delete" is in both rows


# ---- in the write stage: after the writer's own, never ahead of a stable one ---------------------------------------
def _stage(items, call):
    from voyageur.stages import write_for
    inv = Inventory([dict(i) for i in items], True, "")
    return [c.code["python"] for c in write_for(inv, "e1", "python", "page", "chain", "data-testid", call).candidates]


def test_the_stage_offers_the_narrowed_locator_after_a_copy_one_that_may_miss_live():
    call = "page.locator(\"#tape tr:has(td.c-tool:text-is('Edit')):has-text('billing.py')\")"
    rows = [{"ref": "e1", "tag": "tr", "data_tool": "edit", "text": "Modify billing.py", "pw_text": "Modifybilling.py", "box": BOX},
            {"ref": "e2", "tag": "tr", "data_tool": "edit", "text": "Modify app.py", "pw_text": "Modifyapp.py", "box": BOX}]
    assert _stage(rows, call) == ['page.get_by_text("Modify billing.py", exact=True)',
                                  'page.locator("tr[data-tool=\'edit\']").filter(has_text="billing.py")']


def test_the_stage_does_not_add_a_narrowed_locator_when_a_stable_one_is_unique():
    call = "page.locator(\"#tape tr:has-text('billing.py')\")"
    rows = [{"ref": "e1", "tag": "tr", "data_tool": "edit", "data_file": "billing", "text": "Modify billing.py", "pw_text": "Modifybilling.py", "box": BOX},
            {"ref": "e2", "tag": "tr", "data_tool": "edit", "data_file": "app", "text": "Modify app.py", "pw_text": "Modifyapp.py", "box": BOX}]
    assert not any(".filter(" in c for c in _stage(rows, call))


def test_the_proof_reads_pw_text_not_the_spaced_visible_text():
    # the review's case: the listed `text` joins blocks with a space and drops hidden text, Playwright does neither
    mine = dict(MINE, text="My subscription", pw_text="Mysubscription")
    other = dict(OTHER, text="Other", pw_text="OtherMy subscription")       # a display:none duplicate
    assert run(mine, other) is None


def test_a_base_is_paired_with_its_hook_by_position_not_by_text_in_the_code():
    mine = dict(MINE, data_qa="get_by_test_id")
    twin = {"ref": "e3", "tag": "div", "data_qa": "get_by_test_id", "pw_text": "My subscription", "box": BOX}
    out = run(mine, OTHER, twin)
    assert [c.code["python"] for c in out.candidates] == \
        ['page.get_by_test_id("subscription-row").filter(has_text="My subscription")']


def test_a_text_inside_not_is_not_used_and_neither_are_label_or_placeholder_arguments():
    assert call_texts("page.locator(\"tr:not(:has-text('Admin')):has-text('Jane')\")") == ["Jane"]
    assert call_texts('page.get_by_label("Email").locator("..")') == []


def test_a_text_holding_whitespace_python_and_javascript_disagree_on_is_not_used():
    assert call_texts('page.get_by_text("My\\x1csubscription")') == []
    assert call_texts('page.get_by_text("My\\u200bsubscription")') == []


def test_a_not_is_taken_out_however_deep_and_whatever_it_quotes():
    assert call_texts("page.locator(\"tr:not(a:has(b:has-text('x(y)'))):has-text('Jane')\")") == ["Jane"]
    assert call_texts("page.locator(\"tr:not(:has-text(')Admin')):has-text('Jane')\")") == ["Jane"]


def test_a_text_holding_a_control_character_is_not_used():
    assert call_texts('page.get_by_text("a\\x01b")') == []


def test_a_not_in_any_case_or_closed_by_an_escaped_paren_still_hides_its_text():
    assert call_texts("page.locator(\"div:Not(:has-text('Foo')):has-text('Jane')\")") == ["Jane"]
    assert call_texts("page.locator(\"div:not(.a\\\\):has-text('Foo')):has-text('Jane')\")") == ["Jane"]
