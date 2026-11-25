"""POST /v1/overlay: locators marked on a client screenshot with no heal and no model call. Label modes (numbers with a JSON legend,
locator text from the built-in ASCII bitmap), the legend statuses, determinism, the limits, the auth and the privacy rules."""
import base64
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from voyageur import overlay as ov
from voyageur.app import create_app
from voyageur.config import ServiceSettings
from voyageur.core import HealSession
from voyageur.deadline import DeadlineExceeded, ProcessRunner, WorkerFailed
from voyageur.llm import Endpoint
from voyageur.marks import run_overlay
from voyageur.overlay import MAX_LABEL_CHARS, Mark, OverlayError, clean_label, render_marks
from voyageur.schemas import LimitExceeded, OverlayResponse, RefusedRequest, ValidationError, parse_overlay_request
from voyageur.store import Store
from tests.unit.scenarios import ENDPOINT, JSON, BUTTON, PASSWORD, USER, FakeModel, session

pytestmark = pytest.mark.unit

TOKEN = "t0ken-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
RED, BLUE, GREEN = ov._PALETTE[0], ov._PALETTE[1], ov._PALETTE[2]
WHITE = (255, 255, 255)
GRAY = (120, 120, 120)


def png_bytes(w=300, h=200, color=WHITE):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def shot(w=300, h=200, color=WHITE, raw=None):
    data = raw if raw is not None else png_bytes(w, h, color)
    return {"media_type": "image/png", "data": base64.b64encode(data).decode(), "width": w, "height": h}


def body(locators=None, labels=None, inventory=None, w=300, h=200, color=WHITE, **over):
    out = {"client": {"framework": "x", "language": "python"},
           "page": {"url": "http://app.example/login", "inventory": [dict(e) for e in (inventory or [USER, PASSWORD, BUTTON])],
                    "viewport": {"w": w, "h": h, "dpr": 1}, "screenshot": shot(w, h, color)}}
    if locators is not None:
        out["locators"] = locators
    if labels is not None:
        out["options"] = {"labels": labels}
    out.update(over)
    return out


def run(**kw):
    return run_overlay(parse_overlay_request(body(**kw)))


def image_of(out):
    return Image.open(io.BytesIO(base64.b64decode(out["image"]["data"]))).convert("RGB")


def legend_of(out):
    return {e["number"]: e for e in out["legend"]}


def client(token=TOKEN, model=None, **kw):
    core = session(model if model is not None else FakeModel(), **kw)
    return TestClient(create_app(core, token), base_url="http://127.0.0.1", headers=JSON), core


def post(c, payload, headers=AUTH):
    return c.post("/v1/overlay", content=json.dumps(payload), headers={**headers, "Content-Type": "application/json"})


# ---- the request ------------------------------------------------------------------------------------------------------
def test_a_request_with_locators_and_a_screenshot_parses_and_defaults_to_numbers():
    req = parse_overlay_request(body(["page.locator('#user')"]))
    assert req.locators == ["page.locator('#user')"] and req.options.labels == "numbers" and req.page.screenshot.png()[:4] == b"\x89PNG"
    assert parse_overlay_request(body()).locators is None


def test_the_screenshot_is_required_and_no_vision_option_exists():
    no_shot = body()
    no_shot["page"]["screenshot"] = None
    with pytest.raises(ValidationError, match="screenshot"):
        parse_overlay_request(no_shot)
    with pytest.raises(ValidationError):
        parse_overlay_request(body(options={"labels": "numbers", "vision": "on"}))
    with pytest.raises(ValidationError):
        parse_overlay_request(body(labels="words"))


def test_locators_are_strings_and_a_list_is_never_empty():
    for bad in ([], [""], ["  "], [1], [None], "page.locator('#a')", [["x"]], ["x" * 4001]):
        with pytest.raises(ValidationError):
            parse_overlay_request(body(locators=bad))


def test_more_than_100_locators_is_a_413_and_100_is_fine():
    assert parse_overlay_request(body(["page.locator('#a')"] * 100))
    with pytest.raises(LimitExceeded, match="100"):
        parse_overlay_request(body(["page.locator('#a')"] * 101))


def test_values_cookies_and_storage_are_refused_without_an_echo():
    typed = body()
    typed["page"]["inventory"][0]["value"] = "hunter2"
    with pytest.raises(RefusedRequest) as e:
        parse_overlay_request(typed)
    assert "hunter2" not in str(e.value) and "value" in str(e.value)
    with pytest.raises(RefusedRequest, match="cookies"):
        parse_overlay_request({**body(), "cookies": []})


# ---- numbers mode: the legend ------------------------------------------------------------------------------------------
LOCATORS = ["self.page.locator('#user')", "self.page.locator('#ghost')", "self.page.locator('input')",
            "self.page.locator('div').locator('span')"]


def test_each_locator_gets_its_position_as_its_number_and_a_status():
    out = run(locators=LOCATORS)
    legend = legend_of(out)
    assert [e["number"] for e in out["legend"]] == [1, 2, 3, 4] and [e["locator"] for e in out["legend"]] == LOCATORS
    assert legend[1]["status"] == "matched" and legend[1]["ref"] == "e0" and legend[1]["refs"] == ["e0"] and legend[1]["count"] == 1
    assert legend[1]["kind"] == "id" and legend[1]["tier"] == 1
    assert legend[2]["status"] == "not_found" and legend[2]["ref"] is None and legend[2]["refs"] == [] and legend[2]["count"] == 0
    assert legend[3]["status"] == "ambiguous" and legend[3]["count"] == 2 and legend[3]["ref"] is None
    assert legend[3]["refs"] == ["e0", "e1"]
    assert legend[4]["status"] == "unknown" and legend[4]["count"] is None
    assert out["labels"] == "numbers" and all(e["label"] is None for e in out["legend"])
    assert out["summary"]["by_status"] == {"matched": 1, "not_found": 1, "ambiguous": 1, "unknown": 1}


