"""
===============================================================================
Overlay (numbered boxes on the screenshot, cropped and downscaled; locator text for POST /v1/overlay)
===============================================================================
Draws what the vision model is shown: the client's viewport screenshot with a numbered box on each candidate of the pick list,
the number being the candidate's position in that list (first candidate = 1). The service draws it, never the client, so both
frameworks get the same marked image. The picture carries digits and colored outlines and nothing else: no text from the page
is drawn, so nothing a page says can be written into a mark. That path still calls render().

The same drawing serves POST /v1/overlay (marks.py), where nothing goes to a model: render_marks() marks any number of boxes on
the whole viewport (no crop, long side at most OVERLAY_LONG_SIDE), several boxes may share one number (an ambiguous locator), and
the label of a mark is its number or a line of text, the locator, drawn from the same built-in bitmap extended to printable
ASCII. The text is cleaned first (clean_label: control characters removed, anything the bitmap lacks drawn as `?`, at most
MAX_LABEL_CHARS characters, `...` at a cut).

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
    ✓ render_marks(): the whole-viewport variant for POST /v1/overlay with numbers or locator text as labels; every outline is
      drawn first and then every tab
    ✓ A tab goes outside its box (above, below or beside the outline, never over the element's own content) and never over
      another marked box or another tab. A locator's text that has no such place falls back to the number alone
      (`Overlay.number_only` says which, the legend carries the whole locator); a number tries the same places and then the gaps
      inside the boxes that hold its element (still clear of every other box and tab); one that has none either takes the place
      where it covers the least (area of other boxes and tabs under it, the old above, below, inside order breaking a tie), so
      no mark is dropped
    ✓ Placement is bounded: at most a dozen candidate places per tab, each checked against the boxes and tabs near it through a
      grid of 64 px cells, not against all of them
    ✓ Overlapping boxes stay legible: biggest drawn first (so a section lies under what it holds), a number whose box overlaps a
      lower number's box takes the next free palette color, a box that sits snugly inside another is drawn one outline's width
      further in, and tabs are placed biggest box first
    ✓ A 5x7 bitmap font for the 95 printable ASCII characters (a descender takes an eighth row): no font file and no platform
      font, so the drawn text is the same bytes everywhere
    ✓ The screenshot is decoded here (once, at most 16 million pixels: screenshot.py checked the header first)

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import io
import math
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from PIL import Image, ImageDraw

LONG_SIDE = 1024                 # the image sent to a model is at most this many px on its long side
MIN_CROP = 384                   # px a side the crop keeps for context, where the screenshot is that big
MARGIN = 0.15                    # of the boxes' long side, at least MIN_MARGIN px
MIN_MARGIN = 24
OVERLAY_LONG_SIDE = 1600         # POST /v1/overlay: the whole viewport, downscaled (never enlarged) to at most this on its long side
MAX_LABEL_CHARS = 48             # characters of locator text drawn next to a box
VISIBLE_SHARE = 0.5              # of a box that must be inside the screenshot for it to count as visible
SCALE_TOLERANCE = 0.03           # how far the horizontal and vertical scale may differ

Box = Tuple[float, float, float, float]          # x, y, width, height in CSS px, document coordinates
_PALETTE = ((200, 30, 30), (30, 60, 200), (0, 130, 60), (130, 40, 170),
            (210, 100, 0), (0, 125, 140), (190, 20, 120), (120, 70, 20))     # dark enough for white digits
_DIGITS = {
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11110 00001 00001 01110 00001 00001 11110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
}
# the other 85 printable ASCII characters: five columns, seven rows listed top to bottom (a descender adds an eighth)
_REST = {
    ' ': "00000 00000 00000 00000 00000 00000 00000", '!': "00100 00100 00100 00100 00100 00000 00100",
    '"': "01010 01010 01010 00000 00000 00000 00000", '#': "01010 01010 11111 01010 11111 01010 01010",
    '$': "00100 01111 10100 01110 00101 11110 00100", '%': "11000 11001 00010 00100 01000 10011 00011",
    '&': "01100 10010 10100 01000 10101 10010 01101", "'": "00100 00100 01000",
    '(': "00010 00100 01000 01000 01000 00100 00010", ')': "01000 00100 00010 00010 00010 00100 01000",
    '*': "00100 10101 01110 11111 01110 10101 00100", '+': "00000 00100 00100 11111 00100 00100 00000",
    ',': "00000 00000 00000 00000 00000 01100 00100 01000", '-': "00000 00000 00000 11111 00000 00000 00000",
    '.': "00000 00000 00000 00000 00000 01100 01100", '/': "00000 00001 00010 00100 01000 10000 00000",
    ':': "00000 01100 01100 00000 01100 01100", ';': "00000 01100 01100 00000 01100 00100 01000",
    '<': "00010 00100 01000 10000 01000 00100 00010", '=': "00000 00000 11111 00000 11111 00000 00000",
    '>': "01000 00100 00010 00001 00010 00100 01000", '?': "01110 10001 00001 00110 00100 00000 00100",
    '@': "01110 10001 10101 10111 10110 10000 01111", 'A': "00100 01010 10001 10001 11111 10001 10001",
    'B': "11110 10001 10001 11110 10001 10001 11110", 'C': "01110 10001 10000 10000 10000 10001 01110",
    'D': "11110 10001 10001 10001 10001 10001 11110", 'E': "11111 10000 10000 11110 10000 10000 11111",
    'F': "11111 10000 10000 11110 10000 10000 10000", 'G': "01111 10001 10000 10000 10011 10001 01111",
    'H': "10001 10001 10001 11111 10001 10001 10001", 'I': "01110 00100 00100 00100 00100 00100 01110",
    'J': "00111 00010 00010 00010 00010 10010 01100", 'K': "10001 10010 10100 11000 10100 10010 10001",
    'L': "10000 10000 10000 10000 10000 10000 11111", 'M': "10001 11011 10101 10101 10101 10001 10001",
    'N': "10001 10001 11001 10101 10011 10001 10001", 'O': "01110 10001 10001 10001 10001 10001 01110",
    'P': "11110 10001 10001 11110 10000 10000 10000", 'Q': "01110 10001 10001 10001 10101 10010 01101",
    'R': "11110 10001 10001 11110 10100 10010 10001", 'S': "01110 10001 10000 01110 00001 10001 01110",
    'T': "11111 10101 00100 00100 00100 00100 00100", 'U': "10001 10001 10001 10001 10001 10001 01110",
    'V': "10001 10001 10001 10001 10001 01010 00100", 'W': "10001 10001 10001 10101 10101 10101 01010",
    'X': "10001 10001 01010 00100 01010 10001 10001", 'Y': "10001 10001 01010 00100 00100 00100 00100",
    'Z': "11111 00001 00010 01110 01000 10000 11111", '[': "01110 01000 01000 01000 01000 01000 01110",
    '\\': "10000 01000 00100 00010 00001", ']': "01110 00010 00010 00010 00010 00010 01110",
    '^': "00100 01010 10001 00000 00000 00000 00000", '_': "00000 00000 00000 00000 00000 00000 11111",
    '`': "01000 00100 00010", 'a': "00000 00000 01110 00001 01111 10001 01111",
    'b': "10000 10000 10110 11001 10001 11001 10110", 'c': "00000 00000 01110 10000 10000 10001 01110",
    'd': "00001 00001 01101 10011 10001 10011 01101", 'e': "00000 00000 01110 10001 11111 10000 01110",
    'f': "00110 01001 01000 11100 01000 01000 01000", 'g': "00000 00000 01111 10001 10001 01111 00001 01110",
    'h': "10000 10000 10110 11001 10001 10001 10001", 'i': "00100 00000 01100 00100 00100 00100 01110",
    'j': "00010 00000 00110 00010 00010 00010 10010 01100", 'k': "10000 10000 10010 10100 11000 10100 10010",
    'l': "01100 00100 00100 00100 00100 00100 01110", 'm': "00000 00000 11010 10101 10101 10101 10101",
    'n': "00000 00000 10110 11001 10001 10001 10001", 'o': "00000 00000 01110 10001 10001 10001 01110",
    'p': "00000 00000 10110 11001 10001 11110 10000 10000", 'q': "00000 00000 01101 10011 10001 01111 00001 00001",
    'r': "00000 00000 10110 11001 10000 10000 10000", 's': "00000 00000 01111 10000 01110 00001 11110",
    't': "01000 01000 11100 01000 01000 01001 00110", 'u': "00000 00000 10001 10001 10001 10011 01101",
    'v': "00000 00000 10001 10001 10001 01010 00100", 'w': "00000 00000 10001 10001 10101 10101 01010",
    'x': "00000 00000 10001 01010 00100 01010 10001", 'y': "00000 00000 10001 10001 10001 01111 00001 01110",
    'z': "00000 00000 11111 00010 00100 01000 11111", '{': "00010 00100 00100 01000 00100 00100 00010",
    '|': "00100 00100 00100 00100 00100 00100 00100", '}': "01000 00100 00100 00010 00100 00100 01000",
    '~': "00000 00000 01000 10101 00010",
}
_GLYPHS = {**_DIGITS, **_REST}


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
    boxes: Tuple[Tuple[int, int], ...] = ()          # render_marks only: (number, how many of its boxes were drawn), by number
    colors: Tuple[Tuple[int, str], ...] = ()         # render_marks only: (number, the color it was drawn in as #rrggbb)
    texts: Tuple[Tuple[int, str], ...] = ()          # render_marks only: (number, the text drawn for it, after fitting the picture)
    tabs: Tuple[Tuple[int, int, int, int], ...] = ()  # where each label went (x0, y0, x1, y1), in drawing order
    tab_owners: Tuple[int, ...] = ()                 # for each tab, the index of its box in the boxes (render) or marks (render_marks) passed
    number_only: Tuple[Tuple[int, int], ...] = ()    # render_marks only: (number, boxes that drew the number because the text had no place)


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


def _text(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, g: int) -> None:
    """`text` in white from the bitmap, each bitmap pixel a g x g square, characters 6 bitmap pixels apart. The caller has
    made sure every character is in the table (digits for a mark, clean_label's output for a locator)."""
    for i, ch in enumerate(text):
        for row, bits in enumerate(_GLYPHS[ch].split()):
            for col, bit in enumerate(bits):
                if bit == "1":
                    px, py = x + (i * 6 + col) * g, y + row * g
                    draw.rectangle((px, py, px + g - 1, py + g - 1), fill=(255, 255, 255))


def _outline(draw: ImageDraw.ImageDraw, color: Tuple[int, int, int], rect: Tuple[float, float, float, float], g: int) -> None:
    x0, y0, x1, y1 = (round(v) for v in rect)
    for k in range(max(1, g)):                                       # an outline g px thick, inside the box
        draw.rectangle((x0 + k, y0 + k, max(x0 + k, x1 - 1 - k), max(y0 + k, y1 - 1 - k)), outline=color)


def _holds(outer: Sequence[int], inner: Sequence[int], tol: int = 2) -> bool:
    """Whether `outer` lies around `inner` (within `tol` px, for a border or a rounding)."""
    return outer[0] <= inner[0] + tol and outer[1] <= inner[1] + tol and outer[2] >= inner[2] - tol and outer[3] >= inner[3] - tol


def _share(a: Sequence[int], b: Sequence[int]) -> int:
    """The area two rectangles share, in px."""
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


CELL = 64                        # px a side of the grid cells that find the rectangles near a tab


class _Grid:
    """Rectangles (x0, y0, x1, y1 in px) filed under the CELL-sized cells of the picture they touch, so asking what is near a
    tab looks at the few rectangles in its cells and not at every box and tab. A rectangle touches at most (picture / CELL)^2 cells."""

    def __init__(self, size: Tuple[int, int]):
        self.size = size
        self.cells: Dict[Tuple[int, int], List[int]] = {}

    def _span(self, r: Sequence[int]) -> Tuple[range, range]:
        w, h = self.size
        return (range(max(0, min(r[0], w - 1)) // CELL, max(0, min(max(r[0], r[2] - 1), w - 1)) // CELL + 1),
                range(max(0, min(r[1], h - 1)) // CELL, max(0, min(max(r[1], r[3] - 1), h - 1)) // CELL + 1))

    def add(self, key: int, r: Sequence[int]) -> None:
        cols, rows = self._span(r)
        for cx in cols:
            for cy in rows:
                self.cells.setdefault((cx, cy), []).append(key)

    def near(self, r: Sequence[int]) -> Set[int]:
        """Keys of the rectangles that may touch `r`, each once (what is asked of them does not depend on the order)."""
        cols, rows = self._span(r)
        return {k for cx in cols for cy in rows for k in self.cells.get((cx, cy), ())}


class _Tabs:
    """Where the label tabs of one picture go. `boxes` are the rounded rectangles of every box drawn (an index is a mark's);
    a tab for box i goes in the first of the places around it that is inside the picture and covers no box and no tab placed.
    `within` loosens that for the boxes that lie around box i (a section around a field is where the field sits): their inside,
    clear of every other box, is free too. A tab never covers box i itself."""

    def __init__(self, size: Tuple[int, int], boxes: Sequence[Tuple[int, int, int, int]]):
        self.size, self.boxes = size, list(boxes)
        self.box_grid, self.tab_grid = _Grid(size), _Grid(size)
        self.placed: List[Tuple[int, int, int, int]] = []
        self._around: Dict[int, FrozenSet[int]] = {}
        for i, b in enumerate(self.boxes):
            self.box_grid.add(i, b)

    def _holders(self, i: int) -> FrozenSet[int]:
        """The boxes (other than i) that lie around box i, found once through the cell of i's centre: whatever holds a box
        covers its centre (give or take the 2 px of _holds)."""
        if i not in self._around:
            own = self.boxes[i]
            mid = ((own[0] + own[2]) // 2, (own[1] + own[3]) // 2)
            self._around[i] = frozenset(j for j in self.box_grid.near((mid[0] - 2, mid[1] - 2, mid[0] + 3, mid[1] + 3))
                                        if j != i and _holds(self.boxes[j], own))
        return self._around[i]

    def _outside(self, i: int, w: int, h: int) -> List[Tuple[int, int]]:
        """The places touching the outside of box i, nearest first: above and below (left aligned, right aligned, centred), then
        beside it (top aligned, bottom aligned). A place that does not fit the picture is left out; one that would only slide
        along the box's edge is slid, never moved onto the box."""
        x0, y0, x1, y1 = self.boxes[i]
        W, H = self.size
        along = lambda x: min(max(0, x), max(0, W - w))              # noqa: E731  (slid sideways to stay in the picture)
        beside = lambda y: min(max(0, y), max(0, H - h))             # noqa: E731
        out = [(along(x0), y0 - h), (along(x0), y1), (along(x1 - w), y0 - h), (along(x1 - w), y1),
               (x1, beside(y0)), (x0 - w, beside(y0)), (x1, beside(y1 - h)), (x0 - w, beside(y1 - h)),
               (along((x0 + x1 - w) // 2), y0 - h), (along((x0 + x1 - w) // 2), y1)]
        seen: List[Tuple[int, int]] = []
        for tx, ty in out:
            if 0 <= tx and 0 <= ty and tx + w <= W and ty + h <= H and (tx, ty) not in seen:
                seen.append((tx, ty))
        return seen

    def _clash(self, i: int, tab: Tuple[int, int, int, int], within: bool) -> bool:
        around = self._holders(i) if within else frozenset()
        for j in self.box_grid.near(tab):
            if j not in around and _overlaps(tab, self.boxes[j]):
                return True
        return any(_overlaps(tab, self.placed[k]) for k in self.tab_grid.near(tab))

    def place(self, i: int, w: int, h: int, within: bool = False) -> Optional[Tuple[int, int]]:
        """The first place outside box i for a w x h tab that covers no box (those around box i excepted when `within`) and no
        tab, or None."""
        for tx, ty in self._outside(i, w, h):
            if not self._clash(i, (tx, ty, tx + w, ty + h), within):
                return tx, ty
        return None

    def squeeze(self, i: int, w: int, h: int) -> Tuple[int, int]:
        """No clean place exists: the one that covers the least (px under the tab of other boxes and tabs, and twice that of box i
        itself; the boxes around box i do not count), the earlier of the old places (above, below, inside the top, inside the bottom,
        the same at the right end, then one tab further) winning a tie. Clamped into the picture, so the mark is always drawn."""
        x0, y0, x1, y1 = self.boxes[i]
        W, H = self.size
        tries = [(x0, y0 - h), (x0, y1), (x0, y0), (x0, y1 - h), (x1 - w, y0 - h), (x1 - w, y1), (x1 - w, y0), (x1 - w, y1 - h),
                 (x0, y0 - 2 * h), (x0, y1 + h), (x0 + w, y0 - h), (x0 + w, y1)]
        around = self._holders(i)
        best: Optional[Tuple[int, Tuple[int, int]]] = None
        for tx, ty in tries:
            tx, ty = min(max(0, tx), max(0, W - w)), min(max(0, ty), max(0, H - h))
            tab = (tx, ty, tx + w, ty + h)
            limit = best[0] if best else None                        # a place already worse than the best so far is not added up
            under = [self.boxes[j] for j in self.box_grid.near(tab) if j not in around and j != i]
            under += [self.placed[k] for k in self.tab_grid.near(tab)]
            cost = 2 * _share(tab, self.boxes[i])                    # what it points at counts double
            for other in under:
                cost += _share(tab, other)
                if limit is not None and cost >= limit:
                    break
            if best is None or cost < best[0]:
                best = (cost, (tx, ty))
        assert best is not None
        return best[1]

    def put(self, tab: Tuple[int, int, int, int]) -> None:
        self.tab_grid.add(len(self.placed), tab)
        self.placed.append(tab)


def _tab_size(label: str, k: int, pad: int, rows: int) -> Tuple[int, int]:
    """Width and height of a tab holding `label`: glyphs k px, `pad` around, `rows` glyph rows tall."""
    return len(label) * 6 * k - k + 2 * pad, rows * k + 2 * pad


def _draw_tab(draw: ImageDraw.ImageDraw, tabs: _Tabs, spot: Tuple[int, int], size: Tuple[int, int], label: str,
              color: Tuple[int, int, int], k: int, pad: int) -> Tuple[int, int, int, int]:
    """Draw `label` on a tab of `size` at `spot` in `color`, record the tab as placed and return it."""
    tab = (spot[0], spot[1], spot[0] + size[0], spot[1] + size[1])
    tabs.put(tab)
    draw.rectangle((tab[0], tab[1], tab[2] - 1, tab[3] - 1), fill=color)
    _text(draw, label, tab[0] + pad, tab[1] + pad, k)
    return tab


def _decode(png: bytes) -> "Image.Image":
    """The screenshot as an RGB image (PNG only; the header was checked by screenshot.py), or OverlayError undecodable."""
    try:
        im = Image.open(io.BytesIO(png), formats=["PNG"])
        im.load()
        return im.convert("RGB")
    except Exception:
        raise OverlayError("undecodable", "the screenshot could not be decoded") from None


def render(png: bytes, boxes: Sequence[Optional[Box]], viewport: Tuple[Optional[float], Optional[float], Optional[float]],
           scroll: Tuple[float, float] = (0.0, 0.0)) -> Overlay:
    """The marked, cropped, downscaled screenshot for the candidates `boxes` (candidate i is mark i + 1).
    `viewport` is (width, height, dpr) in CSS px and `scroll` the page's scroll offset when the screenshot was taken.
    OverlayError when no mark can be drawn (no_visible_candidate), the picture does not fit the viewport (viewport_mismatch,
    no_viewport) or the file cannot be decoded (undecodable)."""
    im = _decode(png)
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
    # the clipped part of each box, in the crop's pixels (the marked box is what a person sees of the element)
    rects = [((x0 - left) * f, (y0 - top) * f, (x1 - left) * f, (y1 - top) * f) for _, (x0, y0, x1, y1) in seen]
    for (number, _), rect in zip(seen, rects):
        _outline(draw, _PALETTE[(number - 1) % len(_PALETTE)], rect, g)
    tabs = _Tabs(size, [tuple(round(v) for v in r) for r in rects])
    placed: List[Tuple[int, int, int, int]] = []
    owners: List[int] = []
    for i, (number, _) in enumerate(seen):                           # the candidates' order: the first one gets the first free place
        text = str(number)
        dims = _tab_size(text, g, g, 7)
        spot = tabs.place(i, *dims) or tabs.place(i, *dims, within=True) or tabs.squeeze(i, *dims)
        placed.append(_draw_tab(draw, tabs, spot, dims, text, _PALETTE[(number - 1) % len(_PALETTE)], g, g))
        owners.append(seen[i][0] - 1)
    buf = io.BytesIO()
    out.save(buf, format="PNG", compress_level=6)
    return Overlay(buf.getvalue(), size[0], size[1], tuple(n for n, _ in seen), tuple(hidden),
                   tabs=tuple(placed), tab_owners=tuple(owners))


# ---- POST /v1/overlay: the whole viewport, any number of marks, a number or a line of text for each --------------------------
@dataclass(frozen=True)
class Mark:
    """One box to draw. `number` picks its color and is its label when `text` is empty; boxes of one locator share a number.
    `box` is in CSS px, document coordinates (None: the element has no box). `text` is drawn as given: pass clean_label()."""
    number: int
    box: Optional[Box]
    text: str = ""


def clean_label(text: str, limit: int = MAX_LABEL_CHARS) -> str:
    """`text` as the bitmap can draw it: ASCII control characters (and DEL) removed, any other character outside printable
    ASCII drawn as `?` (so a right-to-left mark or a look-alike letter cannot be smuggled in), surrounding blanks trimmed, and
    at most `limit` characters, the last three `...` when it was cut."""
    kept = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in text if not (ord(ch) < 32 or ord(ch) == 127)).strip()
    return _cut(kept, limit)


def _cut(text: str, n: int) -> str:
    """`text` as it is when it fits n characters, else its first n - 3 and `...` (its first n when n is 3 or less)."""
    if len(text) <= n:
        return text
    return text[:n - 3] + "..." if n > 3 else text[:max(0, n)]


def _overlaps(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _area(rect: Sequence[float]) -> float:
    return (rect[2] - rect[0]) * (rect[3] - rect[1])


def _touch(a: Sequence[float], b: Sequence[float]) -> bool:
    """Whether two boxes share area (one inside the other included)."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _snug(outer: Sequence[float], inner: Sequence[float], tol: float) -> bool:
    """Whether `inner` sits inside `outer` with every edge within `tol` of it."""
    return (inner[0] - outer[0] >= -tol and inner[1] - outer[1] >= -tol and outer[2] - inner[2] >= -tol and outer[3] - inner[3] >= -tol
            and inner[0] - outer[0] <= tol and inner[1] - outer[1] <= tol and outer[2] - inner[2] <= tol and outer[3] - inner[3] <= tol)


def _inset(rect: Sequence[float], d: float) -> Tuple[float, float, float, float]:
    """`rect` pulled in by d on every side, unless it would vanish."""
    if d <= 0 or rect[2] - rect[0] <= 2 * d + 2 or rect[3] - rect[1] <= 2 * d + 2:
        return (rect[0], rect[1], rect[2], rect[3])
    return (rect[0] + d, rect[1] + d, rect[2] - d, rect[3] - d)


MAX_COLORED_BOXES = 1500         # past this many boxes the colors are plain (a number's own), not checked against each other


def _colors(rects: Sequence[Tuple[Mark, Sequence[float]]]) -> Dict[int, int]:
    """A palette index for each number: its own (number - 1, wrapping) unless a box of it overlaps a box of a lower number that
    has that color, then the next free one, so a section and the things inside it, or two boxes on top of each other, do not
    share a color. When every color is taken around a box it keeps its own."""
    by_number: Dict[int, List[Sequence[float]]] = {}
    for m, rect in rects:
        by_number.setdefault(m.number, []).append(rect)
    n = len(_PALETTE)
    out: Dict[int, int] = {}
    check = len(rects) <= MAX_COLORED_BOXES
    for number in sorted(by_number):
        own = (number - 1) % n
        taken = {c for k, c in out.items() if any(_touch(a, b) for a in by_number[number] for b in by_number[k])} if check else set()
        out[number] = next((c for c in ((own + j) % n for j in range(n)) if c not in taken), own)
    return out


def visible_boxes(size: Tuple[int, int], boxes: Sequence[Optional[Box]],
                  viewport: Tuple[Optional[float], Optional[float], Optional[float]], scroll: Tuple[float, float] = (0.0, 0.0)
                  ) -> List[bool]:
    """For each box, whether render_marks would draw it on a picture of `size` (width, height) taken in `viewport`, without
    decoding anything. OverlayError (viewport_mismatch, no_viewport) when the picture does not fit the viewport."""
    s = _scale(size[0], size[1], viewport)
    return [_visible(b, scroll, s, size[0], size[1]) is not None for b in boxes]


def render_marks(png: bytes, marks: Sequence[Mark], viewport: Tuple[Optional[float], Optional[float], Optional[float]],
                 scroll: Tuple[float, float] = (0.0, 0.0), labels: str = "numbers") -> Overlay:
    """The whole screenshot, downscaled to at most OVERLAY_LONG_SIDE, with every visible mark drawn: an outline in the color of
    its number and a tab above, below or inside it holding the number (labels "numbers") or the mark's `text` (labels
    "locators"; a mark with no text keeps its number). `drawn` lists the numbers that got a box on the picture and
    `not_visible` those that got none (no box, or mostly outside the screenshot). Nothing visible is not an error: the picture
    comes back unmarked. OverlayError for a picture that does not fit its viewport (viewport_mismatch, no_viewport) or cannot
    be decoded (undecodable)."""
    im = _decode(png)
    w, h = im.size
    s = _scale(w, h, viewport)
    seen: List[Tuple[Mark, Tuple[float, ...]]] = []
    owner_of: List[int] = []                                         # the index of each seen mark among `marks`
    missed: List[int] = []
    for n, mark in enumerate(marks):
        part = _visible(mark.box, scroll, s, w, h)
        if part:
            seen.append((mark, part))
            owner_of.append(n)
        else:
            missed.append(mark.number)
    f = min(1.0, OVERLAY_LONG_SIDE / max(w, h))
    size = (max(1, round(w * f)), max(1, round(h * f)))
    out = im.resize(size, Image.Resampling.LANCZOS) if size != im.size else im
    draw = ImageDraw.Draw(out)
    g = max(2, round(max(size) / 500))                               # a digit's pixel; the text's is smaller (t)
    t = max(1, round(max(size) / 800))
    rects = [(m, tuple(v * f for v in part)) for m, part in seen]
    colors = _colors(rects)
    # biggest first: a section is drawn under the things it holds, and gets the first free place for its tab
    order = sorted(range(len(rects)), key=lambda i: (-_area(rects[i][1]), rects[i][0].number, i))
    outer: List[Tuple[float, ...]] = []
    for i in order:
        m, rect = rects[i]
        # a box that sits just inside another (a button around its own text) is drawn one outline's width further in
        depth = sum(1 for o in outer if _snug(o, rect, 2 * g)) if len(rects) <= MAX_COLORED_BOXES else 0
        _outline(draw, _PALETTE[colors[m.number]], _inset(rect, depth * g), g)
        outer.append(rect)
    tabs = _Tabs(size, [tuple(round(v) for v in r) for _, r in rects])
    placed: List[Tuple[int, int, int, int]] = []
    owners: List[int] = []
    texts: Dict[int, str] = {}
    number_only: Dict[int, int] = {}
    for i in order:
        m, rect = rects[i]
        color = _PALETTE[colors[m.number]]
        label = clean_label(m.text) if labels == "locators" else ""      # whatever the caller passed, only drawable text is drawn
        spot = None
        if label:
            k, pad, rows = t, t + 1, 8                               # a line of text may carry a descender
            label = _cut(label, max(1, (size[0] - 2 * pad + k) // (6 * k)))      # never wider than the picture
            dims = _tab_size(label, k, pad, rows)
            spot = tabs.place(i, *dims)
            if spot:
                texts.setdefault(m.number, label)
            else:                                                    # no place outside for the line: the number, the legend has the text
                number_only[m.number] = number_only.get(m.number, 0) + 1
        if spot is None:
            label, k, pad, rows = str(m.number), g, g, 7
            dims = _tab_size(label, k, pad, rows)
            spot = tabs.place(i, *dims) or tabs.place(i, *dims, within=True) or tabs.squeeze(i, *dims)
        placed.append(_draw_tab(draw, tabs, spot, dims, label, color, k, pad))
        owners.append(owner_of[i])
    buf = io.BytesIO()
    out.save(buf, format="PNG", compress_level=6)
    per_number: Dict[int, int] = {}
    for m, _ in seen:
        per_number[m.number] = per_number.get(m.number, 0) + 1
    drawn = sorted(per_number)
    return Overlay(buf.getvalue(), size[0], size[1], tuple(drawn), tuple(sorted(set(missed) - set(drawn))),
                   tuple(sorted(per_number.items())), tuple((n, "#%02x%02x%02x" % _PALETTE[c]) for n, c in sorted(colors.items())),
                   tuple(sorted(texts.items())), tuple(placed), tuple(owners), tuple(sorted(number_only.items())))


__all__ = ["Overlay", "OverlayError", "Mark", "render", "render_marks", "visible_boxes", "clean_label", "LONG_SIDE", "MIN_CROP",
           "OVERLAY_LONG_SIDE", "MAX_LABEL_CHARS"]
