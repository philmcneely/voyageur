"""Unit: copy the probe cut at its limit (80 UTF-16 units; a placeholder 200) is the head of longer copy, so it cannot prove an exact
text, name or label match. Without the aria snapshot the count, the matched elements, the written locator and the overlay legend all
say "cannot tell" (and the writer names `copy_maybe_cut`); the snapshot, which holds the whole copy, still counts it; a probe that says
`text_cut` / `name_cut` is believed either way, and a client that sends neither is judged by length."""
import pytest

from voyageur.audit import _hits
from voyageur.inventory import Inventory, possibly_cut
from voyageur.locator_policy import PROOF_GAPS, write
from voyageur.marks import _resolve
from voyageur.schemas import InventoryItem

pytestmark = pytest.mark.unit

BOX = {"x": 10, "y": 10, "width": 200, "height": 20}
FULL = "A very long paragraph heading that the page author wrote with more than eighty characters in it for sure yes"
HEAD = FULL[:80]
EMOJI_HEAD = "\U0001F600" * 40                     # 40 code points, 80 UTF-16 units: the length the probe cuts at
assert len(HEAD) == 80 and len(FULL) > 80
GET = 'page.get_by_text("{}", exact=True)'


def el(tag="p", **kw):
    return {"tag": tag, "box": dict(BOX), **kw}


def inv(*items, aria="", counts=True):
    return Inventory(list(items), True, aria, 0, 0 if counts else None, 0 if counts else None)


def snapshot(text):
    return f'- heading "{text}" [level=2]\n- paragraph: {text}\n'


def test_an_exact_text_as_long_as_the_cut_is_not_counted_or_matched_from_the_listing_alone():
    page = inv(el(text=HEAD, name=HEAD), el(text="x", name="x"))
    for call in (GET.format(HEAD), GET.format(HEAD).replace("get_by_text", "getByText")):
        assert page.count(call) is None and page.matched(call) is None, call
    assert page.count(GET.format("x")) == 1                           # copy that is not at the cut is read as before
    assert page.count(GET.format("A very long")) == 0                 # not the head, not the whole: nothing holds it


def test_a_substring_text_is_still_counted_from_the_head_it_is_in():
    page = inv(el(text=HEAD, name=HEAD))
    assert page.count('page.get_by_text("A very long paragraph")') == 1 and len(page.matched('page.get_by_text("A very long")')) == 1


def test_the_writer_refuses_the_head_with_a_reason_that_names_no_copy():
    page = inv(el(text=HEAD, name=HEAD), el(tag="p", text="x", name="x"))
    out = write(page.elements[0], page, "python", "page", "text")
    assert out.candidates == [] and out.gap == "copy_maybe_cut"
    assert out.why.endswith(PROOF_GAPS["copy_maybe_cut"]) and "A very long" not in out.why
    assert "page.get_by_text" not in out.why


@pytest.mark.parametrize("tag", ["p", "h2", "div"])
def test_the_snapshot_that_holds_the_whole_copy_refuses_the_head_as_not_matched(tag):
    page = inv(el(tag, text=HEAD, name=HEAD), el(text="x", name="x"), aria=snapshot(FULL))
    assert page.count(GET.format(HEAD)) == 0
    out = write(page.elements[0], page, "python", "page", "text")
    assert out.candidates == [] and out.gap == "not_matched"


def test_the_snapshot_that_holds_exactly_the_cut_length_text_proves_it():
    page = inv(el(text=HEAD, name=HEAD), el(text="x", name="x"), aria=f"- paragraph: {HEAD}\n- paragraph: x\n")
    assert page.count(GET.format(HEAD)) == 1
    out = write(page.elements[0], page, "python", "page", "text")
    assert [c.code["python"] for c in out.candidates if c.kind == "text"] == [GET.format(HEAD)]


def test_a_copy_only_item_is_never_cut_because_the_probe_withholds_longer_copy():
    page = inv(el(text=HEAD, name=HEAD, copy_only=True))
    assert page.count(GET.format(HEAD)) == 1 and len(page.matched(GET.format(HEAD))) == 1


