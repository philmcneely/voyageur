"""Unit: rules about the source tree itself. No file under src/ evaluates text or runs a program, every module explains
itself in the banner style, and nothing in the repository names a host, an address, a key or an internal product (the
repository is meant to be public)."""
import ast
import codecs
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
MODULES = sorted(SRC.rglob("*.py"))
BANNED_CALLS = {"eval", "exec", "compile", "__import__", "literal_eval"}
# deadline.py is the one module that starts processes: a worker pool that runs this package's own stage functions so a
# runaway scan can be killed (it never runs client text as a program and never shells out). Nothing else is allowed.
ALLOWED_IMPORTS = {"deadline.py": {"multiprocessing"}}
BANNED_IMPORTS = {"subprocess", "pty", "multiprocessing", "ctypes", "pickle", "marshal", "shelve", "playwright"}


def calls_in(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def banned_call(call: ast.Call):
    """The name of the evaluating call this is (eval / exec / compile / __import__ / literal_eval, bare or as an
    attribute), or None. `re.compile` / the RE2 module's compile build a pattern, not code, so it is not one."""
    f = call.func
    if isinstance(f, ast.Name) and f.id in BANNED_CALLS:
        return f.id
    if isinstance(f, ast.Attribute) and f.attr in BANNED_CALLS | {"import_module", "system", "popen"}:
        if f.attr == "compile" and isinstance(f.value, ast.Name) and f.value.id in ("re", "_re2"):
            return None
        return f.attr
    return None


def test_there_is_a_source_tree_to_check():
    assert len(MODULES) >= 12


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_module_evaluates_text_or_compiles_it(path):
    bad = [(c.lineno, banned_call(c)) for c in calls_in(path) if banned_call(c)]
    assert not bad, f"{path.name}: {bad}"


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_module_runs_a_program_or_drives_a_browser(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    found = names & BANNED_IMPORTS - ALLOWED_IMPORTS.get(path.name, set())
    assert not found, f"{path.name}: {sorted(found)}"


def test_the_checker_catches_what_it_is_meant_to_catch(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import ast, os, importlib, re\nx = eval('1')\nexec('y=2')\ncompile('1', 'f', 'eval')\n"
                   "__import__('os')\nast.literal_eval('1')\nbuiltins.eval('1')\nos.system('x')\n"
                   "importlib.import_module('x')\nre.compile('a')\n", encoding="utf-8")
    assert [banned_call(c) for c in calls_in(bad) if banned_call(c)] == [
        "eval", "exec", "compile", "__import__", "literal_eval", "eval", "system", "import_module"]


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_every_module_has_a_banner_docstring_that_says_what_it_does(path):
    doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")), clean=False)
    assert doc, f"{path.name} has no docstring"
    lines = doc.strip("\n").splitlines()
    assert lines[0].startswith("====") and lines[2].startswith("===="), f"{path.name}: no banner"
    assert lines[1].strip(), f"{path.name}: no title"
    body = "\n".join(lines[3:])
    assert len(body.split()) >= 25, f"{path.name}: the banner should say what the module does and why"
    assert lines[-1].startswith("====") and "Author:" in doc


# ---- nothing that must not be public ----------------------------------------------------------------------------
# The internal names, stored rot13 so this file does not itself name them (the repository is meant to be public).
_INTERNAL_ROT13 = "xvex|uny9000|uny|xnenxhev|fragvary|fjvgpuobneq|syrrg|qenpbavf|dn[-_ ]?ehaare|dn[-_ ]?pbagenpgf|rkrphgbe|znp[- ]?fghqvb|jbce|ubzryno|cuvyzparryl"
INTERNAL = re.compile(r"\b(?:" + codecs.decode(_INTERNAL_ROT13, "rot13") + r")\b", re.I)
IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])(/\d{1,2})?")
KEYS = re.compile(r"sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{12,}|xox[bp]-[A-Za-z0-9-]{20,}|"
                  r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
                  r"(?:api[_-]?key|token|secret|password)\s*[=:]\s*['\"][A-Za-z0-9+/_-]{20,}['\"]", re.I)
