"""
===============================================================================
TypeScript Client Driver (run_acceptance.py --client ts)
===============================================================================
Lets the acceptance run drive the TypeScript heal client (playwright-ai-framework-ts) instead of talking to the API itself. The
judge, the golden files, the gap list and the report are run_acceptance.py's own; only the heal of one failing statement changes:
the failure comes from the TS suite (testbed/tests/ts, Playwright's JSON report), the request is built by the client's own code
(its probe, aria snapshot, fingerprint file and request builder), the candidate is checked live, written and reported by the
client's own Healer, and the re-run inside the heal is reported by the client (failed_later for a later failure) and judged by
the service, as in the Python run (see the module docstring of run_acceptance.py).

What it adds:
    ✓ py_to_ts(): a golden Playwright (Python) locator expression as the TypeScript suites write it, so `old` / `new` / `target`
      of the drift and orders goldens can be found in, and written over, the TS files (the real golden already carries `ts`)
    ✓ TsProject: a copy of the client's repository files with a minimal Playwright config; the TS suite is copied in with its
      import pointed at the client's fixtures, so a clean run on the base site records fingerprints (HEAL_FINGERPRINTS=on)
    ✓ AppSite: the site served at ONE path (/app/) whichever variant is live, so a fingerprint recorded on the base site names the
      page the drifted site shows (the client reopens the recorded route)
    ✓ TsWorkdir: the Workdir of the Python run (text, write, run, line_text, last, failure) over the TS suite
    ✓ heal_step(): one heal through `node ts_step.mjs` against the service served over HTTP, the row built like run_acceptance's

Environment Variables:
    HEAL_TS_CLIENT_DIR: the client's repository (same as --ts-client-dir)

Author: PMAC
===============================================================================
"""
import ast
import base64
import http.server
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
TS_DIR = HERE / "tests" / "ts"
STEP = HERE / "ts_step.mjs"
ACTION_TAIL = re.compile(r"\.(?:click|dblclick|fill|check|uncheck|setChecked|selectOption|hover|press|type|clear|focus)\(.*$")
CLIENT_FILES = ("scripts", "utils", "fixtures", "config", "pages", "data", "package.json", "tsconfig.json")
METHODS = {"get_by_role": "getByRole", "get_by_label": "getByLabel", "get_by_text": "getByText", "get_by_placeholder": "getByPlaceholder",
           "get_by_test_id": "getByTestId", "get_by_alt_text": "getByAltText", "get_by_title": "getByTitle",
           "frame_locator": "frameLocator", "locator": "locator", "filter": "filter", "nth": "nth"}
OPTIONS = {"has_text": "hasText", "has_not_text": "hasNotText", "has": "has", "has_not": "hasNot", "include_hidden": "includeHidden"}


# ---- golden expressions: Python -> TypeScript -------------------------------------------------------------------------
def _js_string(value: str) -> str:
    quote = '"' if "'" in value and '"' not in value else "'"
    body = value.replace("\\", "\\\\").replace("\n", "\\n")
    return quote + (body if quote == '"' else body.replace("'", "\\'")) + quote


