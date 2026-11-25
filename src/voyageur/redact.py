"""
===============================================================================
Redaction (credentials and URLs never reach a log, an error or a stored record)
===============================================================================
An error from an HTTP library quotes the request: the URL (with userinfo, path and query, where a token may sit) and,
for a refused header, the header value itself. Anything the service records about a failed model call goes through
here first, so a credential that reaches an error message is removed before it can reach a log line, a stored heal
record or a response. The service logs only a heal id, an outcome and usage, but its errors are recorded too.

Features:
    ✓ scrub_secrets(text, *secrets): removes each secret in every form an error can quote it (raw, repr-escaped,
      unicode-escaped), withholds a refused header whole, drops whatever follows Bearer / Basic on its line, and
      reduces every URL to scheme://host
    ✓ register_secret(secret): a bounded set of credentials remembered for every later scrub, so a caller that has
      no key at hand (a crash handler) still removes it. The oldest is forgotten first
    ✓ redact_urls(text): scheme://host only; userinfo runs to the LAST "@" before the path, so an unencoded "@" in a
      password cannot leak its tail
    ✓ redact_typed_values(yaml_text): an accessibility snapshot without what a person typed. Playwright prints the value
      after the name of textbox, searchbox, spinbutton, slider and combobox lines, password fields included
      (`- textbox "Email": me@example.com`); the value goes, the role, name and states ([checked], [disabled]) stay,
      whatever the key's YAML quoting (plain, 'single' with '' inside, "double" with \" inside). Nested typed
      children go too: everything under a textbox or searchbox bar `- /property` lines, `text` and `/value` under
      the other value roles, and block-scalar continuations. /placeholder and combobox options stay. Ported from the
      same rule set measured against Playwright 1.58's real snapshot output; one left-to-right pass, no regex
    ✓ looks_like_credential(text): whether a name is shaped like a token (a known prefix with a long tail: GitHub, GitLab, OpenAI-style,
      Stripe, Hugging Face, Slack, AWS, Google, a JWT; or a run of 32 or more letters and digits that is mixed-case with a digit, a
      hex digest, or flips between digits and letters often). It is a shape test, not a secret scanner: a base64url token cut into
      pieces by - or _ is not caught. A name is stored by the rollup only when this says no
    ✓ Linear time on hostile text: input is capped before it is scanned and every pattern is anchored and bounded

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import re
from collections import OrderedDict
from typing import List, Optional, Tuple

SCRUB_INPUT_CHARS = 4000       # error text is capped before it is scrubbed (linear time on hostile input)
MAX_REGISTERED_SECRETS = 64    # credentials remembered for scrubbing; the oldest is forgotten first
MIN_SECRET_CHARS = 4           # shorter strings are not treated as secrets (they would mangle ordinary text)

# userinfo runs to the LAST "@" before the path (an unencoded "@" in a password must not leak its tail); closing
# delimiters ) > , end a URL so the text after it survives.
_URL_RE = re.compile(r"(?<![a-z0-9+.-])([a-z][a-z0-9+.-]{0,15}://)(?:[^/?#\s'\"<>()]*@)?([^/?#@\s'\"<>(),]*)"
                     r"[^\s'\"<>(),]*", re.I)
_AUTH_RE = re.compile(r"\b(bearer|basic)\s+[^\r\n]*", re.I)
_HEADER_RE = re.compile(r"(Illegal header (?:value|name))\b[^\r\n]*", re.I)
_SECRETS: "OrderedDict[str, None]" = OrderedDict()      # insertion-ordered set, bounded by MAX_REGISTERED_SECRETS

# A token a service issues starts with a fixed prefix and a long tail; the tail lengths are the ones tests/unit/test_source_hygiene.py
# already treats as a key. The prefix must start a word, so "task-management-dashboard" is not an "sk-" key.
# The "sk-" and "glpat-" tails must hold a digit (a real key does; "sk-learn-extra-long-experiment-name" is a name).
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])(?:sk-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
                       r"|github_pat_[A-Za-z0-9_]{20,}|glpat-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{20,}|xox[a-z]-[A-Za-z0-9-]{10,}"
                       r"|(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}|hf_[A-Za-z0-9]{30,}|AKIA[A-Z0-9]{12,}|AIza[A-Za-z0-9_-]{30,}"
                       r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})")
_RUN_RE = re.compile(r"[A-Za-z0-9]{32,}")                    # a run split on - _ . / and space: a base64url token with those is not caught


def redact_urls(text: str) -> str:
    """Keep scheme://host of every URL in an error text; drop userinfo, path and query (where a token may sit). The
    scheme is anchored and bounded and the input capped, so hostile error text costs linear time."""
    return _URL_RE.sub(r"\1\2/…", text[:SCRUB_INPUT_CHARS])


def looks_like_credential(text: str) -> bool:
    """Whether `text` is shaped like a token: one of the known prefixes with a long tail, or a run of 32 or more letters and digits
    that is a hex digest, mixes upper and lower case with a digit or flips between digits and letters six times (what a random key
    looks like; a project name is words). Linear
    time: every pattern is anchored on a fixed prefix or a character class, and the input is capped."""
    text = text[:SCRUB_INPUT_CHARS]
    if _TOKEN_RE.search(text):
        return True
    for run in _RUN_RE.findall(text):
        if not any(c.isdigit() for c in run):
            continue
        flips = sum(1 for a, b in zip(run, run[1:]) if a.isdigit() != b.isdigit())          # digit-letter boundaries: a random run has many
        if (any(c.islower() for c in run) and any(c.isupper() for c in run)) or flips >= 6 or all(c in "0123456789abcdefABCDEF" for c in run):
            return True
    return False


def register_secret(secret: Optional[str]) -> None:
    """Remember a credential (a configured API key) so every scrub_secrets call removes it, even where the caller has
    no key at hand. At most MAX_REGISTERED_SECRETS are kept; the oldest is forgotten first."""
    s = (secret or "").strip()
    if len(s) < MIN_SECRET_CHARS:
        return
    _SECRETS.pop(s, None)
    _SECRETS[s] = None
    while len(_SECRETS) > MAX_REGISTERED_SECRETS:
        _SECRETS.popitem(last=False)


def scrub_secrets(text: str, *secrets: str) -> str:
    """Error text safe to record. Each secret is removed in every form an error can quote it (raw, repr-escaped,
    unicode-escaped); then a refused header ("Illegal header value b'...'") is withheld whole, as is anything after
    Bearer / Basic on its line; then URLs are reduced to scheme://host."""
    out = text[:SCRUB_INPUT_CHARS]
    for s in {(x or "").strip() for x in secrets} | set(_SECRETS):
        if len(s) < MIN_SECRET_CHARS:
            continue
        for form in {s, repr(s)[1:-1], repr(s.encode("utf-8", "surrogateescape"))[2:-1],
                     s.encode("unicode_escape").decode("ascii")}:
            if form:
                out = out.replace(form, "…")
    out = _HEADER_RE.sub(lambda m: m.group(1) + " (withheld)", out)
    out = _AUTH_RE.sub(lambda m: m.group(1) + " …", out)
    return redact_urls(out)


