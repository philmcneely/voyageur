"""
===============================================================================
Bounds (the one place that says how much client text the pipeline will read)
===============================================================================
Every string the service scans comes from a client: the error text, source files, locator expressions, the aria
snapshot, inventory fields. A scanner that is linear in its input is still a stall when the input is 100 MB, and a
regex that is not linear is a stall at 100 KB. The cause is not any one function: it is that no input had a size. This
module gives each kind of text a cap, and the rule that goes with it: a text over its cap is "cannot evaluate" (no
claim is made about it) or a refusal at intake (413 / 422). It is never cut short and then compared, because a cut
changes what `$` and equality mean.

Features:
    ✓ MAX_EXPR_CHARS: one locator expression (a proposal, a printed call, a call in source)
    ✓ MAX_ERROR_CHARS / MAX_LINE_CHARS: the error text, and one line of it
    ✓ MAX_SOURCE_LINE_CHARS: a source line a call is read from
    ✓ MAX_ARIA_LINE_CHARS / MAX_ARIA_CHARS: one aria snapshot line, and the whole snapshot
    ✓ MAX_FIELD_CHARS: one inventory field value
    ✓ too_long(): the single test every entry point uses; lines_ok(): every line of a text is within a cap
    ✓ loads_bounded(): JSON from a model server, refused past MAX_JSON_DEPTH (json.loads recurses per level)

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import json
from typing import Any, Optional

MAX_EXPR_CHARS = 4000            # longest locator expression looked at; a person writes a fraction of this
MAX_ERROR_CHARS = 64 * 1024
MAX_LINE_CHARS = 8 * 1024        # one line of the error text
MAX_SOURCE_LINE_CHARS = 16 * 1024
MAX_ARIA_LINE_CHARS = 8 * 1024
MAX_ARIA_CHARS = 256 * 1024
MAX_JSON_DEPTH = 64             # a reply envelope is a few levels deep; past this it is not one
MAX_FIELD_CHARS = 400            # one attribute or copy value of an inventory entry


def too_long(text: Optional[str], cap: int = MAX_EXPR_CHARS) -> bool:
    """True when `text` is a string longer than `cap` (the caller answers "cannot evaluate", never a cut-down match)."""
    return isinstance(text, str) and len(text) > cap


def lines_ok(text: str, cap: int) -> bool:
    """True when no line of `text` is longer than `cap` (one pass, no copy of the lines)."""
    start, n = 0, len(text)
    while start < n:
        end = text.find("\n", start)
        if end < 0:
            end = n
        if end - start > cap:
            return False
        start = end + 1
    return True


def loads_bounded(text: str, max_depth: int = MAX_JSON_DEPTH) -> Any:
    """`json.loads(text)`, but a body nested deeper than `max_depth` raises ValueError instead of RecursionError.
    The depth is measured first in one pass (brackets outside strings), so nothing deep is ever handed to the parser."""
    depth, in_str, esc = 0, False, False
    for c in text:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "[{":
            depth += 1
            if depth > max_depth:
                raise ValueError(f"JSON nested deeper than {max_depth} levels")
        elif c in "]}":
            depth -= 1
    try:
        return json.loads(text)
    except RecursionError:
        raise ValueError("JSON nested too deeply") from None
