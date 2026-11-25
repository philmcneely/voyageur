"""
===============================================================================
Request and Response Schemas (what crosses the API, strictly)
===============================================================================
The shapes of POST /v1/heal (the failure, the source files from its stack, the page inventory, the last passing run's
fingerprint), of the answer (a candidate to try, or a decision) and of the result a client reports after it re-runs. They
are pydantic v2 models that refuse unknown fields, so a client typo is an error and not a silently ignored setting.

The service is a pure function of what it is sent, so this module is also where the request is made safe to read:
    - limits are checked before anything is parsed: 10 MB per request, 20 files, 256 KB per file, 500 inventory entries,
      an aria snapshot of at most 256 KB. Over a limit is a LimitExceeded (HTTP 413); nothing is ever cut short silently
    - a request that carries a `value` key anywhere, or a cookie or storage dump, is refused (HTTP 422) without echoing it:
      the page must be described by its attributes and copy, never by what a person typed or a session holds
    - the aria snapshot is accepted only with the text of textbox, searchbox, spinbutton, slider and combobox lines removed
      (password fields included; role, name and states such as [checked] or [disabled] stay)
      (`- textbox "Email": jane@example.com` keeps no `: jane@example.com`)
    - source paths are relative, inside the roots the client names, and the client's own hash of a file must match

Features:
    ✓ Inventory entries accepted in the probe's own shape (flat keys, width/height boxes, data_* attributes) and in the
      spec's shape (attrs bag, w/h boxes, visible / enabled); `flat()` turns either into the one dict the matching code reads
    ✓ Entries without a ref are numbered e<position>; a ref, when given, must read e<digits>
    ✓ Page URL kept as its path only (a query or fragment can carry a token)
    ✓ `page.screenshot` is null or {media_type: image/png, data: base64, width, height}: PNG only, checked in layers before any
      pixel is decoded (screenshot.py: over 4 MB decoded is a 413; bad base64, not a PNG, a side over 8192 px, over 16 million
      pixels or dimensions that do not match the PNG are a 422). A screenshot with `options.vision` off is a 422: it means the
      client and the operator disagree about what leaves the machine
    ✓ `page.viewport.scroll` (optional, CSS px) places the inventory's document-coordinate boxes on a viewport screenshot
    ✓ Responses are models too, so what leaves the service is as strict as what comes in
    ✓ Spans are character offsets (code points) into the file content exactly as the client sent it
    ✓ options.mode "advise" asks for the decision and the best locator (an Advice) with no patch and no try rounds
    ✓ AuditRequest (POST /v1/audit) and FollowupRequest (POST /v1/heals/{id}/followup) under the same limits and refusals
    ✓ OverlayRequest (POST /v1/overlay) is a page with its screenshot (required), optional locators (1 to 100, each up to 4000
      characters; 413 over 100) and options {labels: numbers | locators, kinds: interactive | text, only with no locators};
      there is no vision option, since nothing goes to a model. OverlayResponse is the marked PNG (shaped like a screenshot)
      and the legend
    ✓ DiagnoseRequest (POST /v1/diagnose) is a heal request plus the heals an earlier run applied (`prior_heals`, at most 20, each
      a heal id, a relative path, a line and the locator before and after); DiagnoseResponse names a cause from a closed list,
      with evidence, a confidence and at most one suggested edit that is never an applicable Patch (`applied` is always false)

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import hashlib
import unicodedata
import json
import math
import re
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit

from .bounds import (MAX_ARIA_LINE_CHARS, MAX_ERROR_CHARS, MAX_FIELD_CHARS, MAX_LINE_CHARS, lines_ok)
from .redact import redact_typed_values
from .screenshot import MAX_BASE64_CHARS, MAX_BYTES, MAX_SIDE, check_png
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StrictBool, ValidationError, model_validator

LIMITS = {"request_bytes": 10 * 1024 * 1024, "files": 20, "file_bytes": 256 * 1024, "source_bytes": 1024 * 1024,
          "inventory": 500, "aria_chars": 256 * 1024}
MAX_OVERLAY_LOCATORS = 100        # locators one POST /v1/overlay may name
MAX_OVERLAY_EXPR = 4000           # characters of one of them

# keys that mean the request carries what a person typed or what a session holds
_FORBIDDEN_KEYS = frozenset({"value", "cookie", "cookies", "set-cookie", "storage", "storage_state", "storagestate",
                             "localstorage", "local_storage", "sessionstorage", "session_storage", "indexeddb"})


class LimitExceeded(ValueError):
    """The request is over a hard limit (HTTP 413). The message names the limit, never the content."""


class RefusedRequest(ValueError):
    """The request is not acceptable as sent (HTTP 422): not JSON, or it carries values, cookies or storage."""


# ---- checks that run on the raw JSON, before any model sees it -------------------------------------------------------
def check_limits(raw: Any) -> None:
    """LimitExceeded when the decoded request is over the file, file-size, inventory or aria limits."""
    if not isinstance(raw, dict):
        return
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    files = source.get("files") if isinstance(source.get("files"), list) else []
    if len(files) > LIMITS["files"]:
        raise LimitExceeded(f"more than {LIMITS['files']} files")
    total = 0
    for f in files:
        content = f.get("content") if isinstance(f, dict) else None
        if isinstance(content, str):
            size = len(content.encode("utf-8", "replace"))
            if size > LIMITS["file_bytes"]:
                raise LimitExceeded(f"a file is over {LIMITS['file_bytes'] // 1024} KB")
            total += size
    if total > LIMITS["source_bytes"]:
        raise LimitExceeded(f"the source files add up to more than {LIMITS['source_bytes'] // 1024} KB")
    page = raw.get("page") if isinstance(raw.get("page"), dict) else {}
    inventory = page.get("inventory")
    if isinstance(inventory, list) and len(inventory) > LIMITS["inventory"]:
        raise LimitExceeded(f"more than {LIMITS['inventory']} inventory entries")
    aria = page.get("aria_snapshot")
    if isinstance(aria, str) and len(aria) > LIMITS["aria_chars"]:
        raise LimitExceeded(f"the aria snapshot is over {LIMITS['aria_chars'] // 1024} KB")
    if isinstance(aria, str) and not lines_ok(aria, MAX_ARIA_LINE_CHARS):
        raise LimitExceeded(f"an aria snapshot line is over {MAX_ARIA_LINE_CHARS // 1024} KB")
    locators = raw.get("locators")
    if isinstance(locators, list) and len(locators) > MAX_OVERLAY_LOCATORS:
        raise LimitExceeded(f"more than {MAX_OVERLAY_LOCATORS} locators")
    shot = page.get("screenshot")
    data = shot.get("data") if isinstance(shot, dict) else None
    if isinstance(data, str) and len(data) > MAX_BASE64_CHARS:
        raise LimitExceeded(f"the screenshot is over {MAX_BYTES // (1024 * 1024)} MB")


def refuse_secrets(raw: Any) -> None:
    """RefusedRequest when any dict key anywhere in `raw` is `value` or names a cookie or storage dump. Iterative, so a
    deeply nested body cannot exhaust the stack; the message names the key, never its content."""
    stack = [raw]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(key, str) and key.lower() in _FORBIDDEN_KEYS:
                    raise RefusedRequest(f"the request carries a '{key.lower()}' key: input values, cookies and "
                                         f"storage are never accepted")
                stack.append(child)
        elif isinstance(node, list):
            stack.extend(node)


def strip_aria_values(text: str) -> str:
    """The aria snapshot without the typed text of its input lines (redact.redact_typed_values): `- textbox "Email": a@b.c`
    becomes `- textbox "Email"`; every other line is left as it is."""
    return redact_typed_values(text)


# ---- request models --------------------------------------------------------------------------------------------------
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


COORD_MAX = 1e7                  # CSS px: no page, box or scroll is larger, and a larger number only breeds overflow


class Box(_Strict):
    x: float = Field(allow_inf_nan=False, ge=-COORD_MAX, le=COORD_MAX)
    y: float = Field(allow_inf_nan=False, ge=-COORD_MAX, le=COORD_MAX)
    width: float = Field(allow_inf_nan=False, ge=-COORD_MAX, le=COORD_MAX)
    height: float = Field(allow_inf_nan=False, ge=-COORD_MAX, le=COORD_MAX)

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            for short, long in (("w", "width"), ("h", "height")):
                if short in data and long not in data:
                    data[long] = data.pop(short)
        return data

    @model_validator(mode="after")
    def _finite(self) -> "Box":
        if not all(math.isfinite(v) for v in (self.x, self.y, self.width, self.height)):
            raise ValueError("a box must hold finite numbers")
        return self

    def as_dict(self) -> Dict[str, float]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


_ATTR_KEYS = {"data-testid": "testid", "name": "name_attr", "aria-label": "aria_label", "id": "id", "type": "type",
              "placeholder": "placeholder", "title": "title", "alt": "alt", "role": "role", "class": "class"}
_DATA_KEY = re.compile(r"^data_[a-z0-9_]{1,60}$")


def _attr_key(name: str) -> Optional[str]:
    """The inventory key an HTML attribute is listed under (data-qa -> data_qa); None for one the inventory ignores."""
    lower = name.lower()
    if lower in _ATTR_KEYS:
        return _ATTR_KEYS[lower]
    if lower.startswith("data-"):
        return lower.replace("-", "_")
    return None


_STR_FIELDS = ("tag", "type", "role", "name", "aria_label", "label", "placeholder", "id", "testid", "data_test", "data_qa",
               "data_cy", "name_attr", "for_attr", "title", "alt", "text", "row", "row_attr", "frame", "shadow")


def _flatten(raw: Any) -> Any:
    """Normalise one element description (an inventory entry or a fingerprint) to flat keys: nulls dropped, the attrs bag
    spread into its keys, w/h boxes spelled width/height, visible / enabled as hidden / disabled."""
    if not isinstance(raw, dict):
        return raw
    d = {k: v for k, v in raw.items() if v is not None}
    attrs = d.pop("attrs", None)
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            key = _attr_key(k) if isinstance(k, str) else None
            if key and isinstance(v, str) and key not in d:
                d[key] = v
    if "shadow_host" in d:
        d.setdefault("shadow", d.pop("shadow_host"))
    if "visible" in d:
        visible = d.pop("visible")
        d.setdefault("hidden", not visible if isinstance(visible, bool) else visible)
    if "enabled" in d:
        enabled = d.pop("enabled")
        d.setdefault("disabled", not enabled if isinstance(enabled, bool) else enabled)
    return d


class _Element(_Strict):
    """The facts both an inventory entry and a fingerprint carry."""
    tag: str = Field("", max_length=64)
    type: str = Field("", max_length=MAX_FIELD_CHARS)
    role: str = Field("", max_length=MAX_FIELD_CHARS)
    name: str = Field("", max_length=MAX_FIELD_CHARS)
    label: str = Field("", max_length=MAX_FIELD_CHARS)
    placeholder: str = Field("", max_length=MAX_FIELD_CHARS)
    text: str = Field("", max_length=MAX_FIELD_CHARS)
    row: str = Field("", max_length=MAX_FIELD_CHARS)
    row_attr: Literal["", "data-qa", "data-testid", "data-test", "data-cy", "id"] = ""   # the attribute `row` came from


class InventoryItem(_Element):
    """One element of the page, as the probe lists it (or as the spec writes it). Unknown keys are refused, except the
    data_<name> attributes the probe spells from data-* attributes (a short string each)."""
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    ref: Optional[str] = Field(None, pattern=r"^e\d+$")
    aria_label: str = Field("", max_length=MAX_FIELD_CHARS)
    id: str = Field("", max_length=MAX_FIELD_CHARS)
    testid: str = Field("", max_length=MAX_FIELD_CHARS)
    data_test: str = Field("", max_length=MAX_FIELD_CHARS)
    data_qa: str = Field("", max_length=MAX_FIELD_CHARS)
    data_cy: str = Field("", max_length=MAX_FIELD_CHARS)
    class_: str = Field("", alias="class", max_length=MAX_FIELD_CHARS)
    name_attr: str = Field("", max_length=MAX_FIELD_CHARS)
    for_attr: str = Field("", max_length=MAX_FIELD_CHARS)     # a label's `for`: the id of the control it names
    title: str = Field("", max_length=MAX_FIELD_CHARS)
    alt: str = Field("", max_length=MAX_FIELD_CHARS)
    frame: str = Field("", max_length=MAX_FIELD_CHARS)
    shadow: str = Field("", max_length=MAX_FIELD_CHARS)
    disabled: StrictBool = False
    hidden: StrictBool = False
    copy_wrap: StrictBool = False                  # its whole copy is that of a listed element inside it: getByText matches the inner one
    copy_cut: StrictBool = False                   # on a copy_only entry: the probe left copy-holding elements out (its `copy_more`)
    copy_only: StrictBool = False                  # listed only because it holds copy: no hook, role or control tag made it a candidate
    top_at_center: Optional[StrictBool] = None     # the probe's elementFromPoint at the box centre landed on this element
    pw_text: Optional[str] = Field(None, max_length=MAX_FIELD_CHARS)   # the text has_text reads; None from an older probe
    pw_cut: StrictBool = False                     # pw_text is incomplete: the text cannot be said NOT to hold a string
    box: Optional[Box] = None

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: Any) -> Any:
        d = _flatten(data)
        if isinstance(d, dict) and "hit" in d:                 # `hit` is the spelling of the same fact
            d = dict(d)
            hit = d.pop("hit")
            d.setdefault("top_at_center", hit)
        return d

    @model_validator(mode="after")
    def _extras_are_data_attributes(self) -> "InventoryItem":
        for key, value in (self.model_extra or {}).items():
            if not _DATA_KEY.match(key) or not isinstance(value, str) or len(value) > MAX_FIELD_CHARS:
                raise ValueError("an inventory entry may carry only the keys of the probe's listing")
        return self

    def flat(self, index: int) -> Dict[str, Any]:
        """The dict the matching code reads (inventory.Inventory): present string facts only, the hidden / disabled flags
        when set, the box as x / y / width / height, the data_* attributes, and the ref (e<index> when none was given)."""
        out: Dict[str, Any] = {"ref": self.ref or f"e{index}"}
        for key in _STR_FIELDS:
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.class_:
            out["class"] = self.class_
        out.update({k: v for k, v in (self.model_extra or {}).items() if v})
        if self.hidden:
            out["hidden"] = True
        if self.disabled:
            out["disabled"] = True
        if self.copy_only:
            out["copy_only"] = True
        if self.copy_cut:
            out["copy_cut"] = True
        if self.copy_wrap:
            out["copy_wrap"] = True
        if self.top_at_center is not None:
            out["top_at_center"] = self.top_at_center
        if self.pw_text is not None:
            out["pw_text"] = self.pw_text                  # kept when empty: "" is a text, None is no probe support
        if self.pw_cut:
            out["pw_cut"] = True
        if self.box:
            out["box"] = self.box.as_dict()
        return out


class Fingerprint(_Element):
    frame: str = Field("", max_length=MAX_FIELD_CHARS)
    """What the last passing run recorded about the element a locator hit: text and attributes and a box, never a value."""
    attrs: Dict[str, str] = Field(default_factory=dict)
    box: Optional[Box] = None

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("attrs"), dict) and len(data["attrs"]) > 40:
            raise ValueError("a fingerprint holds at most 40 attributes")
        if isinstance(data, dict) and isinstance(data.get("attrs"), dict):
            for k, v in data["attrs"].items():            # never cut and then compared: over the cap is refused
                if len(str(k)) > 100 or (isinstance(v, str) and len(v) > MAX_FIELD_CHARS):
                    raise ValueError(f"a fingerprint attribute is over {MAX_FIELD_CHARS} characters")
        return data

    def flat(self) -> Dict[str, Any]:
        raw: Dict[str, Any] = {k: getattr(self, k) for k in _STR_FIELDS if hasattr(self, k)}
        for k, v in self.attrs.items():
            key = _attr_key(k)
            if key and isinstance(v, str) and v:
                raw.setdefault(key, v)
        out = {k: v for k, v in raw.items() if v}
        if self.box:
            out["box"] = self.box.as_dict()
        return out


class Client(_Strict):
    framework: str = Field(max_length=100)
    language: Literal["python", "typescript"]
    version: str = Field("", max_length=50)


class TestInfo(_Strict):
    __test__ = False                       # not a pytest class
    id: str = Field(max_length=500)
    title: str = Field("", max_length=500)


class StackFrame(_Strict):
    file: str = Field(max_length=300)
    line: int = Field(ge=0)


class Failure(_Strict):
    error: str = Field(max_length=MAX_ERROR_CHARS)
    stack: List[StackFrame] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _lines_are_bounded(self) -> "Failure":
        if not lines_ok(self.error, MAX_LINE_CHARS):
            raise ValueError(f"a line of the error text is over {MAX_LINE_CHARS // 1024} KB")
        return self


def _safe_path(path: str) -> bool:
    """A relative path inside the repository: no '..', no backslash, and no control character (a newline or an escape
    code would travel into the record, the prompt and the patch header)."""
    return bool(path) and len(path) <= 300 and not path.startswith("/") and "\\" not in path \
        and not any(unicodedata.category(c) == "Cc" for c in path) and ".." not in path.split("/")


class SourceFile(_Strict):
    path: str
    sha256: Optional[str] = Field(None, pattern=r"^[0-9a-fA-F]{64}$")
    content: str

    @property
    def digest(self) -> str:
        """sha256 of the content as UTF-8: the hash a patch to this file is based on."""
        return hashlib.sha256(self.content.encode("utf-8", "replace")).hexdigest()

    @model_validator(mode="after")
    def _checked(self) -> "SourceFile":
        if not _safe_path(self.path):
            raise ValueError("a source path must be relative, inside the repository and free of '..'")
        if self.sha256 and self.sha256.lower() != self.digest:
            raise ValueError("a file's sha256 does not match its content")
        return self


class Source(_Strict):
    roots: List[str] = Field(default_factory=list, max_length=20)
    files: List[SourceFile] = Field(default_factory=list)

    @model_validator(mode="after")
    def _under_roots(self) -> "Source":
        paths = [f.path for f in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("a source path is listed twice")
        roots = [r.strip("/") for r in self.roots]
        if any(not (r in ("", ".") or _safe_path(r)) for r in roots):
            raise ValueError("a root must be a relative path")
        if roots and not any(r in ("", ".") for r in roots):
            for f in self.files:
                if not any(f.path.startswith(r + "/") for r in roots):
                    raise ValueError("a source file is outside source.roots")
        return self


class PageLanguage(_Strict):
    html_lang: Optional[str] = Field(None, max_length=64)
    browser_locale: Optional[str] = Field(None, max_length=64)
    content_language: Optional[str] = Field(None, max_length=200)

    def state(self) -> Dict[str, Optional[str]]:
        """The record locator_policy.page_language reads: <html lang>, then the browser locale, then Content-Language."""
        return {"lang": self.html_lang, "browser_lang": self.browser_locale, "content_language": self.content_language}


class Scroll(_Strict):
    x: float = Field(0, ge=0, le=COORD_MAX, allow_inf_nan=False)
    y: float = Field(0, ge=0, le=COORD_MAX, allow_inf_nan=False)


class Viewport(_Strict):
    w: Optional[float] = Field(None, ge=0, le=COORD_MAX, allow_inf_nan=False)
    h: Optional[float] = Field(None, ge=0, le=COORD_MAX, allow_inf_nan=False)
    dpr: Optional[float] = Field(None, gt=0, le=10, allow_inf_nan=False)
    scroll: Optional[Scroll] = None               # CSS px the page was scrolled when the screenshot was taken (default 0, 0)


class Screenshot(_Strict):
    """One viewport screenshot: PNG bytes as base64, and the PNG's own width and height in device pixels."""
    media_type: Literal["image/png"]
    data: str = Field(repr=False)             # image bytes never appear in a repr, a log line or an error
    width: int = Field(ge=1, le=MAX_SIDE)
    height: int = Field(ge=1, le=MAX_SIDE)
    _png: bytes = PrivateAttr(default=b"")

    @model_validator(mode="after")
    def _is_a_png(self) -> "Screenshot":
        self._png = check_png(self.data, self.width, self.height)    # ValueError names the rule, never the content
        return self

    def png(self) -> bytes:
        """The decoded, checked PNG bytes."""
        return self._png