def test_the_matcher_is_the_audits_own():
    from voyageur.audit import _hits
    from voyageur.inventory import Inventory
    req = parse_overlay_request(body())
    inv = Inventory(req.page.flat_inventory(), True)
    for expr in LOCATORS + ["page.get_by_role('button', name='Sign in')", "page.get_by_text('Sign in')", "page.locator('input').first"]:
        hits = _hits(inv, expr)
        entry = run_overlay(parse_overlay_request(body([expr])))["legend"][0]
        assert entry["count"] == (None if hits is None else len(hits)), expr
        assert entry["refs"] == ([] if hits is None else [h.get("ref") for h in hits if h.get("ref")]), expr


def test_a_narrowed_locator_marks_the_one_element_it_picks():
    out = run(locators=["self.page.locator('input').first"])
    entry = out["legend"][0]
    assert entry["status"] == "matched" and entry["count"] == 1 and entry["ref"] == "e0"


def test_a_match_outside_the_screenshot_is_not_visible_and_gets_no_mark():
    far = dict(BUTTON, box={"x": 5000, "y": 5000, "w": 40, "h": 20})
    out = run(locators=["self.page.locator('#go')", "self.page.locator('#pwd')"], inventory=[USER, PASSWORD, far])
    legend = legend_of(out)
    assert legend[1]["status"] == "not_visible" and legend[1]["ref"] == "e2" and legend[2]["status"] == "matched"
    assert out["summary"]["marked"] == 1


def test_an_element_with_no_box_is_not_visible():
    out = run(locators=["self.page.locator('#go')"], inventory=[USER, PASSWORD, {k: v for k, v in BUTTON.items() if k != "box"}])
    assert out["legend"][0]["status"] == "not_visible"


def test_an_ambiguous_locator_marks_every_match_with_its_one_number():
    out = run(locators=["self.page.locator('input')"])
    img = image_of(out)
    assert img.getpixel((10, 25)) == RED and img.getpixel((10, 65)) == RED          # the left edges of both inputs
    assert img.getpixel((50, 100)) == WHITE and out["legend"][0]["visible"] == 2


def test_a_matched_locator_is_marked_in_its_color_and_the_page_shows_through():
    out = run(locators=["self.page.locator('#pwd')", "self.page.locator('#go')"])
    img = image_of(out)
    assert (out["image"]["width"], out["image"]["height"]) == (300, 200)           # the whole viewport, not a crop
    assert img.getpixel((10, 65)) == RED and img.getpixel((209, 65)) == RED         # #pwd: number 1
    assert img.getpixel((110, 65)) == WHITE                                          # inside the box: the page's own pixels
    assert img.getpixel((10, 105)) == BLUE                                           # #go: number 2


def test_a_marked_element_shows_its_digit():
    img = image_of(run(locators=["self.page.locator('#pwd')"]))
    tab = {img.getpixel((x, y)) for x in range(10, 23) for y in range(32, 49)}       # the tab sits above the box, left aligned
    assert tab == {RED, WHITE}                                                         # its color and the digit's white, nothing else


def test_nothing_visible_still_answers_with_the_unmarked_screenshot_and_the_legend():
    out = run(locators=["self.page.locator('#ghost')"])
    assert out["summary"]["marked"] == 0 and out["legend"][0]["status"] == "not_found"
    assert image_of(out).tobytes() == Image.open(io.BytesIO(png_bytes())).convert("RGB").tobytes()


def test_the_response_matches_its_model():
    for kw in (dict(locators=LOCATORS), dict(), dict(locators=LOCATORS, labels="locators"), dict(labels="locators")):
        OverlayResponse.model_validate(run(**kw))


# ---- no locators: every visible element, labelled as the service would write it ---------------------------------------
def test_with_no_locators_every_visible_element_is_marked_with_the_locator_the_writer_would_write():
    out = run()
    legend = out["legend"]
    assert [e["ref"] for e in legend] == ["e0", "e1", "e2"] and [e["number"] for e in legend] == [1, 2, 3]
    assert legend[0]["locator"] == 'page.get_by_test_id("login-user")' and legend[0]["status"] == "matched" and legend[0]["tier"] == 0
    assert all(e["locator"] and e["count"] == 1 and e["refs"] == [e["ref"]] for e in legend)
    img = image_of(out)
    assert img.getpixel((10, 25)) == RED and img.getpixel((10, 65)) == BLUE and img.getpixel((10, 105)) == GREEN


def test_hidden_and_off_screen_elements_are_not_marked_when_there_are_no_locators():
    hidden = {"ref": "e3", "tag": "button", "name": "Ghost", "attrs": {"id": "ghost"}, "hidden": True, "box": {"x": 10, "y": 130, "w": 50, "h": 20}}
    far = {"ref": "e4", "tag": "button", "name": "Far", "attrs": {"id": "far"}, "box": {"x": 9000, "y": 9000, "w": 50, "h": 20}}
    out = run(inventory=[USER, PASSWORD, BUTTON, hidden, far])
    assert [e["ref"] for e in out["legend"]] == ["e0", "e1", "e2"]
    assert out["summary"]["not_visible"] == 2 and out["summary"]["inventory"] == 5