HOSTNAME = re.compile(r"\b[a-z0-9-]+\.(?:local|lan|internal|home|corp|ts\.net)\b", re.I)
# Docker's documented name for the host as seen from a container: the same on every machine, so it names no network of ours.
# Exact match only (the whole dotted name); anything else ending in .internal is still a finding.
# A dot here is any of the Unicode full stops a resolver folds to "." (U+FF0E, U+3002, U+FF61), so none of them can extend the name.
PUBLIC_HOSTNAMES = re.compile(r"(?<![\w.．。｡-])host\.docker\.internal(?![.．。｡]?[\w-])", re.I)
THIS_FILE = Path(__file__).resolve()
# third-party files shipped as published (swagger-ui-dist 5.17.14, integrity checked against the npm registry when added):
# minified code, where names like `resolve.internal` are properties, not hosts. Only these exact files are left out.
VENDORED = {ROOT / "src" / "voyageur" / "apidocs" / name for name in ("swagger-ui-bundle.js", "swagger-ui.css")}


def files_to_scan():
    skip_dirs = {".git", ".claude", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".runs"}
    exts = {".py", ".js", ".json", ".md", ".toml", ".txt", ".html", ".css", ".yml", ".yaml", ".cfg", ""}
    for p in ROOT.rglob("*"):
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & skip_dirs) and p != THIS_FILE and p not in VENDORED and p.suffix in exts:
            yield p


def hostname_hits(line: str):
    """The internal-looking host names in one line, Docker's own host name left out."""
    return [m.group(0) for m in HOSTNAME.finditer(PUBLIC_HOSTNAMES.sub("", line))]


def lines_of(p: Path):
    return enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), start=1)


def test_the_scan_reaches_the_readme_the_docs_the_fixtures_and_the_testbed():
    scanned = {p.relative_to(ROOT).parts[0] for p in files_to_scan()}
    assert {"README.md", "docs", "fixtures", "testbed", "src", "tests"} <= scanned


def test_no_file_names_an_internal_host_or_product():
    hits = [(str(p.relative_to(ROOT)), i, m.group(0)) for p in files_to_scan() for i, line in lines_of(p)
            for m in INTERNAL.finditer(line)]
    assert not hits, hits


def test_no_file_holds_a_lan_address_or_an_internal_hostname():
    """A 192.168.* address, or any address outside loopback, the private blocks and the documentation ranges, is a
    finding. A block written as a prefix (10.0.0.0/8) is the standard, not an address."""
    hits = []
    for p in files_to_scan():
        for i, line in lines_of(p):
            for m in IPV4.finditer(line):
                a, b = int(m.group(1)), int(m.group(2))
                if m.group(5) is not None:
                    continue
                if (a == 192 and b == 168) or a not in (127, 0, 10, 172, 198, 203, 100, 192):
                    hits.append((str(p.relative_to(ROOT)), i, m.group(0)))
            hits += [(str(p.relative_to(ROOT)), i, h) for h in hostname_hits(line)]
    assert not hits, hits


def test_only_dockers_own_host_name_is_left_out():
    assert hostname_hits("http://host.docker.internal:11434/v1") == []
    assert hostname_hits("HOST.Docker.INTERNAL") == []
    assert hostname_hits("point it at host.docker.internal.") == []                     # the end of a sentence
    assert hostname_hits("db.internal") == ["db.internal"]
    assert hostname_hits("host.docker.internal db.internal") == ["db.internal"]
    assert hostname_hits("my-host.docker.internal") == ["docker.internal"]
    assert hostname_hits("xhost.docker.internal") == ["docker.internal"]
    assert hostname_hits("foo.host.docker.internal") == ["docker.internal"]
    assert hostname_hits("host.docker.internal.lan") == ["docker.internal"]
    assert hostname_hits("host.docker.internal-x.lan") == ["docker.internal", "-x.lan"]
    assert hostname_hits("host.docker.internal.evil.lan") == ["docker.internal", "evil.lan"]
    for dot in ("．", "。", "｡"):                                        # a Unicode full stop extends the name too
        assert hostname_hits(f"host.docker.internal{dot}lan") == ["docker.internal"], dot


def test_no_file_holds_a_key_or_a_token():
    hits = [(str(p.relative_to(ROOT)), i) for p in files_to_scan() for i, line in lines_of(p) if KEYS.search(line)]
    assert not hits, hits


def test_the_scanners_catch_what_they_are_meant_to():
    def plain(word):                                   # words are rot13 here too, for the same reason
        return codecs.decode(word, "rot13")
    assert INTERNAL.search("ssh " + plain("xvex")) and INTERNAL.search(plain("dn-ehaare")) and INTERNAL.search(
        "the " + plain("Syrrg") + " map")
    assert not INTERNAL.search("hallmark") and not INTERNAL.search("exhale")
    lan = ".".join(("192", "168", "1", "2"))
    assert IPV4.search("http://" + lan + ":80") and HOSTNAME.search("box.local") and KEYS.search("sk-" + "a" * 24)
