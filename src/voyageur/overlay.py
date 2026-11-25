"""
===============================================================================
Overlay (numbered boxes on the screenshot, cropped and downscaled)
===============================================================================
Draws what the vision model is shown: the client's viewport screenshot with a numbered box on each candidate of the pick list,
the number being the candidate's position in that list (first candidate = 1). The service draws it, never the client, so both
frameworks get the same marked image. The picture carries digits and colored outlines and nothing else: no text from the page
is drawn, so nothing a page says can be written into a mark.

Geometry. An inventory `box` is in CSS pixels in DOCUMENT coordinates (the probe adds the scroll offset), the screenshot is
in device pixels from the viewport's top-left, so a candidate lands at
    ((box.x - scroll.x) * s, (box.y - scroll.y) * s),  s = image width / viewport width (the dpr when only that is known)
A candidate whose box is under half inside the image, or that has no box, is "not visible": it is listed in the prompt as such
and gets no mark. The vertical and horizontal scale must agree within 3 percent, or the picture is not of that viewport and no
overlay is made (a mark in the wrong place is worse than no mark).

Crop and scale. The crop is the union of the drawn boxes plus a margin, never smaller than 384 px a side where the image allows,
then downscaled so its long side is at most 1024 px (never enlarged). Marks are drawn after the scale, so a line and a digit are
the same size whatever the page's size.

Features:
    ✓ render(): the marked PNG and which marks were drawn, or OverlayError with a reason code
    ✓ Deterministic: integer crop arithmetic, a fixed palette, digits from a built-in 5x7 bitmap (no font file, no platform
      font), Lanczos resampling, no PNG metadata: the same inputs give the same bytes on one Pillow version
    ✓ A module-level function over plain data (bytes, tuples), so a killable worker can run it under the request deadline
    ✓ The screenshot is decoded here (once, at most 16 million pixels: screenshot.py checked the header first)

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import io
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

LONG_SIDE = 1024                 # the image sent to a model is at most this many px on its long side
MIN_CROP = 384                   # px a side the crop keeps for context, where the screenshot is that big
MARGIN = 0.15                    # of the boxes' long side, at least MIN_MARGIN px
MIN_MARGIN = 24
VISIBLE_SHARE = 0.5              # of a box that must be inside the screenshot for it to count as visible
SCALE_TOLERANCE = 0.03           # how far the horizontal and vertical scale may differ

Box = Tuple[float, float, float, float]          # x, y, width, height in CSS px, document coordinates
_PALETTE = ((200, 30, 30), (30, 60, 200), (0, 130, 60), (130, 40, 170),
            (210, 100, 0), (0, 125, 140), (190, 20, 120), (120, 70, 20))     # dark enough for white digits
_GLYPHS = {
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11110 00001 00001 01110 00001 00001 11110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
}


class OverlayError(ValueError):
    """No overlay could be made. `reason` is a short code the record keeps (the message never holds image or page data)."""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


@dataclass(frozen=True)
class Overlay:
    """The marked image (PNG bytes), its size, the marks drawn and the candidates that could not be (not visible)."""
    png: bytes
    width: int
    height: int
    drawn: Tuple[int, ...]
    not_visible: Tuple[int, ...]


def _scale(img_w: int, img_h: int, viewport: Tuple[Optional[float], Optional[float], Optional[float]]) -> float:
    vw, vh, dpr = viewport
    if vw and vw > 0:
        sx = img_w / vw
        if not math.isfinite(sx):
            raise OverlayError("viewport_mismatch", "the viewport width cannot place the boxes")
        if vh and vh > 0 and abs(img_h / vh - sx) > SCALE_TOLERANCE * sx:
            raise OverlayError("viewport_mismatch", "the screenshot's shape does not match the viewport it was taken in")
        return sx
    if dpr and 0 < dpr <= 100 and math.isfinite(dpr):
        return float(dpr)
    raise OverlayError("no_viewport", "the request has no viewport size to place the boxes with")


def _visible(box: Optional[Box], scroll: Tuple[float, float], s: float, w: int, h: int) -> Optional[Tuple[float, ...]]:
    """The part of `box` inside the image as (x0, y0, x1, y1) in image px, or None when it is mostly outside or has no box."""
    if box is None or not all(math.isfinite(v) for v in box) or box[2] <= 0 or box[3] <= 0:
        return None
    x0, y0 = (box[0] - scroll[0]) * s, (box[1] - scroll[1]) * s
    x1, y1 = x0 + box[2] * s, y0 + box[3] * s
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):          # an overflow (inf - inf) is a candidate nobody can see
        return None
    cx0, cy0, cx1, cy1 = max(x0, 0.0), max(y0, 0.0), min(x1, float(w)), min(y1, float(h))
    if cx1 - cx0 < 2 or cy1 - cy0 < 2:
        return None
    if (cx1 - cx0) * (cy1 - cy0) < VISIBLE_SHARE * (x1 - x0) * (y1 - y0):
        return None
    return cx0, cy0, cx1, cy1


def _crop(union: Tuple[float, float, float, float], w: int, h: int) -> Tuple[int, int, int, int]:
    """Integer crop (left, top, right, bottom): the union plus a margin, at least MIN_CROP a side, inside the image."""
    x0, y0, x1, y1 = union
    pad = max(MIN_MARGIN, MARGIN * max(x1 - x0, y1 - y0))
    left, top, right, bottom = math.floor(x0 - pad), math.floor(y0 - pad), math.ceil(x1 + pad), math.ceil(y1 + pad)

    def fit(lo: int, hi: int, size: int) -> Tuple[int, int]:
        if hi - lo < min(MIN_CROP, size):
            mid = (lo + hi) // 2
            lo, hi = mid - min(MIN_CROP, size) // 2, mid - min(MIN_CROP, size) // 2 + min(MIN_CROP, size)
        lo, hi = max(lo, 0), min(hi, size)
        if hi - lo < min(MIN_CROP, size):                          # clamping cut it short: take the room from the other side
            need = min(MIN_CROP, size)
            lo, hi = (0, need) if lo == 0 else (size - need, size)
        return lo, hi
    left, right = fit(left, right, w)
    top, bottom = fit(top, bottom, h)
    return left, top, right, bottom


def _digits(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, g: int) -> None:
    for i, ch in enumerate(text):
        for row, bits in enumerate(_GLYPHS[ch].split()):
            for col, bit in enumerate(bits):
                if bit == "1":
                    px, py = x + (i * 6 + col) * g, y + row * g
                    draw.rectangle((px, py, px + g - 1, py + g - 1), fill=(255, 255, 255))


def _mark(draw: ImageDraw.ImageDraw, number: int, rect: Tuple[float, float, float, float], size: Tuple[int, int],
          g: int) -> None:
    color = _PALETTE[(number - 1) % len(_PALETTE)]
    x0, y0, x1, y1 = (round(v) for v in rect)
    for k in range(max(1, g)):                                       # an outline g px thick, inside the box
        draw.rectangle((x0 + k, y0 + k, max(x0 + k, x1 - 1 - k), max(y0 + k, y1 - 1 - k)), outline=color)
    text = str(number)
    pad = g
    tab_w, tab_h = len(text) * 6 * g - g + 2 * pad, 7 * g + 2 * pad
    tx = min(max(0, x0), max(0, size[0] - tab_w))
    ty = y0 - tab_h if y0 - tab_h >= 0 else min(y0, max(0, size[1] - tab_h))
    draw.rectangle((tx, ty, tx + tab_w - 1, ty + tab_h - 1), fill=color)
    _digits(draw, text, tx + pad, ty + pad, g)


def render(png: bytes, boxes: Sequence[Optional[Box]], viewport: Tuple[Optional[float], Optional[float], Optional[float]],
           scroll: Tuple[float, float] = (0.0, 0.0)) -> Overlay:
    """The marked, cropped, downscaled screenshot for the candidates `boxes` (candidate i is mark i + 1).
    `viewport` is (width, height, dpr) in CSS px and `scroll` the page's scroll offset when the screenshot was taken.
    OverlayError when no mark can be drawn (no_visible_candidate), the picture does not fit the viewport (viewport_mismatch,
    no_viewport) or the file cannot be decoded (undecodable)."""
    try:
        im = Image.open(io.BytesIO(png), formats=["PNG"])
        im.load()
        im = im.convert("RGB")
    except Exception:
        raise OverlayError("undecodable", "the screenshot could not be decoded") from None
    w, h = im.size
    s = _scale(w, h, viewport)
    seen: List[Tuple[int, Tuple[float, ...]]] = []
    hidden: List[int] = []
    for i, box in enumerate(boxes):
        part = _visible(box, scroll, s, w, h)
        (seen.append((i + 1, part)) if part else hidden.append(i + 1))
    if not seen:
        raise OverlayError("no_visible_candidate", "none of the candidates is inside the screenshot")
    union = (min(p[0] for _, p in seen), min(p[1] for _, p in seen), max(p[2] for _, p in seen), max(p[3] for _, p in seen))
    left, top, right, bottom = _crop(union, w, h)
    cw, ch = right - left, bottom - top
    f = min(1.0, LONG_SIDE / max(cw, ch))
    size = (max(1, round(cw * f)), max(1, round(ch * f)))
    out = im.crop((left, top, right, bottom))
    if size != out.size:
        out = out.resize(size, Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(out)
    g = max(2, round(max(size) / 340))
    for number, (x0, y0, x1, y1) in seen:
        # the clipped part of the box, in the crop's pixels (the marked box is what a person sees of the element)
        _mark(draw, number, ((x0 - left) * f, (y0 - top) * f, (x1 - left) * f, (y1 - top) * f), size, g)
    buf = io.BytesIO()
    out.save(buf, format="PNG", compress_level=6)
    return Overlay(buf.getvalue(), size[0], size[1], tuple(n for n, _ in seen), tuple(hidden))


__all__ = ["Overlay", "OverlayError", "render", "LONG_SIDE", "MIN_CROP"]
