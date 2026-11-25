"""Vision input: the screenshot checks (screenshot.py, the schema) and the overlay geometry (overlay.py)."""
import base64
import io
import struct
import zlib

import pytest
from PIL import Image

from voyageur import screenshot
from voyageur.overlay import LONG_SIDE, OverlayError, render
from voyageur.schemas import LimitExceeded, RefusedRequest, ValidationError, parse_request
from tests.unit.scenarios import request

pytestmark = pytest.mark.unit


def png_bytes(w=300, h=200, color=(255, 255, 255)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def shot(data=None, w=300, h=200, media_type="image/png"):
    raw = data if data is not None else png_bytes(w, h)
    return {"media_type": media_type, "data": base64.b64encode(raw).decode(), "width": w, "height": h}


def with_shot(screenshot_=None, vision="auto"):
    req = request()
    req["page"]["screenshot"] = screenshot_
    req["options"]["vision"] = vision
    return req


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def header_only(w, h):
    """The signature and a valid IHDR claiming w x h, and nothing else: what a pixel bomb's header looks like."""
    return screenshot.PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))


# ---- the request: what a screenshot may be ---------------------------------------------------------------------------
def test_a_png_screenshot_is_accepted_and_decoded_once():
    req = parse_request(with_shot(shot()))
    s = req.page.screenshot
    assert s.width == 300 and s.height == 200 and s.png()[:8] == screenshot.PNG_SIGNATURE


def test_no_screenshot_is_fine_with_any_vision_mode():
    for mode in ("off", "auto", "on"):
        assert parse_request(with_shot(None, mode)).page.screenshot is None


def test_vision_off_with_a_screenshot_is_refused():
    with pytest.raises(ValidationError) as e:
        parse_request(with_shot(shot(), "off"))
    assert "vision is off" in str(e.value) and "iVBOR" not in str(e.value)


@pytest.mark.parametrize("raw", [b"GIF89a" + b"\x00" * 40, b"\xff\xd8\xff\xe0" + b"\x00" * 40, b"<svg/>", b"%PDF-1.4 " * 5])
def test_a_file_that_is_not_a_png_is_refused_whatever_it_claims(raw):
    with pytest.raises(ValidationError, match="not a PNG"):
        parse_request(with_shot(shot(raw)))


def test_the_signature_alone_is_not_a_png():
    with pytest.raises(ValidationError, match="not a PNG"):
        parse_request(with_shot(shot(screenshot.PNG_SIGNATURE + b"\x00" * 40)))


@pytest.mark.parametrize("text", ["not base64 !!", "iVBORw0KGgo=\n", "iVBO Rw0K", "iVBORw0KGgo", "data:image/png;base64,iVBORw0KGgo=",
                                  "-_-_", ""])
def test_base64_is_strict(text):
    body = shot()
    body["data"] = text
    with pytest.raises(ValidationError, match="base64|empty|PNG"):
        parse_request(with_shot(body))


def test_the_media_type_must_be_png():
    for kind in ("image/jpeg", "image/gif", "text/html", ""):
        with pytest.raises(ValidationError):
            parse_request(with_shot(shot(media_type=kind)))


def test_a_screenshot_over_4mb_decoded_is_a_413():
    big = shot(b"\x89PNG\r\n\x1a\n" + b"\x00" * (4 * 1024 * 1024 + 10))
    with pytest.raises(LimitExceeded, match="4 MB"):
        parse_request(with_shot(big))


def test_a_pixel_bomb_is_refused_from_its_header_before_anything_is_decoded(monkeypatch):
    def never(*a, **k):
        raise AssertionError("an image decoder was reached")
    monkeypatch.setattr(screenshot.Image, "open", never)
    for w, h in ((8000, 8000), (8192, 4097), (5000, 4000)):
        body = shot(header_only(w, h), w, h)
        with pytest.raises(ValidationError, match="million pixels"):
            parse_request(with_shot(body))


