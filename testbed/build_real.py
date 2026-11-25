#!/usr/bin/env python3
"""Build the real-locator spec: site/real-base, site/v10 ... v12, the two test files, golden-real.json and CHANGES.md.

Every locator in real_catalog.py is copied from a real public test suite (see the catalog notes); the markup around it is the
smallest page that makes it resolve to the intended element, with component-library DOM (react-select, MUI, ag-grid, AntD,
Select2, Ionic-style shadow hosts, payment iframes) where the locator comes from one. Pages, tests and variants are generated
from that one catalog, so the python and ts suites always do the same steps.

An element a test acts on is marked `data-hit` in the source html; hit.js reads the marker and removes it at boot, and records
"hit.<key>: <value>" in the page summary the assertions read (a machine value, never copy).

    python3 build_real.py
"""
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
ASSETS = os.path.join(HERE, "real_assets")
PY_TEST = os.path.join(HERE, "tests", "python", "test_real.py")
TS_TEST = os.path.join(HERE, "tests", "ts", "real.spec.ts")
GOLDEN = os.path.join(HERE, "golden-real.json")
BASE_DIR = "real-base"
VARIANTS = ("v10", "v11", "v12")


# ---------------------------------------------------------------- locator calls -> python / typescript
def L(sel, has_text=None, has_text_re=None):
    return ("loc", sel, has_text, has_text_re)


def F(sel):
    return ("frame", sel)


def T(text, exact=False):
    return ("text", text, exact)


def R(role, name=None, exact=False):
    return ("role", role, name, exact)


def TID(s):
    return ("testid", s)


def LAB(s):
    return ("label", s)


FIRST, LAST = ("first",), ("last",)


def NTH(n):
    return ("nth", n)


def FT(text):
    return ("ftext", text)


def FTRE(rx):
    return ("ftextre", rx)


def FH(calls):
    return ("fhas", calls)


def pys(s):
    s = s.replace("\\", "\\\\")
    return '"' + s + '"' if '"' not in s else ("'" + s + "'" if "'" not in s else '"' + s.replace('"', '\\"') + '"')


def tss(s):
    s = s.replace("\\", "\\\\")
    return "'" + s + "'" if "'" not in s else ('"' + s + '"' if '"' not in s else "'" + s.replace("'", "\\'") + "'")


def py_expr(calls, root="page"):
    out = root
    for c in calls:
        k = c[0]
        if k == "loc":
            extra = (", has_text=" + pys(c[2])) if c[2] else (", has_text=re.compile(r" + pys(c[3]) + ")" if c[3] else "")
            out += ".locator(" + pys(c[1]) + extra + ")"
        elif k == "frame":
            out += ".frame_locator(" + pys(c[1]) + ")"
        elif k == "text":
            out += ".get_by_text(" + pys(c[1]) + (", exact=True" if c[2] else "") + ")"
        elif k == "role":
            out += ".get_by_role(" + pys(c[1]) + ((", name=" + pys(c[2])) if c[2] else "") + (", exact=True" if c[3] else "") + ")"
        elif k == "testid":
            out += ".get_by_test_id(" + pys(c[1]) + ")"
        elif k == "label":
            out += ".get_by_label(" + pys(c[1]) + ")"
        elif k == "first":
            out += ".first"
        elif k == "last":
            out += ".last"
        elif k == "nth":
            out += ".nth(%d)" % c[1]
        elif k == "ftext":
            out += ".filter(has_text=" + pys(c[1]) + ")"
        elif k == "ftextre":
            out += ".filter(has_text=re.compile(r" + pys(c[1]) + "))"
        elif k == "fhas":
            out += ".filter(has=" + py_expr(c[1]) + ")"
        else:
            raise ValueError(k)
    return out