# Roles whose text after the colon is the control's current value; other roles' text after the colon is page prose.
_VALUE_ROLES = frozenset({"textbox", "searchbox", "spinbutton", "slider", "combobox", "textarea"})
# Roles that hold free text: everything nested under them (text, links, ...) was typed, bar `/property` lines.
_FREE_TEXT_ROLES = frozenset({"textbox", "searchbox", "textarea"})


def _quoted_end(s: str, i: int) -> int:
    """Index just past the YAML quoted scalar opening at `s[i]` (`'..''..'` or `"..\\".."`), or -1 if unterminated."""
    quote, n, j = s[i], len(s), i + 1
    while j < n:
        c = s[j]
        if quote == "'" and c == "'":
            if j + 1 < n and s[j + 1] == "'":
                j += 2
                continue
            return j + 1
        if quote == '"' and c == "\\":
            j += 2
            continue
        if quote == '"' and c == '"':
            return j + 1
        j += 1
    return -1


def _role_at(s: str, i: int) -> str:
    j = i
    while j < len(s) and "a" <= s[j] <= "z":
        j += 1
    return s[i:j] if j == len(s) or s[j] in ' \t:"[\'' else ""


def _plain_key_end(s: str, i: int) -> int:
    """End of `role "name" [state]...` starting at `s[i]` (the role must already be known to end there)."""
    j = i
    while j < len(s) and "a" <= s[j] <= "z":
        j += 1
    while j + 1 < len(s) and s[j] == " ":
        if s[j + 1] == '"':
            k = _quoted_end(s, j + 1)
            if k < 0:
                return len(s)
            j = k
        elif s[j + 1] == "[":
            k = s.find("]", j + 2)
            if k < 0:
                return len(s)
            j = k + 1
        else:
            break
    return j