def test_a_side_over_8192_is_refused():
    with pytest.raises(ValidationError):
        parse_request(with_shot(shot(header_only(9000, 10), 9000, 10)))
    with pytest.raises(ValidationError):
        parse_request(with_shot(dict(shot(), width=9000)))


def test_claimed_dimensions_must_match_the_png():
    for w, h in ((301, 200), (300, 199), (600, 400), (1, 1)):
        body = dict(shot(w=300, h=200), width=w, height=h)
        with pytest.raises(ValidationError, match="do not match"):
            parse_request(with_shot(body))


def test_pillows_own_bomb_warning_is_an_error(monkeypatch):
    monkeypatch.setattr(screenshot.Image, "MAX_IMAGE_PIXELS", 1000)       # 100 x 100 is over this, under ours
    with pytest.raises(ValidationError, match="readable"):
        parse_request(with_shot(shot(w=100, h=100)))


def test_a_png_with_a_corrupt_chunk_is_not_readable():
    raw = bytearray(png_bytes())
    raw[-20] ^= 0xFF                                     # inside the last IDAT or its CRC
    with pytest.raises(ValidationError, match="readable"):
        parse_request(with_shot(shot(bytes(raw))))
    with pytest.raises(ValidationError, match="readable"):
        parse_request(with_shot(shot(png_bytes()[:-30])))        # truncated: no IEND


def test_image_bytes_do_not_appear_in_a_repr_or_an_error():
    req = parse_request(with_shot(shot()))
    assert shot()["data"][:40] not in repr(req.page.screenshot) and shot()["data"][:40] not in repr(req)
    with pytest.raises(ValidationError) as e:
        parse_request(with_shot(dict(shot(), width=1)))
    assert shot()["data"][:40] not in str(e.value)


def test_the_screenshot_object_refuses_unknown_keys():
    with pytest.raises(ValidationError):
        parse_request(with_shot(dict(shot(), extra="x")))


def test_viewport_scroll_is_optional_and_never_negative():
    req = with_shot(shot())
    req["page"]["viewport"] = {"w": 300, "h": 200, "dpr": 1, "scroll": {"x": 0, "y": 640}}
    assert parse_request(req).page.viewport.scroll.y == 640
    req["page"]["viewport"]["scroll"] = {"x": 0, "y": -1}
    with pytest.raises(ValidationError):
        parse_request(req)
    req["page"]["viewport"] = {"w": 300, "h": 200, "dpr": 1}
    assert parse_request(req).page.viewport.scroll is None


def test_non_json_and_secret_refusals_still_come_first():
    with pytest.raises(RefusedRequest):
        parse_request(b"not json")


# ---- the overlay -----------------------------------------------------------------------------------------------------
def pixel(png, xy):
    return Image.open(io.BytesIO(png)).convert("RGB").getpixel(xy)


RED = (200, 30, 30)
BLUE = (30, 60, 200)
WHITE = (255, 255, 255)


def test_a_mark_is_drawn_on_the_candidate_and_numbered_by_list_position():
    o = render(png_bytes(300, 200), [(100, 50, 60, 40), (200, 120, 50, 30)], (300, 200, 1))
    assert (o.width, o.height) == (300, 200) and o.drawn == (1, 2) and o.not_visible == ()
    assert pixel(o.png, (100, 70)) == RED and pixel(o.png, (159, 70)) == RED       # box 1: left and right edge
    assert pixel(o.png, (130, 70)) == WHITE                                         # its inside is the page's own pixels
    assert pixel(o.png, (200, 140)) == BLUE                                         # box 2 has the second color


def test_device_pixel_ratio_scales_the_boxes_to_the_image():
    o = render(png_bytes(340, 240), [(40, 20, 30, 20)], (170, 120, 2))              # a 170 x 120 CSS viewport at dpr 2
    assert (o.width, o.height) == (340, 240)
    assert pixel(o.png, (80, 60)) == RED and pixel(o.png, (139, 60)) == RED and pixel(o.png, (110, 60)) == WHITE