def ts_expr(calls, root="page"):
    out = root
    for c in calls:
        k = c[0]
        if k == "loc":
            extra = (", { hasText: " + tss(c[2]) + " }") if c[2] else (", { hasText: /" + c[3] + "/ }" if c[3] else "")
            out += ".locator(" + tss(c[1]) + extra + ")"
        elif k == "frame":
            out += ".frameLocator(" + tss(c[1]) + ")"
        elif k == "text":
            out += ".getByText(" + tss(c[1]) + (", { exact: true }" if c[2] else "") + ")"
        elif k == "role":
            opts = []
            if c[2]:
                opts.append("name: " + tss(c[2]))
            if c[3]:
                opts.append("exact: true")
            out += ".getByRole(" + tss(c[1]) + ((", { " + ", ".join(opts) + " }") if opts else "") + ")"
        elif k == "testid":
            out += ".getByTestId(" + tss(c[1]) + ")"
        elif k == "label":
            out += ".getByLabel(" + tss(c[1]) + ")"
        elif k == "first":
            out += ".first()"
        elif k == "last":
            out += ".last()"
        elif k == "nth":
            out += ".nth(%d)" % c[1]
        elif k == "ftext":
            out += ".filter({ hasText: " + tss(c[1]) + " })"
        elif k == "ftextre":
            out += ".filter({ hasText: /" + c[1] + "/ })"
        elif k == "fhas":
            out += ".filter({ has: " + ts_expr(c[1]) + " })"
        else:
            raise ValueError(k)
    return out


# ---------------------------------------------------------------- catalog entries
class M:
    """A variant's change to one entry. sub = strict (old, new) replacements of the entry's html; chain / xchain = the new
    selector the markup is generated from; html = the whole new fragment; fsub = replacements in the frame document;
    heal = the correct locator after the change (None = the element is gone: NONE; "green" = the line must keep passing)."""

    def __init__(self, change, heal="green", why="", sub=(), chain=None, xchain=None, html=None, fsub=(), no_frame=False,
                 frame=None, also=None, also_html=None):
        self.frame, self.also, self.also_html = frame, also or {}, also_html or {}
        self.change, self.heal, self.why = change, heal, why
        self.sub, self.chain, self.xchain, self.html, self.fsub, self.no_frame = list(sub), chain, xchain, html, list(fsub), no_frame


class E:
    def __init__(self, key, cat, page, loc=None, act=None, html="", src="", muts=None, chain=None, xchain=None, text="",
                 leaf=None, leaf_tag=None, fname=None, fhtml="", check_assert=True):
        self.key, self.cat, self.page, self.loc, self.act, self.html, self.src = key, cat, page, loc, act, html, src
        self.muts = muts or {}
        self.chain, self.xchain, self.text, self.leaf, self.leaf_tag = chain, xchain, text, leaf or {}, leaf_tag
        self.fname, self.fhtml, self.check_assert = fname, fhtml, check_assert


def hit_value(act):
    if act is None:
        return None
    if act == "click":
        return "click"
    if act == "check":
        return "checked"
    return act[1]          # ("fill", v) / ("select", v)


# ---------------------------------------------------------------- markup generators (selector -> smallest page)
def _split_chain(sel):
    """[(combinator, compound)] of a css chain with ' > ' and ' ' combinators (brackets, parens and quotes respected)."""
    out, cur, depth, quote, comb, i = [], "", 0, None, ">", 0
    s = sel.strip()
    while i < len(s):
        ch = s[i]
        if quote:
            cur += ch
            if ch == "\\":
                cur += s[i + 1]
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur += ch
        elif ch in "[(":
            depth += 1
            cur += ch
        elif ch in "])":
            depth -= 1
            cur += ch
        elif depth == 0 and ch in " >":
            if cur:
                out.append((comb, cur))
                cur, comb = "", " "
            if ch == ">":
                comb = ">"
        else:
            cur += ch
        i += 1
    if cur:
        out.append((comb, cur))
    return out


_ATTR = re.compile(r"""\[([\w:-]+)(?:=(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|([^\]]*)))?\]""")


def _unesc(v):
    return re.sub(r"\\(.)", r"\1", v)


def _compound(c):
    m = re.match(r"([a-zA-Z][\w-]*|\*)?", c)
    tag, rest = (m.group(1) or "div"), c[m.end():]
    if tag == "*":
        tag = "div"
    ident, classes, attrs, nth = None, [], [], None
    while rest:
        if rest[0] == "#":
            m = re.match(r"#([\w-]+)", rest)
            ident = m.group(1)
        elif rest[0] == ".":
            m = re.match(r"\.(-?[\w-]+)", rest)
            classes.append(m.group(1))
        elif rest[0] == "[":
            m = _ATTR.match(rest)
            v = next((g for g in m.groups()[1:] if g is not None), None)
            attrs.append((m.group(1), None if v is None else _unesc(v)))
        elif rest.startswith(":nth-child("):
            m = re.match(r":nth-child\((\d+)\)", rest)
            nth = int(m.group(1))
        else:
            raise ValueError(f"unsupported selector part {rest!r} in {c!r}")
        rest = rest[m.end():]
    return tag, ident, classes, attrs, nth