def test_an_element_with_no_stable_locator_is_marked_and_says_so():
    bare = {"ref": "e0", "tag": "div", "box": {"x": 10, "y": 10, "w": 50, "h": 20}}
    twin = {"ref": "e1", "tag": "div", "box": {"x": 10, "y": 50, "w": 50, "h": 20}}
    out = run(inventory=[bare, twin])
    assert [e["status"] for e in out["legend"]] == ["no_stable_locator", "no_stable_locator"]
    assert all(e["locator"] is None and e["ref"] for e in out["legend"]) and out["summary"]["marked"] == 2


def test_the_writer_follows_the_clients_language():
    ts = body()
    ts["client"]["language"] = "typescript"
    out = run_overlay(parse_overlay_request(ts))
    assert out["legend"][0]["locator"] == "page.getByTestId('login-user')"


# ---- no locators: text elements, not only controls; nested boxes --------------------------------------------------------
LONG_HEADING = "A heading that is far longer than the cap of a drawn label so that it is cut"
ACCOUNT_PAGE = [
    {"tag": "section", "role": "region", "name": "Account", "aria_label": "Account", "box": {"x": 5, "y": 5, "w": 290, "h": 190}},
    {"tag": "h1", "text": "Welcome back", "name": "Welcome back", "copy_only": True, "pw_text": "Welcome back",
     "box": {"x": 15, "y": 12, "w": 200, "h": 24}},
    {"tag": "label", "text": "Username", "for_attr": "user", "copy_only": True, "pw_text": "Username",
     "box": {"x": 15, "y": 44, "w": 80, "h": 16}},
    {"tag": "input", "type": "text", "role": "textbox", "name": "Username", "label": "Username", "attrs": {"id": "user"},
     "box": {"x": 15, "y": 62, "w": 200, "h": 28}},
    {"tag": "p", "text": "Your session has expired", "role": "status", "copy_only": True, "pw_text": "Your session has expired",
     "box": {"x": 15, "y": 100, "w": 250, "h": 18}},
    {"tag": "a", "role": "link", "name": "Forgot password?", "text": "Forgot password?", "pw_text": "Forgot password?",
     "box": {"x": 15, "y": 126, "w": 140, "h": 18}},
    {"tag": "button", "name": "Sign in", "text": "Sign in", "pw_text": "Sign in", "box": {"x": 15, "y": 152, "w": 100, "h": 32}},
]
ACCOUNT_ARIA = ('- region "Account":\n  - heading "Welcome back" [level=1]\n  - text: Username\n  - textbox "Username"\n'
                '  - status: Your session has expired\n  - link "Forgot password?"\n  - button "Sign in"\n')


def account(**options):
    """The page of a real probe request: the copy counts it sends, and an aria snapshot."""
    req = body(inventory=ACCOUNT_PAGE, **({"labels": options.pop("labels")} if "labels" in options else {}))
    req["page"].update(inventory_copy_withheld=0, inventory_copy_withheld_data=0, aria_snapshot=ACCOUNT_ARIA)
    if options:
        req["options"] = {**req.get("options", {}), **options}
    return req


def test_with_no_locators_headings_labels_status_text_and_named_sections_are_marked_with_controls():
    out = run_overlay(parse_overlay_request(account()))
    legend = out["legend"]
    assert [e["ref"] for e in legend] == [f"e{i}" for i in range(7)]
    assert [e["element_kind"] for e in legend] == ["text", "text", "text", "interactive", "text", "interactive", "interactive"]
    assert all(e["status"] == "matched" and e["locator"] for e in legend), [e["locator"] for e in legend]
    assert out["summary"]["by_element_kind"] == {"text": 4, "interactive": 3} and out["summary"]["marked"] == 7
    assert legend[0]["locator"] == 'page.get_by_role("region", name="Account", exact=True)'
    assert legend[1]["locator"] == 'page.get_by_text("Welcome back", exact=True)' and legend[4]["kind"] == "text"
    assert legend[2]["locator"] == "page.locator(\"label[for='user']\")"


def test_each_legend_entry_states_the_stability_rank_of_its_locator():
    legend = run_overlay(parse_overlay_request(account()))["legend"]
    assert [(e["kind"], e["tier"]) for e in legend] == [("role", 4), ("text", 7), ("css", 3), ("id", 1), ("text", 7), ("role", 4),
                                                        ("role", 4)]
    assert [e["copy_based"] for e in legend] == [True, True, False, False, True, True, True]


def test_the_kinds_option_filters_what_is_marked_and_the_default_is_everything():
    both = run_overlay(parse_overlay_request(account()))
    assert len(both["legend"]) == 7
    assert len(run_overlay(parse_overlay_request(account(kinds=["interactive", "text"])))["legend"]) == 7
    inter = run_overlay(parse_overlay_request(account(kinds=["interactive"])))
    assert [e["ref"] for e in inter["legend"]] == ["e3", "e5", "e6"] and [e["number"] for e in inter["legend"]] == [1, 2, 3]
    assert {e["element_kind"] for e in inter["legend"]} == {"interactive"} and inter["summary"]["not_visible"] == 4
    text = run_overlay(parse_overlay_request(account(kinds=["text"])))
    assert [e["ref"] for e in text["legend"]] == ["e0", "e1", "e2", "e4"] and text["summary"]["marked"] == 4


def test_kinds_must_be_known_and_cannot_be_combined_with_locators():
    for bad in ([], ["links"], ["text", "text", "text"], "text"):
        with pytest.raises(ValidationError):
            parse_overlay_request(account(kinds=bad))
    with pytest.raises(ValidationError, match="kinds"):
        parse_overlay_request({**account(kinds=["text"]), "locators": ["page.locator('#user')"]})