def test_dpr_alone_places_the_boxes_when_the_viewport_size_is_unknown():
    o = render(png_bytes(340, 240), [(40, 20, 30, 20)], (None, None, 2))
    assert pixel(o.png, (80, 60)) == RED


def test_scroll_moves_document_coordinates_into_the_viewport():
    o = render(png_bytes(300, 200), [(100, 650, 60, 40)], (300, 200, 1), (0, 600))      # document y 650, scrolled 600
    assert o.drawn == (1,) and pixel(o.png, (100, 70)) == RED
    o2 = render(png_bytes(300, 200), [(100, 50, 60, 40), (100, 650, 60, 40)], (300, 200, 1))
    assert o2.drawn == (1,) and o2.not_visible == (2,)                                  # unscrolled, the second is below


def test_a_candidate_off_screen_or_without_a_box_is_not_visible_and_gets_no_mark():
    o = render(png_bytes(300, 200), [(100, 50, 60, 40), None, (500, 50, 60, 40), (-300, 0, 60, 40), (0, 0, 0, 0)],
               (300, 200, 1))
    assert o.drawn == (1,) and o.not_visible == (2, 3, 4, 5)


def test_a_box_mostly_outside_the_image_is_not_visible_one_mostly_inside_is():
    o = render(png_bytes(300, 200), [(100, 50, 20, 20), (290, 50, 40, 20), (280, 100, 40, 20)], (300, 200, 1))
    assert o.drawn == (1, 3) and o.not_visible == (2,)        # 10 of 40 px inside vs 20 of 40


def test_no_visible_candidate_means_no_overlay():
    with pytest.raises(OverlayError) as e:
        render(png_bytes(300, 200), [None, (900, 900, 10, 10)], (300, 200, 1))
    assert e.value.reason == "no_visible_candidate"


def test_a_screenshot_that_does_not_fit_the_viewport_makes_no_overlay():
    with pytest.raises(OverlayError) as e:
        render(png_bytes(300, 200), [(10, 10, 20, 20)], (300, 400, 1))             # wrong aspect
    assert e.value.reason == "viewport_mismatch"
    with pytest.raises(OverlayError) as e:
        render(png_bytes(300, 200), [(10, 10, 20, 20)], (None, None, None))
    assert e.value.reason == "no_viewport"
    assert render(png_bytes(1280, 720), [(10, 10, 20, 20)], (1265, 720, 1)).drawn == (1,)        # a scrollbar's width is fine


def test_a_file_pillow_cannot_decode_makes_no_overlay():
    with pytest.raises(OverlayError) as e:
        render(png_bytes()[:-30], [(10, 10, 20, 20)], (300, 200, 1))
    assert e.value.reason == "undecodable"


def test_the_image_is_cropped_to_the_candidates_and_downscaled_to_1024():
    big = png_bytes(3000, 2000)
    o = render(big, [(1000, 800, 100, 60), (1400, 900, 120, 60)], (3000, 2000, 1))
    assert max(o.width, o.height) <= LONG_SIDE and (o.width, o.height) != (3000, 2000)
    assert o.width < 3000 and o.height < 2000                     # a crop, not the page
    whole = render(big, [(10, 10, 50, 50), (2900, 1900, 50, 50)], (3000, 2000, 1))
    assert max(whole.width, whole.height) == LONG_SIDE and whole.width / whole.height == pytest.approx(1.5, abs=0.02)
    small = render(png_bytes(900, 600), [(100, 100, 700, 400)], (900, 600, 1))
    assert (small.width, small.height) == (900, 600)                  # under 1024: never enlarged


def test_a_small_candidate_keeps_context_around_it():
    o = render(png_bytes(1280, 720), [(600, 300, 40, 20)], (1280, 720, 1))
    assert (o.width, o.height) == (384, 384)