def _open(tag, ident, classes, attrs, extra):
    a = ""
    if ident:
        a += f' id="{ident}"'
    cls = list(classes) + ([extra["class"]] if extra.get("class") else [])
    if cls:
        a += ' class="%s"' % " ".join(cls)
    for k, v in attrs:
        if k == "id" and v is not None:
            a += f' id="{v}"'
        else:
            a += f' {k}' if v is None else f' {k}="{v}"'
    for k, v in extra.items():
        if k != "class":
            a += f' {k}="{v}"'
    return a


def chain_html(sel, text, leaf=None, leaf_tag=None):
    """The markup in which the css chain `sel` resolves to one element: a leaf holding `text` and the hit marker."""
    parts = _split_chain(sel)
    n = len(parts)

    def build(i):
        comb, c = parts[i]
        tag, ident, classes, attrs, nth = _compound(c)
        last = i == n - 1
        if last and leaf_tag:
            tag = leaf_tag
        filler = "i" if tag == "span" else "span"
        pre = ("<%s></%s>" % (filler, filler)) * ((nth or 1) - 1)
        if last:
            inner = text
            extra = dict(leaf or {})
            return pre + "<%s%s{H}>%s</%s>" % (tag, _open(tag, ident, classes, attrs, extra), inner, tag)
        nxt = build(i + 1)
        if parts[i + 1][0] == " ":
            nxt = "<div>%s</div>" % nxt
        return pre + "<%s%s>%s</%s>" % (tag, _open(tag, ident, classes, attrs, {}), nxt, tag)

    return build(0)


def xpath_html(xp, text, leaf=None):
    """(markup, absolute): the markup in which the positional xpath resolves to one element. absolute = it starts at /html/body."""
    m = re.match(r"""//\*\[@id=["']([^"']+)["']\](.*)$""", xp)
    root_attrs, absolute = "", False
    if m:
        root_attrs, rest = f' id="{m.group(1)}"', m.group(2)
    else:
        m = re.match(r"""//\*\[starts-with\(@id,\s*["']([^"']+)["']\)\](.*)$""", xp)
        if m:
            root_attrs, rest = f' id="{m.group(1)}x1"', m.group(2)
        else:
            assert xp.startswith("/html/body"), xp
            rest, absolute = xp[len("/html/body"):], True
    steps = [s for s in rest.split("/") if s]

    def build(i):
        name, idx = re.match(r"([\w-]+)(?:\[(\d+)\])?$", steps[i]).groups()
        pre = ("<%s></%s>" % (name, name)) * ((int(idx) if idx else 1) - 1)
        if i == len(steps) - 1:
            extra = "".join(f' {k}="{v}"' for k, v in (leaf or {}).items())
            return pre + "<%s%s{H}>%s</%s>" % (name, extra, text, name)
        return pre + "<%s>%s</%s>" % (name, build(i + 1), name)

    body = build(0)
    return (body if absolute else "<div%s>%s</div>" % (root_attrs, body)), absolute


def entry_html(e, mut=None, also=None):
    """The markup of an entry in a variant (mut = its M, or None for the base; also = (html override, subs) from other entries' M)."""
    if also and also[0] is not None:
        h = also[0]
    elif mut and mut.html is not None:
        h = mut.html
    elif e.chain or (mut and mut.chain):
        h = chain_html(mut.chain if mut and mut.chain else e.chain, e.text, e.leaf, e.leaf_tag)
    elif e.xchain or (mut and mut.xchain):
        h = xpath_html(mut.xchain if mut and mut.xchain else e.xchain, e.text, e.leaf)[0]
    else:
        h = e.html
    for old, new in (mut.sub if mut else []) + (also[1] if also else []):
        assert h.count(old) >= 1, f"{e.key}: {old!r} not in the entry html"
        h = h.replace(old, new)
    return h


def is_absolute(e, mut=None):
    x = (mut.xchain if mut and mut.xchain else e.xchain)
    return bool(x) and x.startswith("/html/body")