class Page(_Strict):
    url: str = Field(max_length=2000)
    language: PageLanguage = Field(default_factory=PageLanguage)
    aria_snapshot: Optional[str] = None
    inventory: List[InventoryItem] = Field(default_factory=list)
    inventory_more: int = Field(0, ge=0)          # entries the probe left out (its `more`): the listing is not complete
    inventory_copy_withheld: Optional[int] = Field(None, ge=0, le=10**6)       # None: the client sent none. # visible copy its filters kept out of the listing (probe copy_withheld)
    inventory_copy_withheld_data: Optional[int] = Field(None, ge=0, le=10**6)  # of those, inside a control or a data widget (copy_withheld_data)
    inventory_copy_more: int = Field(0, ge=0, le=10**6)     # copy-holding elements it left out (its `copy_more`): no text count is certain
    viewport: Optional[Viewport] = None
    screenshot: Optional[Screenshot] = None

    @model_validator(mode="after")
    def _strip_values(self) -> "Page":
        refs = [i.ref for i in self.inventory if i.ref]
        if len(refs) != len(set(refs)):
            raise ValueError("inventory refs must be unique")
        if self.aria_snapshot:
            self.aria_snapshot = strip_aria_values(self.aria_snapshot)
        return self

    @property
    def path(self) -> str:
        """The URL's path only: a query or fragment can carry a token."""
        try:
            return (urlsplit(self.url).path or "/")[:200]
        except ValueError:
            return "/"

    def flat_inventory(self) -> List[Dict[str, Any]]:
        return [item.flat(i) for i, item in enumerate(self.inventory)]