def _ts(node: ast.AST) -> str:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return "true" if node.value else "false"
        return _js_string(node.value) if isinstance(node.value, str) else str(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        if node.attr in ("first", "last"):
            return f"{_ts(node.value)}.{node.attr}()"
        raise ValueError(f"attribute {node.attr}")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        method = METHODS.get(node.func.attr)
        if method is None:
            raise ValueError(f"method {node.func.attr}")
        args = [_ts(a) for a in node.args]
        if node.keywords:
            opts = ", ".join(f"{OPTIONS.get(k.arg, k.arg)}: {_ts(k.value)}" for k in node.keywords)
            args.append("{ " + opts + " }")
        return f"{_ts(node.func.value)}.{method}({', '.join(args)})"
    raise ValueError(type(node).__name__)


def py_to_ts(expr: str) -> Optional[str]:
    """A golden Playwright (Python) locator expression as the TS suites write it, or None when it has a form not translated."""
    try:
        return _ts(ast.parse(expr, mode="eval").body)
    except (ValueError, SyntaxError):
        return None


def norm(text: str) -> str:
    """Spacing and quote style folded, so a statement is found whichever way the suite spells a string."""
    return re.sub(r"\s+", "", text).replace('"', "'")


# ---- the served site -------------------------------------------------------------------------------------------------
class AppSite:
    """The sites served at one path: /app/ is whichever variant `show()` named last."""

    def __init__(self, site_root: Path, handler: Any) -> None:
        self.site_root = site_root
        self.dir = Path(tempfile.mkdtemp(prefix="heal-testbed-app-"))
        self.link = self.dir / "app"
        import functools
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(handler, directory=str(self.dir)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"

    def show(self, variant: str) -> None:
        tmp = self.dir / f".app-{os.getpid()}"
        if tmp.is_symlink():
            tmp.unlink()
        tmp.symlink_to(self.site_root / variant, target_is_directory=True)
        os.replace(tmp, self.link)

    def close(self) -> None:
        self.server.shutdown()
        shutil.rmtree(self.dir, ignore_errors=True)


# ---- the project: the client's files and the TS suite ------------------------------------------------------------------
class TsProject:
    """A scratch copy of the TS client's repository files plus one TS suite, ready to run with Playwright."""

    def __init__(self, client_dir: Path, spec: str, timeout_ms: int) -> None:
        # absolute, so the node_modules link in the scratch copy resolves from the temp dir
        self.client_dir, self.spec, self.timeout_ms = Path(client_dir).resolve(), spec, timeout_ms
        self.dir = Path(tempfile.mkdtemp(prefix=f"heal-testbed-ts-{spec}-"))
        self.file = f"tests/{spec}.spec.ts"
        self.populate()

    def populate(self) -> None:
        for name in CLIENT_FILES:
            src = self.client_dir / name
            if src.is_dir():
                shutil.copytree(src, self.dir / name, symlinks=True)
            elif src.exists():
                shutil.copy2(src, self.dir / name)
        nm = self.client_dir / "node_modules"
        (self.dir / "node_modules").symlink_to(nm)
        (self.dir / "tests").mkdir(exist_ok=True)
        (self.dir / "playwright.config.ts").write_text(
            'import { defineConfig } from "@playwright/test";\n'
            f'export default defineConfig({{ testDir: "./tests", timeout: 120000, workers: 1, retries: 0, reporter: [["list"]],\n'
            f'  expect: {{ timeout: {self.timeout_ms} }}, use: {{ headless: true, actionTimeout: {self.timeout_ms} }} }});\n',
            encoding="utf-8")
        text = (TS_DIR / f"{self.spec}.spec.ts").read_text(encoding="utf-8")
        text = text.replace("from '@playwright/test'", "from '../fixtures/index.js'", 1)
        text = re.sub(r"test\.use\(\{ actionTimeout: \d+ \}\);", "test.use({ actionTimeout: Number(process.env.QA_ACTION_TIMEOUT_MS || 1500) });", text)
        (self.dir / self.file).write_text(text, encoding="utf-8")

    def env(self, base_url: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        env = dict(os.environ, QA_BASE_URL=base_url, QA_ACTION_TIMEOUT_MS=str(self.timeout_ms), HEAL_PROJECT="")
        env.pop("HEAL_RUN", None)
        env.update(extra or {})
        return env

    def titles(self) -> List[str]:
        proc = subprocess.run(["npx", "playwright", "test", self.file, "--list", "--reporter=json"], cwd=self.dir, env=self.env("http://x/"),
                              capture_output=True, text=True, timeout=300)
        out = proc.stdout
        if not out.strip():
            raise RuntimeError(f"playwright --list printed nothing in {self.dir}: {proc.stderr[-2000:]}")
        found: List[str] = []

        def walk(suite: Dict[str, Any]) -> None:
            for s in suite.get("suites", []):
                walk(s)
            found.extend(spec["title"] for spec in suite.get("specs", []))
        for s in json.loads(out).get("suites", []):
            walk(s)
        return found

    def copy(self) -> "TsProject":
        """A scratch copy of this project (its .heal folder with it), so each variant edits its own suite file."""
        other = object.__new__(TsProject)
        other.client_dir, other.spec, other.timeout_ms, other.file = self.client_dir, self.spec, self.timeout_ms, self.file
        other.dir = Path(tempfile.mkdtemp(prefix=f"heal-testbed-ts-{self.spec}-"))
        for child in self.dir.iterdir():
            if child.is_symlink():
                (other.dir / child.name).symlink_to(os.readlink(child))
            elif child.is_dir():
                shutil.copytree(child, other.dir / child.name, symlinks=True)
            else:
                shutil.copy2(child, other.dir / child.name)
        return other


def is_assertion(error: str) -> bool:
    """A Playwright (TS) assertion failure: its first line names expect(...). A Python run says AssertionError."""
    return bool(re.match(r"(Error: )?(\S+: )?expect\(", error.split("\n", 1)[0]))


def test_name(title: str) -> str:
    """The Python suite's name for a TS test title ('profile settings' -> 'test_profile_settings')."""
    return "test_" + re.sub(r"\W+", "_", title.strip()).strip("_")


class TsWorkdir:
    """run_acceptance.Workdir over the TS suite: the same text / write / run / line_text, the failure as Playwright's JSON report
    gives it. A heal edits the project's suite file in place."""

    old_key, new_key = "old_ts", "new_ts"

    def __init__(self, project: TsProject, base_url: str, titles: Dict[str, str]) -> None:
        self.project, self.base_url, self.titles = project, base_url, titles
        self.dir = project.dir
        self.test_file, self.spec = project.file, project.spec
        self.last: Optional[Dict[str, Any]] = None
        self.details: Dict[str, Dict[str, Any]] = {}

    @property
    def text(self) -> str:
        return (self.dir / self.test_file).read_text(encoding="utf-8")

    def write(self, text: str) -> None:
        (self.dir / self.test_file).write_text(text, encoding="utf-8")

    def line_text(self, line: Optional[int]) -> str:
        lines = self.text.split("\n")
        return lines[line - 1].strip() if line and 0 < line <= len(lines) else ""

    def run(self, test: Optional[str] = None, record: bool = False) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
        """Run one test (or the whole suite, HEAL_FINGERPRINTS=on when `record`). Returns {python test name: result}."""
        out_file = self.dir / "report.json"
        args = ["npx", "playwright", "test", self.test_file, "--reporter=json", "--retries=0"]
        if test:
            args += ["--grep", re.escape(self.titles[test])]
        extra = {"PLAYWRIGHT_JSON_OUTPUT_NAME": str(out_file)}
        if record:
            extra["HEAL_FINGERPRINTS"] = "on"
        else:
            # as the client's own runs do (both are needed): a failing test attaches its page as it left it
            extra.update(HEAL_RUN="1", HEAL_CAPTURE="1")
        subprocess.run(args, cwd=self.dir, env=self.project.env(self.base_url, extra), capture_output=True, text=True, timeout=900)
        report = json.loads(out_file.read_text(encoding="utf-8"))
        by_title = {v: k for k, v in self.titles.items()}
        results: Dict[str, Dict[str, Any]] = {}

        def walk(suite: Dict[str, Any]) -> None:
            for s in suite.get("suites", []):
                walk(s)
            for spec in suite.get("specs", []):
                t = spec["tests"][0]
                last = t["results"][-1] if t.get("results") else {}
                name = by_title.get(spec["title"], spec["title"])
                if last.get("status") == "passed":
                    results[name] = {"result": "passed"}
                    continue
                err = (last.get("errors") or [{}])[0]
                message = re.sub(r"\x1b\[[0-9;]*m", "", err.get("message", "") or "")
                loc = err.get("location") or {}
                line = loc.get("line")
                att = next((a for a in last.get("attachments", []) if a.get("name") == "heal-page-url"), None)
                route = None
                if att and att.get("body"):
                    route = base64.b64decode(att["body"]).decode("utf-8", "replace").strip()
                cap = next((a for a in last.get("attachments", []) if a.get("name") == "heal-page-capture"), None)
                capture_text = base64.b64decode(cap["body"]).decode("utf-8", "replace") if cap and cap.get("body") else None
                self.details[name] = {"title": spec["title"], "stack": re.sub(r"\x1b\[[0-9;]*m", "", err.get("stack", "") or ""),
                                      "location": loc or None, "route": route, "capture_text": capture_text}
                results[name] = {"result": "failed", "error": message[:20000], "frames": [(Path(loc.get("file", "")).name, line)] if line else [],
                                 "line": line}
        for s in report.get("suites", []):
            walk(s)
        return results, {}

    def failure(self, test: str) -> Optional[Dict[str, Any]]:
        """Stands in for the Python run's dump: the TS client reopens the page itself, so only 'a failure was seen' is needed."""
        return {"probe": {}, "url": ""} if test in self.details else None


# ---- one heal --------------------------------------------------------------------------------------------------------
def target_expression(step: Optional[Dict[str, Any]], statement: str) -> Tuple[Optional[str], Optional[str]]:
    """(the TS expression of the golden element, a note when there is none). Like the Python run, a step with no `target` of its
    own composes it from the failing statement with the golden replacement in place and its action cut off."""
    if step is None:
        return None, "no golden step matched the failing line"
    if step.get("new") is None:
        return None, None
    expr = step.get("target_ts")
    if step.get("compose") and step.get("new_ts") and step.get("old_ts"):
        text = statement.strip().replace(step["old_ts"], step["new_ts"])
        expr = re.sub(r"^await\s+", "", ACTION_TAIL.sub("", text))
    return (expr, None) if expr else (None, "the golden expression has no TypeScript form")


def heal_step(svc: Any, wd: TsWorkdir, test: str, result: Dict[str, Any], step: Optional[Dict[str, Any]], use_fingerprints: bool,
              run_id: str, meets: Callable[[Dict[str, Any]], bool]) -> Dict[str, Any]:
    """Heal one failing statement through `node ts_step.mjs` (the TS client). Returns the row run_acceptance.heal_step returns."""
    detail = wd.details[test]
    statement = wd.line_text(result["line"])
    expr, note = target_expression(step, statement)
    payload = {
        "projectDir": str(wd.dir), "serviceUrl": svc.http_url, "scriptUrl": svc.http_url + "/_testbed/script" if svc.model is not None else None,
        "baseUrl": wd.base_url.split("/app/")[0], "appUrl": wd.base_url, "file": wd.test_file, "timeoutMs": wd.project.timeout_ms,
        "fingerprints": use_fingerprints, "runId": run_id, "targetTs": expr, "hasTarget": step is not None and step.get("new") is not None,
        "failure": {"title": detail["title"], "error": result["error"], "stack": detail["stack"], "location": detail["location"],
                    "route": detail["route"], "captureText": detail.get("capture_text")},
    }
    proc = subprocess.run(["node", str(STEP)], cwd=wd.dir, input=json.dumps(payload), capture_output=True, text=True, timeout=900)
    if os.environ.get("TS_STEP_DEBUG"):
        print(proc.stderr[-3000:])
    line = next((entry for entry in reversed(proc.stdout.splitlines()) if entry.startswith("RESULT ")), None)
    if line is None:
        raise RuntimeError(f"ts_step.mjs gave no result: {(proc.stdout + proc.stderr)[-600:]}")
    out = json.loads(line[len("RESULT "):])
    d = out["decision"]
    target_ref = out.get("targetRef")
    row: Dict[str, Any] = {"target_note": None if target_ref else (out.get("targetNote") or note or "the golden element is not in the probe listing"),
                           "test": test, "line": result["line"], "statement": statement[:200], "target_hidden": out.get("targetHidden"), "target_facts": out.get("targetFacts"),
                           "expected": (step or {}).get("expect", {"outcome": "unlisted"}), "target_ref": target_ref}
    resp: Dict[str, Any] = {"status": d["outcome"], "reason": d.get("reason"), "why": d.get("why"), "settled_at": d.get("settled_at"),
                            "round": d.get("rounds") or 0, "usage": d.get("usage") or {}}
    record: Dict[str, Any] = {"tries": []}
    if d.get("heal_id"):
        record = svc.get(f"/v1/heals/{d['heal_id']}")
        resp["ended_at"] = record.get("ended_at")
    usage = resp["usage"]
    row.update({"images": usage.get("images", 0), "vision_ran": False, "vision_changed": False, "vision_trigger": None, "vision_skipped": None,
                "heal_id": d.get("heal_id"), "status": resp["status"], "reason": resp["reason"], "why": resp["why"],
                "settled_at": resp["settled_at"], "ended_at": resp.get("ended_at"), "calls": usage.get("calls", 0),
                "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0), "rounds": resp["round"],
                "tries": [t["result"] for t in record["tries"]], "candidates": [], "duration_ms": record.get("duration_ms"),
                "wrong_catches": sum(1 for t in record["tries"] if t["result"] in ("wrong_element", "rerun_failed")),
                "tier_movement": record.get("tier_movement"), "below_floor": bool(record.get("below_floor")),
                "branch": bool(record.get("branch")), "cache_hit": bool(record.get("cache_hit")),
                "final_locator": record.get("final_locator") if resp["status"] == "healed" else None,
                "on_target": (d.get("ref") is not None and d.get("ref") == target_ref) if resp["status"] == "healed" else None,
                "client_notes": d.get("notes")})
    row["met"] = meets(row)
    return row