@pytest.mark.parametrize("tag,role,copy_only,expected", [
    ("h2", "", True, "text"), ("p", "", True, "text"), ("label", "", False, "text"), ("section", "region", False, "text"),
    ("div", "alert", False, "text"), ("span", "", True, "text"), ("nav", "", False, "text"), ("li", "", True, "text"),
    ("input", "", False, "interactive"), ("button", "", False, "interactive"), ("a", "", False, "interactive"),
    ("select", "", False, "interactive"), ("div", "button", False, "interactive"), ("span", "tab", False, "interactive"),
    ("div", "", False, "interactive"), ("summary", "", False, "interactive")])
def test_the_kind_of_an_element(tag, role, copy_only, expected):
    from voyageur.marks import element_kind
    assert element_kind({"tag": tag, "role": role, "copy_only": copy_only}) == expected


def test_a_long_locator_is_whole_in_the_legend_when_the_drawn_text_is_cut():
    page = [{"tag": "h1", "text": LONG_HEADING, "copy_only": True, "pw_text": LONG_HEADING, "box": {"x": 10, "y": 60, "w": 280, "h": 24}},
            {"tag": "p", "text": "Short", "copy_only": True, "pw_text": "Short", "box": {"x": 10, "y": 120, "w": 100, "h": 18}}]
    req = body(inventory=page, labels="locators")
    req["page"].update(inventory_copy_withheld=0, inventory_copy_withheld_data=0)
    long, short = run_overlay(parse_overlay_request(req))["legend"]
    assert LONG_HEADING in long["locator"] and len(long["locator"]) > MAX_LABEL_CHARS      # the legend holds all of it
    assert long["label_truncated"] is True and len(long["label"]) <= MAX_LABEL_CHARS and long["label"].endswith("...")
    assert short["label_truncated"] is False and short["label"] == short["locator"]
    narrow = body(inventory=page[1:], labels="locators", w=120, h=200)
    narrow["page"].update(inventory_copy_withheld=0, inventory_copy_withheld_data=0)
    cut = run_overlay(parse_overlay_request(narrow))["legend"][0]
    assert cut["label_truncated"] is True and len(cut["label"]) < len(cut["locator"]) and cut["label"].endswith("...")    # too wide for the picture


def test_a_number_label_is_never_marked_truncated():
    legend = run_overlay(parse_overlay_request(account()))["legend"]
    assert all(e["label"] is None and e["label_truncated"] is False for e in legend)


def test_boxes_that_overlap_get_different_colors_and_boxes_apart_keep_their_own():
    marks = [Mark(1, (10, 10, 280, 180)), Mark(9, (20, 20, 100, 30)),            # 9 would be 1's color: it sits inside 1
             Mark(2, (150, 60, 60, 30)), Mark(3, (20, 140, 60, 30))]
    out = render_marks(png_bytes(300, 200), marks, (300, 200, 1))
    colors = dict(out.colors)
    assert colors[9] != colors[1] and colors[2] == "#%02x%02x%02x" % ov._PALETTE[1] and colors[3] == "#%02x%02x%02x" % ov._PALETTE[2]
    assert colors[2] != colors[1] and colors[3] != colors[1]                         # every child differs from the section around it
    img = Image.open(io.BytesIO(out.png)).convert("RGB")
    assert img.getpixel((10, 100)) == ov._PALETTE[0]                                # the section's edge
    assert img.getpixel((20, 35)) == tuple(int(colors[9][i:i + 2], 16) for i in (1, 3, 5))      # the child's left edge, its own color


def test_every_nested_box_of_a_deep_page_has_a_color_unlike_the_boxes_around_it():
    marks = [Mark(i + 1, (10 + 12 * i, 10 + 12 * i, 280 - 24 * i, 180 - 24 * i)) for i in range(7)]       # seven boxes, one in another
    out = render_marks(png_bytes(300, 200), marks, (300, 200, 1))
    colors = [c for _, c in out.colors]
    assert len(set(colors)) == 7


def test_a_box_that_sits_right_inside_another_keeps_its_own_outline_visible():
    out = render_marks(png_bytes(300, 200), [Mark(1, (50, 50, 100, 60)), Mark(2, (50, 50, 100, 60))], (300, 200, 1))
    img = Image.open(io.BytesIO(out.png)).convert("RGB")
    assert img.getpixel((50, 80)) == ov._PALETTE[0] and img.getpixel((52, 80)) == ov._PALETTE[1]      # two outlines, side by side


def tabs_clear(out):
    return all(not ov._overlaps(a, b) for i, a in enumerate(out.tabs) for b in out.tabs[i + 1:])


@pytest.mark.parametrize("labels", ["numbers", "locators"])
def test_labels_of_nested_boxes_do_not_cover_each_other(labels):
    req = parse_overlay_request(account(labels=labels))
    texts = {e["number"]: e["locator"] for e in run_overlay(req)["legend"]}
    marks = [Mark(n, tuple(el["box"][k] for k in ("x", "y", "width", "height")), texts[n] if labels == "locators" else "")
             for n, el in enumerate(req.page.flat_inventory(), start=1)]
    out = render_marks(png_bytes(300, 200), marks, (300, 200, 1), labels=labels)
    assert len(out.tabs) == 7 and tabs_clear(out)