def test_the_probes_own_flag_is_believed_when_it_is_there():
    whole = inv(el(text=HEAD, name=HEAD, text_cut=False, name_cut=False))
    assert whole.count(GET.format(HEAD)) == 1                         # the probe says exactly this long, not cut
    short = inv(el(text="Short head", name="Short head", text_cut=True, name_cut=True))
    assert short.count(GET.format("Short head")) is None              # the probe says the copy goes on
    name_only = inv(el(text="x", name=HEAD, name_cut=True))
    assert name_only.matched(GET.format(HEAD)) is None


def test_length_is_measured_in_utf16_units_as_the_probe_does():
    page = inv(el(text=EMOJI_HEAD, name=EMOJI_HEAD), el(text="x"))
    assert page.count(GET.format(EMOJI_HEAD)) is None
    under = "\U0001F600" * 39                                           # 78 units: below the cut, so whole
    assert inv(el(text=under, name="x"), el(text="y")).count(GET.format(under)) == 1


def test_a_value_ending_in_half_a_surrogate_pair_is_judged_by_length_too():
    assert possibly_cut({"text": "a" * 79 + "\ud83d"}, "text") is True          # the probe's slice can end in half a pair
    assert possibly_cut({"text": "a" * 78 + "\ud83d"}, "text") is False


def test_a_role_name_that_may_be_cut_is_not_matched_from_the_listing():
    call = f'page.get_by_role("heading", name="{HEAD}", exact=True)'
    page = inv(el("h2", text=HEAD, name=HEAD), el("p", text="x", name="x"))
    assert page.matched(call) is None
    with_snapshot = inv(el("h2", text=HEAD, name=HEAD), el("p", text="x", name="x"), aria=snapshot(FULL))
    assert with_snapshot.count(call) == 0                                       # the snapshot has the whole name


def twin_heads(flagged):
    """A cut heading (its copy is FULL) and another element whose whole copy is exactly HEAD, on a page whose snapshot says so."""
    cut = {"text_cut": True, "name_cut": True} if flagged else {}
    whole = {"text_cut": False, "name_cut": False} if flagged else {}
    return inv(el("h2", text=HEAD, name=HEAD, **cut), el("h2", text=HEAD, name=HEAD, **whole),
               aria=f'- heading "{FULL}" [level=2]\n- heading "{HEAD}" [level=2]\n')


@pytest.mark.parametrize("call", [GET.format(HEAD), f'page.get_by_role("heading", name="{HEAD}", level=2, exact=True)'])
def test_the_snapshot_count_never_names_the_cut_heading_as_the_element_with_exactly_the_head(call):
    flagged = twin_heads(True)
    assert flagged.count(call) == 1 and flagged.matched(call) == [flagged.elements[1]]
    assert write(flagged.elements[0], flagged, "python", "page", "text").candidates == []      # the cut one is not the exact head
    assert [c.kind for c in write(flagged.elements[1], flagged, "python", "page", "text").candidates] == ["role", "text"]
    older = twin_heads(False)                         # no flags: either may be the whole one, so neither is named
    assert older.count(call) == 1 and len(older.matched(call)) == 2
    for i in (0, 1):
        assert write(older.elements[i], older, "python", "page", "text").candidates == []


@pytest.mark.parametrize("call", [
    'page.locator("a[title=\'{}\']")', 'page.locator("input[placeholder=\'{}\']")', 'page.locator("a[title$=\'{}\']")',
    'page.locator("button[aria-label=\'{}\']")'])
def test_a_css_attribute_equal_to_a_cut_head_is_unknown_not_a_match(call):
    head = "w" * 200 if "placeholder" in call else HEAD
    key = "placeholder" if "placeholder" in call else "title" if "title" in call else "name"
    tag = "input" if key == "placeholder" else "button" if key == "name" else "a"
    page = inv(el(tag, **{key: head}), el("a", id="other"))
    assert page.count(call.format(head)) is None and page.matched(call.format(head)) is None
    assert inv(el(tag, **{key: head[:60]}), el("a", id="other")).count(call.format(head[:60])) == 1


def test_a_css_prefix_or_substring_of_a_cut_head_is_still_read():
    page = inv(el("a", title=HEAD), el("a", id="other"))
    assert page.count('page.locator("a[title^=\'A very long\']")') == 1 and page.count('page.locator("a[title*=\'paragraph\']")') == 1
    assert page.count(f'page.locator("a[title^=\'{HEAD}x\']")') is None            # longer than the head: it may match