class LastPass(_Strict):
    fingerprint: Fingerprint
    neighbours: List[Fingerprint] = Field(default_factory=list, max_length=100)   # other elements recorded on the page
    language: Optional[str] = Field(None, max_length=64)


class Options(_Strict):
    mode: Literal["heal", "advise"] = "heal"        # advise: the decision and the best locator, no patch and no try rounds
    vision: Literal["off", "auto", "on"] = "off"
    max_rounds: int = Field(3, ge=1, le=5)
    locale_branching: bool = True


class RunInfo(_Strict):
    id: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    repo: Optional[str] = Field(None, max_length=200)


class HealRequest(_Strict):
    client: Client
    test: TestInfo
    failure: Failure
    source: Source
    page: Page
    last_pass: Optional[LastPass] = None
    options: Options = Field(default_factory=Options)
    test_id_attribute: str = Field("data-testid", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    run: Optional[RunInfo] = None

    @model_validator(mode="after")
    def _no_image_when_vision_is_off(self) -> "HealRequest":
        if self.page.screenshot is not None and self.options.vision == "off":
            raise ValueError("page.screenshot is set but options.vision is off: no image is accepted with vision off")
        return self


class PriorHeal(_Strict):
    """A heal an earlier run applied to the code under test, as the client remembers it: which record, where, what changed."""
    heal_id: str = Field(pattern=r"^h_[0-9a-f]{24}$")
    file: str = Field(max_length=300)
    line: int = Field(ge=1, le=10**7)
    from_: str = Field(alias="from", max_length=500)
    to: str = Field(max_length=500)
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _path(self) -> "PriorHeal":
        if not _safe_path(self.file):
            raise ValueError("a prior heal's file must be a relative path inside the repository")
        return self


class DiagnoseRequest(HealRequest):
    """POST /v1/diagnose: the body of a heal request (same limits, refusals and redaction) plus the heals applied before."""
    prior_heals: List[PriorHeal] = Field(default_factory=list, max_length=20)


class AuditRequest(_Strict):
    """POST /v1/audit: the page inventory and the source files to audit, as a heal request carries them but with no
    failure: every locator call in the files is ranked against the inventory. No model is called."""
    client: Optional[Client] = None
    source: Source
    page: Page
    test_id_attribute: str = Field("data-testid", pattern=r"^[a-z][a-z0-9-]{0,63}$")


class OverlayOptions(_Strict):
    labels: Literal["numbers", "locators"] = "numbers"      # what is drawn beside each box: its number, or the locator's text
    # with no locators: which listed elements to mark. interactive: controls and anything a handler acts on; text: headings,
    # labels, paragraphs, status text, sections and landmarks. None (the default) marks both
    kinds: Optional[List[Literal["interactive", "text"]]] = Field(None, min_length=1, max_length=2)


class OverlayRequest(_Strict):
    """POST /v1/overlay: a screenshot of the page with its inventory and, optionally, the locators to mark on it. Locators are
    expressions as written in a test (`page.get_by_test_id("login")`), at most MAX_OVERLAY_LOCATORS; with none, every visible
    element is marked and labelled with the locator the service would write for it. Nothing is healed and no model is called, so
    no `options.vision` exists: the screenshot is only ever drawn on and returned."""
    client: Optional[Client] = None
    page: Page
    locators: Optional[List[str]] = Field(None, min_length=1)
    options: OverlayOptions = Field(default_factory=OverlayOptions)
    test_id_attribute: str = Field("data-testid", pattern=r"^[a-z][a-z0-9-]{0,63}$")

    @model_validator(mode="after")
    def _checked(self) -> "OverlayRequest":
        if self.page.screenshot is None:
            raise ValueError("page.screenshot is required: an overlay is drawn on it")
        if self.locators is not None and self.options.kinds is not None:
            raise ValueError("options.kinds filters the elements marked when no locators are sent; it cannot be combined with locators")
        for expr in self.locators or []:
            if not expr.strip() or len(expr) > MAX_OVERLAY_EXPR:
                raise ValueError(f"a locator must be 1 to {MAX_OVERLAY_EXPR} characters")
            try:
                expr.encode("utf-8")                         # the legend echoes it: a lone surrogate could not be sent back
            except UnicodeEncodeError:
                raise ValueError("a locator must be text that UTF-8 can encode") from None
        return self


def _decode(body: Any) -> Dict[str, Any]:
    """The JSON object in `body` (bytes, text or an already decoded dict) under the size, limit and refusal rules, in
    this order: size, JSON, limits (LimitExceeded), refused keys (RefusedRequest)."""
    if isinstance(body, (bytes, bytearray, str)):
        if len(body) > LIMITS["request_bytes"]:
            raise LimitExceeded(f"the request is over {LIMITS['request_bytes'] // (1024 * 1024)} MB")
        try:
            raw = json.loads(body)
        except (ValueError, RecursionError):
            raise RefusedRequest("the request body is not valid JSON") from None
    else:
        raw = body
        try:                                          # an already decoded body (the MCP tools): the same total size limit
            size = len(json.dumps(raw, default=str))
        except (TypeError, ValueError, RecursionError):
            raise RefusedRequest("the request body is not plain JSON data") from None
        if size > LIMITS["request_bytes"]:
            raise LimitExceeded(f"the request is over {LIMITS['request_bytes'] // (1024 * 1024)} MB")
    if not isinstance(raw, dict):
        raise RefusedRequest("the request body must be a JSON object")
    check_limits(raw)
    refuse_secrets(raw)
    return raw


def parse_audit_request(body: Any) -> AuditRequest:
    """An AuditRequest from the raw body, under the same limits, refusals and redaction as a heal request."""
    return AuditRequest.model_validate(_decode(body))


def parse_overlay_request(body: Any) -> OverlayRequest:
    """An OverlayRequest from the raw body, under the same limits, refusals, redaction and screenshot checks as a heal request."""
    return OverlayRequest.model_validate(_decode(body))


def parse_diagnose_request(body: Any) -> DiagnoseRequest:
    """A DiagnoseRequest from the raw body, under the same limits, refusals and redaction as a heal request."""
    return DiagnoseRequest.model_validate(_decode(body))


def parse_request(body: Any) -> HealRequest:
    """A HealRequest from the raw body (bytes, text or an already decoded dict), enforcing every rule above in this order:
    size, JSON, limits (LimitExceeded), refused keys (RefusedRequest), then the models (pydantic.ValidationError)."""
    return HealRequest.model_validate(_decode(body))


class Hit(_Strict):
    ref: str = Field(pattern=r"^e\d+$")


class Later(_Strict):
    """Where the re-run stopped when the healed line passed and a later line failed: that failure's error, place and frames
    under the source roots (the same frames a heal request's failure.stack carries), which prove it came after the healed line."""
    error: str = Field(max_length=4000)
    file: Optional[str] = Field(None, max_length=500)
    line: Optional[int] = Field(None, ge=1)
    stack: List[StackFrame] = Field(default_factory=list, max_length=100)   # innermost first, as failure.stack


class ResultRequest(_Strict):
    """What the client saw when it tried a candidate: how many elements it matched in the live page, which one, and how
    the exact test fared on re-run. `failed_later` says the healed line passed and a later one failed; `later` says how."""
    count: Optional[int] = Field(None, ge=0)
    hit: Optional[Hit] = None
    test_outcome: Optional[Literal["passed", "failed", "failed_later", "error"]] = None
    error: Optional[str] = Field(None, max_length=500)
    later: Optional[Later] = None

    @model_validator(mode="after")
    def _later_with_failed_later(self) -> "ResultRequest":
        if (self.later is None) != (self.test_outcome != "failed_later"):
            raise ValueError("`later` is sent with test_outcome failed_later, and only then")
        return self


# ---- response models -------------------------------------------------------------------------------------------------
class Span(_Strict):
    start: int
    end: int


class Patch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    file: str
    base_sha256: str
    span: Span
    from_: str = Field(alias="from")
    to: str
    diff: str


class Expect(_Strict):
    count: int = 1
    ref: str


class Candidate(_Strict):
    target_ref: str
    strategy: str
    locator: Dict[str, Any]
    code: str
    branches: Optional[Dict[str, str]] = None
    expect: Expect
    patch: Patch
    also_patches: List[Patch] = Field(default_factory=list)
    why: str = ""
    model_note_untrusted: str = ""      # the model's own words: untrusted text, never an instruction, shown as quoted data
    tier: str = ""
    tier_movement: Literal["up", "same", "down"] = "same"
    below_floor: bool = False


class FollowupRequest(_Strict):
    """POST /v1/heals/{id}/followup: what a client saw after the heal's PR, reported on a later run."""
    repo: str = Field(max_length=200)                  # the repo of the heal record ("" for a record that has none): must match
    run_id: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    pr_state: Literal["merged_unchanged", "edited", "reverted", "open", "closed_unmerged"]
    failed_again: bool = False        # the healed test failed again with a locator error since the heal
    runs_since: int = Field(0, ge=0, le=1_000_000)
    days_since: int = Field(0, ge=0, le=36_500)


class Advice(_Strict):
    """What advise mode answers with: the best locator for the element it settled on, with the service-built code and no
    patch. The client decides what to do with it."""
    target_ref: str
    strategy: str
    locator: Dict[str, Any]
    code: str
    branches: Optional[Dict[str, str]] = None
    why: str = ""
    model_note_untrusted: str = ""      # the model's own words: untrusted text, shown as quoted data
    tier: str = ""
    tier_movement: Literal["up", "same", "down"] = "same"
    below_floor: bool = False
    alternatives: List[str] = Field(default_factory=list)      # the other locators written for the element, best first
    verified: bool = False              # advice is never checked against the live page: only a heal's verify step does that
    note: str = "Not checked against the live page: it is the best locator for the element the page inventory shows. " \
                "Check count 1 and the right element before using it."


class ModelUsage(_Strict):
    role: str
    model: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    images: int = 0


class Usage(_Strict):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    images: int = 0
    by_model: List[ModelUsage] = Field(default_factory=list)


Cause = Literal["stale_expectation", "app_bug", "timing", "test_data_or_env", "earlier_heal_wrong_element", "flow_changed",
                "unknown"]


class SuggestedPatch(BaseModel):
    """One edit the model proposed and the service checked (it applies cleanly to the file as sent). Deliberately not a Patch:
    no client applies it as a heal, and `applied` is always false."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    file: str
    line: int
    from_: str = Field(alias="from")
    to: str
    why: str = ""
    base_sha256: str
    span: Span
    diff: str
    applied: Literal[False] = False


class Diagnosis(_Strict):
    """What the service says about a failure that is not locator drift. `evidence_untrusted` and `model_note_untrusted` are the
    model's own words (URLs reduced to their host): untrusted text, shown as quoted data. `downgraded` says why the service changed the model's answer."""
    cause: Cause
    evidence_untrusted: List[str] = Field(default_factory=list, max_length=6)
    confidence: Literal["low", "medium", "high"]
    revert_heal_id: Optional[str] = None            # only for earlier_heal_wrong_element, and only when the gate passed
    suggested_patch: Optional[SuggestedPatch] = None
    model_note_untrusted: str = ""
    downgraded: Optional[str] = None
    verified: bool = False                          # a diagnosis is never checked against the live page, like advice


class DiagnoseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    heal_id: str
    status: Literal["diagnosed", "needs_human", "not_applicable"]
    diagnosis: Optional[Diagnosis] = None
    reason: Optional[str] = None
    why: Optional[str] = None
    usage: Usage = Field(default_factory=Usage)
    cache_hit: bool = False

    def to_json(self) -> Dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


class OverlayImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_type: Literal["image/png"]
    data: str = Field(repr=False)
    width: int
    height: int


LegendStatus = Literal["matched", "not_found", "ambiguous", "not_visible", "unknown", "no_stable_locator"]


class LegendEntry(BaseModel):
    """One mark of the picture. `number` is the locator's position in the request (from 1); with no locators in the request,
    the element's position among those marked."""
    model_config = ConfigDict(extra="forbid")
    number: int
    locator: Optional[str] = None           # as sent, or the one the service would write; None: no stable locator is unique
    label: Optional[str] = None             # the text drawn for it (labels "locators"), None when the number was drawn
    label_truncated: bool = False           # the drawn text is shorter than `locator` (capped, or too wide for the picture): `locator` is whole
    color: Optional[str] = None             # #rrggbb of its box and tab; boxes that overlap are given different colors
    element_kind: Optional[Literal["interactive", "text"]] = None     # when no locators were sent: what kind of element it is
    status: LegendStatus
    count: Optional[int] = None             # listed elements it matches; None when the inventory cannot tell
    ref: Optional[str] = None               # the element, when exactly one is matched
    refs: List[str] = Field(default_factory=list)    # the matched elements the listing shows (at most 50)
    visible: int = 0                        # of those, how many got a box on the picture
    kind: Optional[str] = None
    tier: Optional[int] = None              # the locator's stability rank (locator_policy.RANK): 0 test id, 1 id, 2 name, 3 css, 4 role, 5 label, 6 placeholder, 7 text, 8 chain; lower is more stable
    copy_based: bool = False                # the locator depends on page copy (a text, a name, a label)
    reason: Optional[str] = None            # no_stable_locator only: what the request lacked to show a copy locator unique (aria_snapshot_missing, copy_counts_missing, listing_incomplete, copy_not_unique, ...) or nothing_stable
    why: Optional[str] = None               # that, as a sentence; it names no page copy


class OverlaySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locators: int                           # legend entries
    marked: int                             # legend entries with at least one box on the picture
    not_visible: int                        # locators: entries with status not_visible; none sent: elements hidden or off the picture
    by_status: Dict[str, int]
    by_element_kind: Dict[str, int] = Field(default_factory=dict)      # when no locators were sent: legend entries per element kind
    inventory: int
    inventory_complete: bool                # the probe listed every element (inventory_more is 0)


class HealResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    heal_id: str
    status: Literal["try", "healed", "needs_human", "not_drift", "advised"]
    candidate: Optional[Candidate] = None
    advice: Optional[Advice] = None
    round: int = 0
    reason: Optional[str] = None
    why: Optional[str] = None
    usage: Usage = Field(default_factory=Usage)
    cache_hit: bool = False
    settled_at: Optional[int] = None
    ended_at: Optional[int] = None

    def to_json(self) -> Dict[str, Any]:
        """The response as JSON-ready data (the patch's `from` spelled as the API spells it)."""
        return self.model_dump(by_alias=True, mode="json")


class OverlayResponse(BaseModel):
    """POST /v1/overlay: the marked picture and what each mark is. The picture has the shape of a request's `page.screenshot`."""
    model_config = ConfigDict(extra="forbid")
    image: OverlayImage
    labels: Literal["numbers", "locators"]
    legend: List[LegendEntry]
    summary: OverlaySummary
    truncated: bool = False                 # the work budget ran out: the legend holds the locators done, and the picture only theirs


__all__ = ["LIMITS", "LimitExceeded", "RefusedRequest", "ValidationError", "check_limits", "refuse_secrets",
           "strip_aria_values", "parse_request", "HealRequest", "ResultRequest", "HealResponse", "Candidate", "Patch",
           "Usage", "ModelUsage", "Expect", "Span", "Hit", "AuditRequest", "parse_audit_request", "FollowupRequest",
           "Advice", "Screenshot", "Scroll", "Later", "PriorHeal", "DiagnoseRequest", "parse_diagnose_request", "Cause",
           "SuggestedPatch", "Diagnosis", "DiagnoseResponse", "OverlayRequest", "OverlayResponse", "parse_overlay_request",
           "MAX_OVERLAY_LOCATORS", "MAX_OVERLAY_EXPR"]