def _entry(line: str) -> Optional[Tuple[int, int, str, int, bool]]:
    """(indent, key_start, role, key_end, has_inline_value) of a `- key[: value]` line, or None for anything else."""
    n = len(line)
    indent = n - len(line.lstrip(" "))         # in C: a Python loop here costs real time on deeply indented trees
    if line[indent:indent + 1] != "-" or line[indent + 1:indent + 2] != " ":
        return None
    start = indent + 1
    while start < n and line[start] == " ":
        start += 1
    if start >= n:
        return None
    if line[start] in "'\"":
        end = _quoted_end(line, start)
        if end < 0:
            return None
        role = _role_at(line, start + 1)
    else:
        role = _role_at(line, start)
        end = _plain_key_end(line, start)
    rest = line[end:].lstrip(" \t")
    return indent, start, role, end, rest.startswith(":") and bool(rest[1:].strip())


def entry_key(line: str) -> Optional[str]:
    """The key of a `- key[: value]` snapshot line, unquoted: Playwright quotes the whole key when its name holds
    ": " (`- 'textbox "Email: work"': v`), and a role parser that expects `- role "name"` would skip that entry.
    Single quotes: `''` is `'`. Double quotes: each backslash escape is its character. None for a non-entry line."""
    ent = _entry(line)
    if ent is None:
        return None
    _, start, _, end, _ = ent
    key = line[start:end]
    if key[:1] == "'":
        return key[1:-1].replace("''", "'")
    if key[:1] == '"':
        out, i, body = [], 0, key[1:-1]
        while i < len(body):
            if body[i] == "\\" and i + 1 < len(body):
                i += 1
            out.append(body[i])
            i += 1
        return "".join(out)
    return key


def role_line(line: str) -> str:
    """`line` with a quoted key replaced by its unquoted form, so `_ROLE_RE`-style parsers see `- role "name": v`."""
    head = line.lstrip(" ")
    if head[:1] != "-" or head[1:].lstrip(" ")[:1] not in ("'", '"'):    # nearly every line: no scan at all
        return line
    ent = _entry(line)
    if ent is None or line[ent[1]:ent[1] + 1] not in "'\"":
        return line
    return line[:ent[1]] + (entry_key(line) or "") + line[ent[3]:]


def _is_property(line: str) -> bool:
    return line.lstrip(" ").startswith("- /")


def redact_typed_values(yaml_text: str) -> str:
    """`yaml_text` with the value removed from every input line of an accessibility snapshot, inline or nested."""
    out: List[str] = []
    frames: List[Tuple[int, str, List[int]]] = []   # open value-role entries: (indent, role, [child indent or -1])
    dropping = -1                                   # indent of a dropped child whose subtree is being skipped
    for line in yaml_text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        indent = len(bare) - len(bare.lstrip(" "))
        if not bare.strip():
            if dropping < 0:
                out.append(line)
            continue
        if dropping >= 0 and indent > dropping:
            continue
        dropping = -1
        while frames and frames[-1][0] >= indent:
            frames.pop()
        ent = _entry(bare)
        if frames:
            _, f_role, child = frames[-1]
            if child[0] < 0:
                child[0] = indent
            if indent <= child[0]:
                prop = _is_property(bare)
                if bare.lstrip(" ").startswith("- /value") or (not prop and (
                        ent is None or f_role in _FREE_TEXT_ROLES or ent[2] == "text")):
                    dropping = indent
                    continue
        if ent is not None and ent[2] in _VALUE_ROLES:
            frames.append((ent[0], ent[2], [-1]))
            # Cut at the key: the inline value and its colon go, and the dash is normalised to "- " so every
            # redacted line has one shape whatever the spacing it arrived with.
            line = " " * ent[0] + "- " + bare[ent[1]:ent[3]] + line[len(bare):]
        out.append(line)
    return "".join(out)