def test_an_iframe_title_as_long_as_the_probes_cut_is_not_written_as_a_selector():
    from voyageur.frame_heal import frame_selectors
    one, other = el("iframe", title=HEAD, ref="e0"), el("iframe", title="Other", ref="e1")
    assert [s.selector for s in frame_selectors(one, [one, other], "data-testid", "python")] == []
    assert [s.selector for s in frame_selectors(other, [one, other], "data-testid", "python")] == ["iframe[title='Other']"]


def test_a_copy_only_name_that_is_its_text_is_whole_and_an_older_probes_trimmed_name_is_judged_by_its_text():
    leaf = inv(el("div", text=HEAD, name=HEAD, copy_only=True))
    assert possibly_cut(leaf.elements[0], "name") is False and leaf.matched(GET.format(HEAD)) == [leaf.elements[0]]
    spaced = "word " * 16                                                     # 80 units: a cut that ends on a space
    assert possibly_cut({"text": spaced, "name": spaced.strip()}, "name") is True
    assert possibly_cut({"text": "short", "name": "short"}, "name") is False


def test_a_data_value_as_long_as_the_probes_cut_is_possibly_cut_but_a_hook_spelling_is_not():
    assert possibly_cut({"data_menu": "m" * 80}, "data_menu") is True and possibly_cut({"data_menu": "m" * 79}, "data_menu") is False
    assert possibly_cut({"data_qa": "m" * 80}, "data_qa") is False


def test_a_control_labelled_role_cannot_use_the_snapshot_and_its_head_is_unknown():
    box = el("div", role="checkbox", text=HEAD, name=HEAD)
    page = inv(box, aria=f'- checkbox "{FULL}"')
    assert page.count(GET.format(HEAD)) is None


@pytest.mark.parametrize("key,call,cap", [
    ("placeholder", 'page.get_by_placeholder("{}", exact=True)', 200),
    ("alt", 'page.get_by_alt_text("{}", exact=True)', 80),
    ("title", 'page.get_by_title("{}", exact=True)', 80),
    ("label", 'page.get_by_label("{}", exact=True)', 80),
    ("aria_label", 'page.get_by_label("{}", exact=True)', 80),
])
def test_the_other_fields_the_probe_cuts_are_held_to_their_own_limit(key, call, cap):
    head, below = "w" * cap, "w" * (cap - 1)
    page = inv(el("input", **{key: head}), el("input", id="other"))
    assert page.count(call.format(head)) is None and page.matched(call.format(head)) is None
    assert inv(el("input", **{key: below}), el("input", id="other")).count(call.format(below)) == 1


def test_the_placeholder_limit_is_two_hundred_not_eighty():
    long80 = "p" * 80
    assert inv(el("input", placeholder=long80)).count(f'page.get_by_placeholder("{long80}", exact=True)') == 1
    long200 = "p" * 200
    assert inv(el("input", placeholder=long200)).count(f'page.get_by_placeholder("{long200}", exact=True)') is None


def test_the_audit_and_the_overlay_legend_do_not_call_the_head_a_match():
    page = inv(el(text=HEAD, name=HEAD, ref="e0"), el(text="x", name="x", ref="e1"))
    assert _hits(page, GET.format(HEAD)) is None
    entry, marked = _resolve(page, 1, GET.format(HEAD))
    assert entry["status"] == "unknown" and entry["count"] is None and marked == []
    entry, _ = _resolve(page, 2, GET.format("x"))
    assert entry["status"] == "matched"


def test_the_flags_are_part_of_the_inventory_schema_and_only_booleans():
    item = InventoryItem.model_validate({"tag": "p", "text": HEAD, "text_cut": False, "name_cut": True})
    flat = item.flat(0)
    assert flat["text_cut"] is False and flat["name_cut"] is True
    assert "text_cut" not in InventoryItem.model_validate({"tag": "p", "text": "x"}).flat(0)          # none sent: none said
    for bad in ("yes", 1, "true"):
        for flag in ("text_cut", "name_cut"):
            with pytest.raises(Exception):
                InventoryItem.model_validate({"tag": "p", flag: bad})