def test_the_overlay_is_deterministic():
    boxes = [(100, 50, 60, 40), (200, 120, 50, 30), None]
    a = render(png_bytes(1600, 900), boxes, (1600, 900, 1))
    assert a == render(png_bytes(1600, 900), boxes, (1600, 900, 1)) and a.png == render(png_bytes(1600, 900), boxes,
                                                                                      (1600, 900, 1)).png


def test_the_overlay_has_no_metadata_and_is_a_png():
    o = render(png_bytes(), [(10, 10, 40, 40)], (300, 200, 1))
    im = Image.open(io.BytesIO(o.png))
    assert im.format == "PNG" and not im.info


def test_two_digit_marks_fit_and_overlay_takes_no_page_text():
    import inspect
    boxes = [(10 + (i % 10) * 25, 10 + (i // 10) * 25, 20, 20) for i in range(30)]
    o = render(png_bytes(300, 200), boxes, (300, 200, 1))
    assert o.drawn == tuple(range(1, 31))
    assert [p for p in inspect.signature(render).parameters] == ["png", "boxes", "viewport", "scroll"]      # numbers only in


# ---- hostile numbers --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("boxes,viewport,scroll", [
    ([(-1e308, 0, 1e308, 10)], (None, None, 2), (0.0, 0.0)),                  # inf - inf
    ([(0, 0, 1.7e308, 1.7e308)], (800, 600, 1), (0.0, 0.0)),
    ([(-1.5e308, -1.5e308, 1.5e308, 1.5e308)], (None, None, 2), (0.0, 0.0)),
    ([(10, 10, 50, 50)], (1e-300, None, 1), (0.0, 0.0)),                       # a scale of infinity
    ([(10, 10, 50, 50)], (float("nan"), 600, 1), (0.0, 0.0)),
    ([(10, 10, 50, 50)], (float("inf"), 600, 1), (0.0, 0.0)),
    ([(10, 10, 50, 50)], (None, None, 1e308), (0.0, 0.0)),
    ([(0, 0, 1e308, 1e308)], (None, None, 2), (1e7, 0.0)),
])
def test_hostile_numbers_never_escape_as_anything_but_an_overlay_error_or_a_skipped_candidate(boxes, viewport, scroll):
    try:
        out = render(png_bytes(800, 600), boxes, viewport, scroll)
    except OverlayError:
        return
    assert all(1 <= n <= len(boxes) for n in out.drawn)


def test_non_finite_derived_coordinates_make_a_candidate_not_visible():
    o = render(png_bytes(800, 600), [(-1e308, 0, 1e308, 10), (100, 100, 50, 50)], (800, 600, 1))
    assert o.drawn == (2,) and o.not_visible == (1,)


def test_box_and_viewport_values_are_bounded_in_the_schema():
    def with_box(box, viewport=None):
        req = with_shot(shot())
        req["page"]["inventory"][0]["box"] = box
        if viewport:
            req["page"]["viewport"] = viewport
        return req
    assert parse_request(with_box({"x": 1e7, "y": -1e7, "w": 10, "h": 10}))
    for bad in ({"x": 1e8, "y": 0, "w": 1, "h": 1}, {"x": 0, "y": 0, "w": 1e308, "h": 1}, {"x": -1e308, "y": 0, "w": 1, "h": 1}):
        with pytest.raises(ValidationError):
            parse_request(with_box(bad))
    for bad in ({"w": 1e8, "h": 1, "dpr": 1}, {"w": 1, "h": 1, "dpr": 0}, {"w": 1, "h": 1, "dpr": 11}, {"w": 1, "h": 1, "dpr": -1},
                {"w": -5, "h": 1, "dpr": 1}):
        with pytest.raises(ValidationError):
            parse_request(with_box({"x": 0, "y": 0, "w": 1, "h": 1}, bad))
    assert parse_request(with_box({"x": 0, "y": 0, "w": 1, "h": 1}, {"w": 1280, "h": 720, "dpr": 10}))