def test_labels_of_a_section_and_the_same_corner_stack_clear_of_each_other():
    marks = [Mark(1, (10, 40, 200, 100), "page.get_by_role('region')"), Mark(2, (10, 40, 200, 30), "page.get_by_text('Heading')"),
             Mark(3, (10, 40, 80, 20), "page.get_by_text('Eyebrow')")]                  # three boxes that share a top-left corner
    out = render_marks(png_bytes(300, 200), marks, (300, 200, 1), labels="locators")
    assert tabs_clear(out) and len(out.tabs) == 3


def test_the_legend_color_is_the_color_drawn_on_the_picture():
    out = run_overlay(parse_overlay_request(account(labels="locators")))
    img = image_of(out)
    assert all(e["color"] and e["color"].startswith("#") and len(e["color"]) == 7 for e in out["legend"])
    section, field = out["legend"][0], out["legend"][3]
    box = ACCOUNT_PAGE[3]["box"]
    assert img.getpixel((box["x"], box["y"] + box["h"] // 2)) == tuple(int(field["color"][i:i + 2], 16) for i in (1, 3, 5))
    assert section["color"] != field["color"]                                        # the section holds the field


def test_a_locator_that_is_not_drawn_has_no_color():
    out = run(locators=["self.page.locator('#ghost')"])
    assert out["legend"][0]["color"] is None and out["legend"][0]["visible"] == 0


# ---- locators mode: the text on the image ------------------------------------------------------------------------------
def test_every_printable_ascii_character_has_one_distinct_5_wide_glyph():
    glyphs = {chr(c): ov._GLYPHS[chr(c)] for c in range(0x20, 0x7F)}
    assert len(glyphs) == 95
    rows = {ch: g.split() for ch, g in glyphs.items()}
    assert all(1 <= len(r) <= 8 and all(len(x) == 5 and set(x) <= {"0", "1"} for x in r) for r in rows.values())
    assert len(set(glyphs.values())) == 95, "two characters share a bitmap"
    assert "1" not in glyphs[" "] and all("1" in g for ch, g in glyphs.items() if ch != " ")


def test_text_is_drawn_from_the_bitmap_pixel_for_pixel():
    im = Image.new("RGB", (40, 12), (0, 0, 0))
    ov._text(__import__("PIL.ImageDraw", fromlist=["x"]).Draw(im), "Aq", 1, 1, 1)
    for i, ch in enumerate("Aq"):
        for r, bits in enumerate(ov._GLYPHS[ch].split()):
            for c, bit in enumerate(bits):
                assert (im.getpixel((1 + i * 6 + c, 1 + r)) == WHITE) == (bit == "1"), (ch, r, c)


def test_clean_label_strips_control_characters_replaces_what_the_bitmap_lacks_and_caps_the_length():
    assert clean_label("a\x00b\nc\x1b[31m\x7fd\t") == "abc[31md"
    assert clean_label("café ‮中") == "caf? ??"
    assert clean_label("  page.locator('#a')  ") == "page.locator('#a')"
    long = clean_label("x" * 500)
    assert len(long) == MAX_LABEL_CHARS and long.endswith("...") and long.startswith("x" * 10)
    assert clean_label("y" * MAX_LABEL_CHARS) == "y" * MAX_LABEL_CHARS
    assert clean_label("x" * 500, 10) == "xxxxxxx..." and clean_label("") == "" and clean_label("\x00\x01") == ""


def test_locators_mode_draws_the_locator_text_and_still_returns_the_legend():
    nums = run(locators=["self.page.locator('#go')"], color=GRAY)
    text = run(locators=["self.page.locator('#go')"], labels="locators", color=GRAY)
    white = lambda o: image_of(o).tobytes().count(b"\xff\xff\xff")                   # noqa: E731  (the digits and the text are white)
    assert text["image"]["data"] != nums["image"]["data"] and white(text) > 4 * white(nums)
    e = text["legend"][0]
    assert e["status"] == "matched" and e["number"] == 1 and e["label"] == "self.page.locator('#go')" and text["labels"] == "locators"


def test_the_drawn_text_is_the_locator_only_capped_and_clean():
    hostile = "page.locator('#go')\x00\x1b[2J" + "A" * 300 + "‮"
    out = run(locators=[hostile], labels="locators")
    e = out["legend"][0]
    assert e["locator"] == hostile                                                    # the legend echoes what was asked
    assert e["label"] == clean_label(hostile) and len(e["label"]) == MAX_LABEL_CHARS and "\x1b" not in e["label"]


def test_a_label_wider_than_the_image_is_shortened_to_fit():
    out = render_marks(png_bytes(120, 80, GRAY), [Mark(1, (80, 40, 30, 20), "A" * MAX_LABEL_CHARS)], (120, 80, 1), labels="locators")
    assert (out.width, out.height) == (120, 80) and out.drawn == (1,)


def test_labels_in_locators_mode_for_every_visible_element_use_the_written_locators():
    out = run(labels="locators")
    assert out["legend"][0]["label"] == 'page.get_by_test_id("login-user")'
    assert all(e["label"] for e in out["legend"])


def test_an_element_with_no_stable_locator_gets_its_number_as_the_text_in_locators_mode():
    out = run(inventory=[{"ref": "e0", "tag": "div", "box": {"x": 10, "y": 10, "w": 50, "h": 20}}], labels="locators")
    assert out["legend"][0]["label"] is None and out["summary"]["marked"] == 1


# ---- determinism and geometry -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("labels", ["numbers", "locators"])
def test_the_same_request_gives_the_same_bytes(labels):
    a = run(locators=LOCATORS, labels=labels)
    b = run(locators=LOCATORS, labels=labels)
    assert a == b and a["image"]["data"] == b["image"]["data"]


def test_the_image_has_no_metadata_and_is_never_enlarged_nor_over_1600_px():
    out = run(locators=["self.page.locator('#user')"], w=300, h=200)
    im = Image.open(io.BytesIO(base64.b64decode(out["image"]["data"])))
    assert im.format == "PNG" and not im.info and im.size == (300, 200)
    big = run(locators=["self.page.locator('#user')"], w=3000, h=2000)
    assert max(big["image"]["width"], big["image"]["height"]) == ov.OVERLAY_LONG_SIDE


def test_boxes_follow_dpr_and_scroll():
    req = body(["self.page.locator('#user')"], w=150, h=100)
    req["page"]["screenshot"] = shot(300, 200)
    req["page"]["viewport"] = {"w": 150, "h": 100, "dpr": 2, "scroll": {"x": 0, "y": 0}}
    img = image_of(run_overlay(parse_overlay_request(req)))
    assert img.getpixel((20, 50)) == RED                                              # x 10 css px at dpr 2
    scrolled = body(["self.page.locator('#user')"])
    scrolled["page"]["inventory"][0]["box"] = {"x": 10, "y": 610, "w": 200, "h": 30}
    scrolled["page"]["viewport"]["scroll"] = {"x": 0, "y": 600}
    assert image_of(run_overlay(parse_overlay_request(scrolled))).getpixel((10, 25)) == RED


def test_a_screenshot_that_does_not_fit_its_viewport_is_refused_with_the_reason():
    wrong = body(["self.page.locator('#user')"])
    wrong["page"]["viewport"] = {"w": 300, "h": 400, "dpr": 1}
    assert run_overlay(parse_overlay_request(wrong)) == {"refused": "viewport_mismatch"}
    none = body()
    none["page"]["viewport"] = None
    assert run_overlay(parse_overlay_request(none)) == {"refused": "no_viewport"}


def test_marks_are_numbered_by_position_whatever_is_visible():
    marks = [Mark(1, None), Mark(2, (10, 10, 40, 20)), Mark(2, (100, 10, 40, 20)), Mark(3, (9000, 10, 5, 5))]
    out = render_marks(png_bytes(), marks, (300, 200, 1))
    assert out.drawn == (2,) and out.not_visible == (1, 3) and (out.width, out.height) == (300, 200)


def test_render_marks_refuses_a_broken_picture_with_a_reason():
    with pytest.raises(OverlayError) as e:
        render_marks(png_bytes()[:-30], [Mark(1, (1, 1, 5, 5))], (300, 200, 1))
    assert e.value.reason == "undecodable"
    with pytest.raises(OverlayError) as e:
        render_marks(png_bytes(), [Mark(1, (1, 1, 5, 5))], (None, None, None))
    assert e.value.reason == "no_viewport"


@pytest.mark.parametrize("labels", ["numbers", "locators"])
def test_hostile_boxes_never_escape_render_marks(labels):
    boxes = [(-1e308, 0, 1e308, 10), (0, 0, 1.7e308, 1.7e308), (float("nan"), 0, 5, 5), (0, 0, float("inf"), 5), (10, 10, 20, 20)]
    out = render_marks(png_bytes(), [Mark(i + 1, b, "page.locator('x')" if labels == "locators" else "") for i, b in enumerate(boxes)],
                       (300, 200, 1), labels=labels)
    assert out.drawn == (5,)


def test_many_marks_are_drawn_without_trouble():
    inv = [{"ref": f"e{i}", "tag": "button", "name": f"Button {i}", "attrs": {"id": f"b{i}"},
            "box": {"x": 5 + (i % 25) * 38, "y": 5 + (i // 25) * 28, "w": 30, "h": 20}} for i in range(500)]
    out = run(inventory=inv, labels="locators", w=1000, h=600)
    assert out["summary"]["marked"] == 500 and len(out["legend"]) == 500


def test_the_work_budget_stops_the_matching_and_says_so():
    out = run_overlay(parse_overlay_request(body(LOCATORS * 5)), budget_s=-1)
    assert out["truncated"] is True and len(out["legend"]) < 20 and "image" in out
    out = run_overlay(parse_overlay_request(body()), budget_s=-1)
    assert out["truncated"] is True


def test_the_boxes_drawn_on_one_picture_are_bounded_whatever_the_locators_match():
    from voyageur.marks import MAX_MARKS
    inv = [{"ref": f"e{i}", "tag": "input", "attrs": {"name": f"n{i}"}, "box": {"x": 5 + (i % 25) * 38, "y": 5 + (i // 25) * 28, "w": 30, "h": 20}}
           for i in range(400)]
    out = run(locators=["page.locator('input')"] * 100, inventory=inv, w=1000, h=600, labels="locators")
    assert sum(e["visible"] for e in out["legend"]) == MAX_MARKS and len(out["legend"]) == 100
    assert all(e["count"] == 400 and len(e["refs"]) == 50 for e in out["legend"])


def test_the_number_of_matches_marked_per_locator_is_bounded():
    inv = [{"ref": f"e{i}", "tag": "input", "attrs": {"name": f"n{i}"}, "box": {"x": 5 + (i % 25) * 38, "y": 5 + (i // 25) * 28, "w": 30, "h": 20}}
           for i in range(200)]
    entry = run(locators=["page.locator('input')"], inventory=inv, w=1000, h=600)["legend"][0]
    assert entry["status"] == "ambiguous" and entry["count"] == 200 and len(entry["refs"]) == 50 and entry["visible"] == 50


# ---- the route --------------------------------------------------------------------------------------------------------
def test_the_route_answers_json_with_a_png_a_legend_and_no_store():
    c, _ = client()
    r = post(c, body(["self.page.locator('#user')"]))
    assert r.status_code == 200 and r.headers["cache-control"] == "private, no-store" and r.headers["x-content-type-options"] == "nosniff"
    out = r.json()
    assert out["image"]["media_type"] == "image/png" and base64.b64decode(out["image"]["data"])[:8] == b"\x89PNG\r\n\x1a\n"
    assert out["legend"][0]["status"] == "matched" and out["truncated"] is False
    assert r.json() == post(c, body(["self.page.locator('#user')"])).json()


def test_the_route_needs_the_token_like_every_v1_route():
    c, _ = client()
    assert c.post("/v1/overlay", content=json.dumps(body())).status_code == 401
    assert post(c, body(), headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert post(c, body(), headers={"Authorization": TOKEN}).status_code == 401
    open_c, _ = client(token=None)
    assert open_c.post("/v1/overlay", content=json.dumps(body())).status_code == 200


def test_a_post_without_json_content_type_is_a_415():
    c, _ = client()
    assert c.post("/v1/overlay", content=json.dumps(body()), headers={**AUTH, "Content-Type": "text/plain"}).status_code == 415


def test_the_route_refuses_what_heal_refuses_and_names_the_rule_not_the_input():
    c, _ = client()
    assert post(c, body(["x"] * 101)).status_code == 413
    typed = body()
    typed["page"]["inventory"][0]["value"] = "hunter2"
    r = post(c, typed)
    assert r.status_code == 422 and "hunter2" not in r.text
    assert post(c, {**body(), "storage_state": {}}).status_code == 422
    assert post(c, {**body(), "surprise": 1}).status_code == 422
    assert c.post("/v1/overlay", content=b"not json", headers={**AUTH, "Content-Type": "application/json"}).status_code == 422
    assert post(c, {"page": {}}).status_code == 422
    no_shot = body()
    no_shot["page"]["screenshot"] = None
    assert post(c, no_shot).status_code == 422


def test_the_screenshot_door_checks_are_the_heals():
    c, _ = client()
    for raw in (b"GIF89a" + b"\x00" * 40, b"<svg/>", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40):
        r = post(c, body(**{"page": dict(body()["page"], screenshot=shot(raw=raw))}))
        assert r.status_code == 422 and "PNG" in r.text, raw
    huge = shot(raw=b"\x89PNG\r\n\x1a\n" + b"\x00" * (4 * 1024 * 1024 + 10))
    assert post(c, body(**{"page": dict(body()["page"], screenshot=huge)})).status_code == 413
    claim = shot()
    claim["width"] = 301
    assert post(c, body(**{"page": dict(body()["page"], screenshot=claim)})).status_code == 422
    import struct
    import zlib
    ihdr = struct.pack(">IIBBBBB", 8000, 8000, 8, 2, 0, 0, 0)
    bomb = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    r = post(c, body(**{"page": dict(body()["page"], screenshot=dict(shot(raw=bomb), width=8000, height=8000))}))
    assert r.status_code == 422 and "million pixels" in r.text


def test_a_picture_that_does_not_fit_its_viewport_is_a_422_that_names_the_reason():
    c, _ = client()
    wrong = body()
    wrong["page"]["viewport"] = {"w": 300, "h": 400, "dpr": 1}
    r = post(c, wrong)
    assert r.status_code == 422 and "viewport" in r.json()["detail"] and "iVBOR" not in r.text


def test_a_png_that_passes_the_door_but_cannot_be_decoded_is_a_422():
    good = png_bytes()
    # a valid chunk structure whose compressed pixel data is not a zlib stream: CRCs hold, so only decoding fails
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    bad = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 300, 200, 8, 2, 0, 0, 0)) + chunk(b"IDAT", b"not zlib at all") + chunk(b"IEND", b"")
    assert good != bad
    c, _ = client()
    r = post(c, body(**{"page": dict(body()["page"], screenshot=shot(raw=bad))}))
    assert r.status_code == 422, r.text
    assert "iVBOR" not in r.text


# ---- privacy: no model, no record, no vision setting -------------------------------------------------------------------
def test_no_model_is_called_nothing_is_stored_and_it_is_counted():
    model = FakeModel('{"ref": "e0"}')
    c, core = client(model=model)
    assert post(c, body(LOCATORS, labels="locators")).status_code == 200 and post(c, body()).status_code == 200
    assert model.calls == [] and core.store.count_rows("heals") == 0 and core.store.count_rows("overlays") == 0
    assert core.store.count_rows("calls") == 0 and core.open_conversations() == 0
    assert "voyageur_overlays_total 2" in c.get("/metrics", headers=AUTH).text


def test_it_is_not_gated_on_the_vision_settings():
    for vision in (Endpoint(), Endpoint(base_url="http://localhost:11434/v1", model="v", images_allowed=False),
                   Endpoint(base_url="http://localhost:11434/v1", model="v", images_allowed=True)):
        core = HealSession(ServiceSettings(retention_days=14), Store(":memory:", 14), FakeModel(), ENDPOINT, vision_endpoint=vision)
        c = TestClient(create_app(core, TOKEN), base_url="http://127.0.0.1", headers=JSON)
        assert post(c, body(["self.page.locator('#user')"])).status_code == 200


def test_a_retention_of_zero_changes_nothing():
    c, core = client(retention=0)
    assert post(c, body()).status_code == 200 and core.store.count_rows("overlays") == 0


# ---- work limits ----------------------------------------------------------------------------------------------------------
def test_a_busy_service_answers_429_with_retry_after_and_shares_the_audit_slots():
    c, core = client()
    taken = []
    while core._audit_slots.acquire(blocking=False):
        taken.append(1)
    try:
        r = post(c, body())
        assert r.status_code == 429 and r.headers["retry-after"] == "2"
        assert post(c, {**body(), "bad": 1}).status_code == 422                       # a refusal needs no slot
    finally:
        for _ in taken:
            core._audit_slots.release()
    assert post(c, body()).status_code == 200


def test_the_slot_is_released_when_the_overlay_is_refused():
    c, core = client()
    wrong = body()
    wrong["page"]["viewport"] = {"w": 300, "h": 400, "dpr": 1}
    for _ in range(4):
        assert post(c, wrong).status_code == 422
    assert post(c, body()).status_code == 200


class _Stops:
    def __init__(self, exc):
        self.exc = exc

    def run(self, fn, *args, timeout=None):
        raise self.exc

    def close(self):
        pass


@pytest.mark.parametrize("exc,status", [(DeadlineExceeded("x"), 504), (WorkerFailed("x"), 503)])
def test_a_worker_that_is_stopped_is_a_504_or_503_and_frees_its_slot(exc, status):
    core = session(FakeModel())
    core.runner = _Stops(exc)
    c = TestClient(create_app(core, None), base_url="http://127.0.0.1", headers=JSON)
    assert post(c, body(), headers={}).status_code == status
    core.runner = __import__("voyageur.deadline", fromlist=["x"]).InlineRunner()
    assert post(c, body(), headers={}).status_code == 200


def test_it_runs_in_a_killable_worker_process_and_gives_the_same_bytes():
    inline = run_overlay(parse_overlay_request(body(LOCATORS, labels="locators")))
    runner = ProcessRunner(1)
    try:
        core = HealSession(ServiceSettings(retention_days=14, workers=2), Store(":memory:", 14), FakeModel(), ENDPOINT, runner=runner)
        out = core.overlay(parse_overlay_request(body(LOCATORS, labels="locators")))
    finally:
        runner.close()
    assert out == inline


# ---- MCP and the API page --------------------------------------------------------------------------------------------------
def test_overlay_locators_is_the_route_over_the_same_core_and_refuses_the_same():
    from voyageur.mcp_server import ToolRefused, overlay_locators
    core = session(FakeModel())
    out = overlay_locators(core, body(LOCATORS))
    assert out["legend"][0]["status"] == "matched" and out["image"]["media_type"] == "image/png" and core.store.count_rows("heals") == 0
    typed = body()
    typed["page"]["inventory"][0]["value"] = "hunter2"
    with pytest.raises(ToolRefused) as e:
        overlay_locators(core, typed)
    assert "hunter2" not in str(e.value) and "value" in str(e.value)
    with pytest.raises(ToolRefused, match="schema"):
        overlay_locators(core, {"page": {}})
    with pytest.raises(ToolRefused):
        overlay_locators(core, [1, 2])                                      # type: ignore[arg-type]
    wrong = body()
    wrong["page"]["viewport"] = {"w": 300, "h": 400, "dpr": 1}
    with pytest.raises(ToolRefused, match="viewport"):
        overlay_locators(core, wrong)


def test_the_mcp_tool_returns_the_picture_as_an_image_block_and_the_legend_as_text():
    pytest.importorskip("mcp")
    import asyncio
    from voyageur.mcp_server import build_server
    server = build_server(session(FakeModel()))

    async def go():
        listed = {t.name: t for t in await server.list_tools()}
        ok = await server.call_tool("overlay_locators", {"request": body(LOCATORS, labels="locators")})
        try:
            await server.call_tool("overlay_locators", {"request": {"page": {}}})
            bad = None
        except Exception as e:                                                  # the SDK's tool error for a refused request
            bad = e
        return listed, ok.model_dump(mode="json"), bad
    listed, ok, bad = asyncio.run(go())
    assert "text the page supplied" in listed["overlay_locators"].description
    kinds = [c["type"] for c in ok["content"]]
    assert kinds == ["image", "text"] and ok["content"][0]["mime_type"] == "image/png"
    assert base64.b64decode(ok["content"][0]["data"])[:8] == b"\x89PNG\r\n\x1a\n"
    legend = json.loads(ok["content"][1]["text"])
    assert [e["status"] for e in legend["legend"]] == ["matched", "not_found", "ambiguous", "unknown"]
    assert "data" not in legend["image"] and legend["image"]["media_type"] == "image/png"       # the picture is not sent twice
    assert bad is not None and "schema" in str(bad)


def test_the_api_page_documents_the_route_with_a_working_example():
    c = TestClient(create_app(session(FakeModel()), ""), base_url="http://127.0.0.1", headers=JSON)
    doc = c.get("/openapi.json").json()
    op = doc["paths"]["/v1/overlay"]["post"]
    assert op["security"] == [{"bearer": []}]
    assert op["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/OverlayRequest")
    assert op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/OverlayResponse")
    assert {"OverlayRequest", "OverlayResponse"} <= set(doc["components"]["schemas"])
    examples = op["requestBody"]["content"]["application/json"]["examples"]
    assert examples
    for name, ex in examples.items():
        r = c.post("/v1/overlay", json=ex["value"])
        assert r.status_code == 200, (name, r.text)
        OverlayResponse.model_validate(r.json())