# ---------------------------------------------------------------- pages
HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
"""
TAIL = """<script src="hit.js"></script>
<script src="components.js"></script>
</body>
</html>
"""


def mark(h, key):
    return re.sub(r"\{H(?::(\w+))?\}", lambda m: ' data-hit="%s"' % (m.group(1) or key), h)


def build_site(name, entries, pages):
    d = os.path.join(SITE, name)
    if os.path.exists(d):
        shutil.rmtree(d)
    os.makedirs(d)
    for f in os.listdir(ASSETS):
        shutil.copy2(os.path.join(ASSETS, f), d)
    var = None if name == BASE_DIR else name
    frames = {}
    also = {}                      # entry key -> [html override or None, subs] contributed by other entries' variant changes
    for e in entries:
        m = e.muts.get(var) if var else None
        if m:
            for k, subs in m.also.items():
                also.setdefault(k, [None, []])[1].extend(subs)
            for k, h in m.also_html.items():
                also.setdefault(k, [None, []])[0] = h
    for page, es in pages.items():
        out = [HEAD % page]
        body = []
        absolute = False
        for e in es:
            mut = e.muts.get(var) if var else None
            h = entry_html(e, mut, also.get(e.key))
            if mut and mut.frame:
                frames.setdefault(mut.frame[0], []).append(mark(mut.frame[1], e.key))
            if is_absolute(e, mut):
                absolute = True
                body.append("<!-- %s [%s] -->\n%s\n" % (e.key, e.cat, mark(h, e.key)))
                continue
            body.append("<!-- %s [%s] -->\n<section>%s</section>\n" % (e.key, e.cat, mark(h, e.key)) if h else "")
            if e.fname and not (mut and mut.no_frame):
                fh = e.fhtml
                if mut:
                    for old, new in mut.fsub:
                        assert old in fh, f"{e.key}: {old!r} not in the frame html"
                        fh = fh.replace(old, new)
                frames.setdefault(e.fname, []).append(mark(fh, e.key))
        if absolute:   # an absolute /html/body/... path owns the body; the summary is not a div so positions are kept
            out.append('<p data-testid="summary"></p>\n')
        else:
            out.append('<p data-testid="summary"></p>\n')
        out.append("".join(body))
        out.append(TAIL)
        with open(os.path.join(d, page + ".html"), "w", encoding="utf-8") as f:
            f.write("".join(out))
    for fname, parts in frames.items():
        with open(os.path.join(d, "frame-" + fname + ".html"), "w", encoding="utf-8") as f:
            f.write(HEAD % fname + "".join(parts) + "\n" + TAIL)
    return d


# ---------------------------------------------------------------- tests
def gen_tests(pages):
    """Write the two suites; returns {key: (py line, ts line)} of every acting step."""
    py = ['import os', 'import re  # noqa: F401', '', 'from playwright.sync_api import Page, expect', '',
          'TIMEOUT = int(os.environ.get("QA_ACTION_TIMEOUT_MS", "5000"))', '',
          'BASE = os.environ.get("QA_BASE_URL", "http://127.0.0.1:8765/real-base/")', 'if not BASE.endswith("/"):', '    BASE += "/"', '',
          '# Every locator below is copied from a real public test suite; the comment names its category and kind of source.', '']
    ts = ["import { test, expect } from '@playwright/test';", '',
          "let BASE = process.env.QA_BASE_URL || 'http://127.0.0.1:8765/real-base/';", "if (!BASE.endsWith('/')) BASE += '/';", '',
          "test.use({ actionTimeout: 5000 });", '', '// Every locator below is copied from a real public test suite; the comment names its category and kind of source.', '']
    lines, tests = {}, {}
    for page, es in pages.items():
        fn = "test_real_" + page.replace("-", "_")
        title = "real " + page.replace("-", " ")
        tests[page] = (fn, title)
        py += ["", f"def {fn}(page: Page):", "    page.set_default_timeout(TIMEOUT)", f'    page.goto(BASE + "{page}.html")']
        ts += ["", f"test({tss(title)}, async ({{ page }}) => {{", f"  await page.goto(BASE + '{page}.html');"]
        for e in es:
            if e.act is None:
                continue
            py.append(f"    # [{e.cat}] {e.src}")
            ts.append(f"  // [{e.cat}] {e.src}")
            a = e.act
            pa = ".click()" if a == "click" else ".check()" if a == "check" else (".fill(%s)" % pys(a[1]) if a[0] == "fill" else ".select_option(%s)" % pys(a[1]))
            ta = ".click()" if a == "click" else ".check()" if a == "check" else (".fill(%s)" % tss(a[1]) if a[0] == "fill" else ".selectOption(%s)" % tss(a[1]))
            py.append("    " + py_expr(e.loc) + pa)
            ts.append("  await " + ts_expr(e.loc) + ta + ";")
            lines[e.key] = (len(py), len(ts))
            if e.check_assert:
                v = "hit.%s: %s" % (e.key, hit_value(a))
                py.append(f"    expect(page.get_by_test_id(\"summary\")).to_contain_text({pys(v)})")
                ts.append(f"  await expect(page.getByTestId('summary')).toContainText({tss(v)});")
        ts.append("});")
    with open(PY_TEST, "w", encoding="utf-8") as f:
        f.write("\n".join(py) + "\n")
    with open(TS_TEST, "w", encoding="utf-8") as f:
        f.write("\n".join(ts) + "\n")
    return lines, tests


# ---------------------------------------------------------------- variants
CHANGES_INTRO = {
    "v10": ("v10 - restructure", "Wrappers inserted or removed, siblings added or reordered, columns and panes reordered. Attributes, ids, "
            "test hooks, aria and copy are unchanged, so the structural, nth and position chains break (some resolve to the WRONG element and fail "
            "the assertion instead of timing out) and the right heals move to a stable attribute."),
    "v11": ("v11 - attribute churn", "Same structure. Generated ids and hash classes regenerate, test-hook attributes are renamed, aria-labels and copy "
            "change, library versions rename internals. The right heal is whatever stable attribute is left on the same element (never a "
            "generated id, never the new copy when a hook exists)."),
    "v12": ("v12 - mixed", "A few of each, plus elements moved into an iframe and a shadow root, one moved out of an iframe, one list re-sorted so a "
            "positional locator hits another row, and one element removed (expect NONE, never a substitute)."),
}


def write_changes(var, d, entries, lines):
    title, intro = CHANGES_INTRO[var]
    out = [f"# {title}", "", intro, "",
           "Lines are tests/python/test_real.py (py) and tests/ts/real.spec.ts (ts); heals are written in the python syntax and follow the "
           "locator policy: test id > hand-written id > name > css on a non-copy attribute > role+name > label > text > structural chain. "
           "`verify_golden.py --spec real` applies every heal listed here and runs the suite. A line that is not listed still passes.", ""]
    golden = []
    for e in entries:
        mut = e.muts.get(var)
        if not mut:
            continue
        out.append(f"- [{e.cat}] `{e.key}`: {mut.change}")
        if e.act is None:
            continue
        pl, tl = lines[e.key]
        if mut.heal == "green":
            out.append(f"  - py L{pl}; ts L{tl} stays green: {mut.why}")
            continue
        old = py_expr(e.loc)
        note = "NONE: the element is gone, delete the step (no substitute)" if mut.heal is None else "`" + py_expr(mut.heal) + "`"
        out.append(f"  - affects `{old}` (py L{pl}; ts L{tl}): {mut.why}. Heal: {note}")
        golden.append({"key": e.key, "page": e.page, "old": {"python": old, "ts": ts_expr(e.loc)},
                       "new": None if mut.heal is None else {"python": py_expr(mut.heal), "ts": ts_expr(mut.heal)}})
    out += ["", "Every other entry of the catalog is unchanged and passes.", ""]
    with open(os.path.join(d, "CHANGES.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return golden


def main():
    import real_catalog as rc
    entries = rc.ENTRIES
    pages = {}
    for e in entries:
        pages.setdefault(e.page, []).append(e)
    lines, tests = {}, {}
    # variants first change nothing of the tests: the tests come from the base entries
    lines, tests = gen_tests(pages)
    build_site(BASE_DIR, entries, pages)
    golden = {}
    for var in VARIANTS:
        d = build_site(var, entries, pages)
        golden[var] = write_changes(var, d, entries, lines)
    with open(GOLDEN, "w", encoding="utf-8") as f:
        json.dump({"tests": {p: {"python": v[0], "ts": v[1]} for p, v in tests.items()}, "variants": golden}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    n = sum(1 for e in entries if e.act)
    print(f"built {BASE_DIR} + {', '.join(VARIANTS)}: {len(pages)} pages, {n} steps, "
          + ", ".join(f"{v} {len(golden[v])} heals" for v in VARIANTS))


if __name__ == "__main__":
    main()
