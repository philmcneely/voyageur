# API

All routes are JSON over HTTP. Everything under `/v1` needs `Authorization: Bearer <token>` when `HEAL_SERVICE_TOKEN` is set (401
otherwise). Error bodies name the field and the rule, never the input. The examples below are taken from the contract fixtures
in `fixtures/contract/` (the case named in each heading), so they are the same bytes a client's tests replay.

| Status | Meaning |
| --- | --- |
| 200 | Answered. A `needs_human` or `not_drift` decision is also a 200. |
| 401 | Missing or wrong bearer token. |
| 404 | No such heal (never started, purged, or the process restarted mid-conversation). |
| 409 | The heal already has its final answer (a result for an advised heal, too), or a follow-up for a heal that did not end `healed`. |
| 403 | An open service (no token) was reached under a Host name that is not loopback (`127.0.0.1`, `localhost`, `[::1]`, with any port) or the address it binds. With a token there is no Host check. |
| 415 | A POST without `Content-Type: application/json`. |
| 429 | `POST /v1/audit` and `POST /v1/overlay`: every audit slot is taken (at most workers minus 1 run at once, so a heal always gets a worker). Retry after the `Retry-After` seconds. |
| 503, 504 | `POST /v1/overlay`: its worker died (503) or was stopped at the request deadline (504). |
| 413 | Over a limit: body over 10 MB, more than 20 files, a file over 256 KB or 1 MB of source in total, more than 500 inventory entries, an aria snapshot over 256 KB or a line over 8 KB, a screenshot over 4 MB decoded. |
| 422 | The body is not valid JSON or does not match the schema, the error text is over 64 KB (or a line over 8 KB), it carries a `value`, `cookie` or `storage` key anywhere, or its screenshot is refused (see [Screenshot](#screenshot)). |

## GET /healthz

No token. `{"ok": true, "version": "0.1.0"}`.

## GET /v1/capabilities

```json
{
  "version": "0.1.0",
  "models": {"text": {"configured": true, "provider": "openai", "model": "your-model-id", "local": true},
             "vision": {"configured": true, "provider": "openai", "model": "your-vision-model-id", "local": true,
                        "images_allowed": true}},
  "vision": {"available": true},
  "limits": {"request_bytes": 10485760, "files": 20, "file_bytes": 262144, "inventory": 500, "aria_chars": 262144,
             "screenshot_bytes": 4194304, "screenshot_side_px": 8192, "screenshot_pixels": 16777216,
             "candidate_cap": 30, "max_calls": 2, "max_rounds": 5, "run_token_budget": null},
  "retention_days": 31,
  "overlay_retention_days": 7,
  "max_db_mb": 0,
  "languages": ["python", "typescript"],
  "diagnose": {"enabled": false},
  "library_classes": []
}
```

`library_classes` lists the component libraries `HEAL_LIBRARY_CLASSES` opted in (empty by default).

No endpoint URL and no key is ever returned; `local` says whether the model endpoint is on this machine or a private network.
`vision.available` is true only when a vision model is configured **and** `HEAL_VISION_IMAGES_ALLOWED` is set: until then a client's
`auto` and `on` fall back to text, and the client need not take a screenshot at all (it can read this first).

`durability` says whether the service can measure durability at all: `{"available": false, "needs_retention_days": 31}` when
`HEAL_RETENTION_DAYS` is 30 or less (the default is 31), because a heal is purged before it can live 30 days. The service logs one warning
at startup in that case, and the dashboard's Durability tile says so instead of showing a percentage.

`retention_days`, `overlay_retention_days` and `max_db_mb` are the three data limits the operator set (`HEAL_RETENTION_DAYS`,
`HEAL_OVERLAY_RETENTION_DAYS`, `HEAL_MAX_DB_MB`): how long a heal record and its marked image are kept, and the optional size cap of the
store (`0` is no cap). See [What is kept](../README.md#what-is-kept-and-for-how-long).

### Configuration: `HEAL_LIBRARY_CLASSES`

Off by default (empty): a class that a component library generates (`MuiSelect-select`) is treated as generated and never written, as before.
Set it to a comma-separated list from the fixed set `mui`, `ant`, `chakra`, `mantine`, `mat` (any case, an optional trailing dash:
`Mui,ant-,mat-`) and a class of a listed library, `MuiSelect-select`, `ant-select-item`, `mat-menu-item`, `chakra-select`,
`MantineSelect-input`, may be written as a css locator (`button.MuiButton-root`; inside a row, after the row's hook). It ranks below test
ids, stable ids, names and plain `data-*` hooks and above role, label, placeholder and text. Never written, listed or not: hashed or
counted variants (`css-1x2y3z`, `jss123`, `MuiBox-root-123`, `MuiSelect-select-a1b2c3d4`) and state classes (`Mui-focused`,
`ant-select-open`, `ant-tabs-tab-active`). Uniqueness and identity proofs are unchanged, so a class is written only where the inventory can
prove it unique: on a tag the probe lists (button, input, a, ...) as `button.MuiButton-root`, or, for a class the broken call itself names
(`.MuiSelect-select`), next to the element's explicit role as `div[role='combobox'].MuiSelect-select` (the probe lists every element with a
role), at page level or inside a hooked row or container. Never on a bare `div` the listing does not cover. An unknown name is a startup
error that lists the known set.

## GET /v1/probe.js

The in-page script a client runs as `page.evaluate(<script>)` for the whole page, or `locator.evaluate(<script>)` for one element. It
returns `{items, more, copy_more, page}`: the elements a locator could hook on (attributes and visible copy, never typed values), how many were
left out beyond 500, and a small record of the page (`path`, `lang`, `browser_lang`, `viewport`, `scroll`, `dpr`). Served as
`application/javascript`. Clients fetch it from the service so the version always matches the schema.

Besides the controls and hooked elements, the list holds the visible elements that carry copy of their own (a chat line, a heading, a
radio's label text), each marked `copy_only`, so a step that finds its element by text can be healed. They fill only the room the others
leave under 500, in page order, and are not counted in `more`: the ones left out are counted in the probe's `copy_more` (send it as `page.inventory_copy_more`; each `copy_only` item also says `copy_cut`), and while it is above zero no text count read from the listing is certain, so none is made. Their copy is at most 80 characters, is never inside a form control, an
editable region or a text-entry role, and is left out when it holds an e-mail address, a run of six or more digits or a token-like word.
A `label` with a `for` is listed with it as `for_attr`. An element whose whole copy is that of a listed element inside it is marked `copy_wrap` (text locators match the inner one). The filters above are in [SECURITY.md](SECURITY.md#client-data-is-untrusted); a run of six or more digits keeps a `copy_only` item out, `inventory_copy_more`, `inventory_copy_withheld` (the probe's `copy_withheld`) and `inventory_copy_withheld_data` (`copy_withheld_data`) are at most 1,000,000 each (leave the two withheld ones out and a listing with `copy_only` items is read as having withheld copy); while any is above zero, the text counts that read the listing are unknown where the missing copy could match.

The script is a function expression. Playwright for Python calls a function string passed to `evaluate`, but Playwright for
JavaScript and TypeScript does not (it evaluates the string as an expression and returns the function uncalled). A JS client must
pass a function: the TypeScript client wraps its vendored, sha256-checked copy as `new Function("only", "return (" + source +
")(only);")` and hands that to `page.evaluate` / `locator.evaluate`; it is never called in Node, only serialized to the page. Only
ever wrap the vendored copy whose hash matches, never a script fetched at run time.

## POST /v1/heal

Starts a heal for one failing test.

| Field | Notes |
| --- | --- |
| `client` | `{framework, language: "python" \| "typescript", version}` |
| `test` | `{id, title}` |
| `failure` | `{error, stack: [{file, line}]}`: the error text as the runner printed it, and the frames inside the source files |
| `source` | `{roots, files: [{path, content, sha256?}]}`: the files from the stack, relative paths only |
| `page` | `{url, language: {html_lang, browser_locale, content_language}, inventory, inventory_more, inventory_copy_more?, aria_snapshot?, viewport?: {w, h, dpr, scroll?: {x, y}}, screenshot: null \| {media_type, data, width, height}}` |
| `last_pass` | optional `{fingerprint, neighbours, language}`: what the last passing run recorded about the element (text and attributes only) |
| `options` | `{vision: "off" \| "auto" \| "on" (off), max_rounds: 1..5 (3), locale_branching: true}` |
| `test_id_attribute` | the project's test id attribute, default `data-testid`; `get_by_test_id` is written only for it |
| `run` | `{id, repo}`: the run the token budget and the metrics group by |

Inventory entries are accepted in the probe's own shape and in a compact shape (an `attrs` bag, `w`/`h` boxes).

### Screenshot

`page.screenshot` is `null` or one viewport screenshot (never a full-page one):

```json
{"media_type": "image/png", "data": "<base64>", "width": 1280, "height": 720}
```

`width` and `height` are the PNG's own size in device pixels. The service reads the type from the bytes, not from `media_type`, and
refuses (422) a file that is not a PNG, base64 that is not strict (no whitespace, no `data:` prefix, no URL-safe alphabet), a side over
8192 px, more than 16 million pixels, a size that differs from the claimed one, a PNG whose chunks do not check, and any screenshot sent
with `options.vision` `off`. Over 4 MB decoded is a 413. All of this is decided from the header before a pixel is decoded.

The probe's inventory `box` is in CSS pixels in **document** coordinates. To put a box on a viewport screenshot the service needs the
viewport and scroll it was taken in: send `page.viewport` as `{w, h, dpr, scroll: {x, y}}` from the same moment (`innerWidth`,
`innerHeight`, `devicePixelRatio`, `scrollX`, `scrollY`; `scroll` defaults to 0, 0). The scale is the image width over `viewport.w`
(the `dpr` when only that is known); a screenshot whose shape does not match the viewport within 3 percent makes no overlay.

Mask what must not be seen before sending: the service never sees an unmasked picture unless the client sends one.
`options.vision`: `off` sends no image and the service accepts none (a screenshot with `off` is a 422); `auto` lets the service use it only when the text pick fails (no
usable pick, or a pick a gate refused), is a close call (another listed element reads the same: same tag, role, copy, row and frame) or
the client's live check found the wrong element; `on` uses it for every heal that reaches the pick (testing and evaluation). When the
vision model is not configured, not allowed images, or the screenshot cannot be used, the heal goes on with text alone and the record
says why (`evidence.vision.skipped`).

### Example: a renamed id healed to the test id (`renamed_id_to_testid`)

Request (abridged to the parts that matter):

```json
{
  "client": {"framework": "example-framework", "language": "python", "version": "1.0.0"},
  "test": {"id": "tests/test_account.py::save", "title": "saves the account"},
  "failure": {"error": "playwright._impl._errors.TimeoutError: Locator.fill: Timeout 30000ms exceeded.\nCall log:\n  - waiting for locator('#username')\n",
              "stack": [{"file": "pages/account_page.py", "line": 6}]},
  "source": {"roots": ["pages"], "files": [{"path": "pages/account_page.py",
             "content": "class AccountPage:\n    def __init__(self, page):\n        self.page = page\n\n    def save(self, name):\n        self.page.locator('#username').fill(name)\n"}]},
  "page": {"url": "http://app.example/account", "language": {"html_lang": "en", "browser_locale": "en-US"},
           "inventory": [{"tag": "input", "type": "text", "role": "textbox", "name": "Username", "label": "Username",
                          "box": {"x": 10, "y": 10, "w": 200, "h": 30},
                          "attrs": {"id": "user-name", "data-testid": "login-user", "name": "user"}}]},
  "options": {"max_rounds": 3},
  "run": {"id": "run-1", "repo": "example-app"}
}
```

Response, a candidate to try:

```json
{
  "heal_id": "h_596baec1f2494f1381d91525",
  "status": "try",
  "round": 1,
  "candidate": {
    "target_ref": "e0",
    "strategy": "test_id",
    "locator": {"kind": "test_id", "chain": [{"m": "get_by_test_id", "a": ["login-user"], "o": {}, "p": false}]},
    "code": "self.page.get_by_test_id(\"login-user\")",
    "branches": null,
    "expect": {"count": 1, "ref": "e0"},
    "patch": {"file": "pages/account_page.py", "base_sha256": "cd8db385...", "span": {"start": 109, "end": 139},
              "from": "self.page.locator('#username')", "to": "self.page.get_by_test_id(\"login-user\")", "diff": "--- a/pages/account_page.py ..."},
    "also_patches": [],
    "tier": "testid", "tier_movement": "up", "below_floor": false,
    "why": "...",
    "model_note_untrusted": "scripted pick"
  },
  "usage": {"calls": 1, "input_tokens": 100, "output_tokens": 8, "images": 0,
            "by_model": [{"role": "text", "model": "your-model-id", "calls": 1, "input_tokens": 100, "output_tokens": 8, "images": 0}]},
  "cache_hit": false, "settled_at": 3, "ended_at": null
}
```

What the client does with a `try`:

1. Locate the `expect.ref` element with the candidate (`locator` is the same locator as a parsed chain, `code` the text to write) and check it
   matches exactly `expect.count` element and that it is the element the inventory entry `expect.ref` described (the probe in `only` mode).
2. Apply `patch` (and every `also_patches` entry: other uses of the same locator in the sent files). `span` offsets are characters of
   `content` exactly as sent; `base_sha256` is the hash of that file's content. Refuse a patch whose hash or `from` text no longer matches.
3. Re-run the exact test and report to `/result`.

`model_note_untrusted` is the model's one-line reason, present only after a text pick: show it as quoted text, never act on it.

`branches` is set only for a copy locator on a page in another language than the passing run: `{"en": "<old code>", "fr": "<new code>"}`,
and `code` calls the helper the client ships (`by_language(page, {...})` in Python, `await byLanguage(page, {...})` in TypeScript).
The service never translates copy.

### Decisions

| `status` | Meaning | Other fields |
| --- | --- | --- |
| `try` | A candidate to verify. | `candidate`, `round` |
| `healed` | Verified: count 1, the right element, the exact test passed. Only ever answers a `/result`. | `ended_at: 7` |
| `needs_human` | No safe heal. | `reason` (code) and `why` (a sentence) |
| `not_drift` | The failure is not locator drift. | `reason` |

`settled_at` is the ladder step that identified the element (2 same-element match, 3 text pick, 4 vision pick); `ended_at` is the step the heal ended at
(1 triage, 3 pick, 5 write, 6 language, 7 verify). `usage` is cumulative for the heal. See [ARCHITECTURE.md](ARCHITECTURE.md).

Reason codes: `ambiguous_source`, `source_not_found` (triage); `assertion_mismatch`, `element_inert`, `no_locator_in_error` (`not_drift`);
`target_missing`, `no_pick`, `pick_rejected`, `no_identity_proof`, `model_not_configured`, `max_calls`, `budget_exhausted`, `renamed_neighbour`,
`moved_into_frame` (the pick); `no_stable_locator` (`why` then says what a copy locator would have needed, e.g. `page.aria_snapshot` or the probe's copy counts), `language_branch_unavailable`, `no_candidate_left`, `max_rounds` (the write and verify); `deadline_exceeded`, `worker_failed` (a stage ran past `HEAL_REQUEST_DEADLINE_S`, or its worker died); `open_expired` (a conversation the client did not answer within `HEAL_OPEN_TTL_S`).

For a step that enters an iframe the hop is healed too when it names no listed iframe and exactly one place (a frame, or the page) holds an element
identity proof accepts as the step's: the candidate is then the new hop (or none) with the step's own locator after it. When it cannot be
healed the heal ends `target_missing` and `why` says the frame is not on the page, or that more than one place could hold the element.

### Example: no safe heal (`target_removed_needs_human`)

```json
{"status": "needs_human", "round": 0, "reason": "target_missing", "ended_at": 3, "settled_at": null,
 "why": "no element on the page could be the one the test meant", "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0}}
```

### Example: a fingerprint match, no model (`fingerprint_match_zero_tokens`)

With `last_pass.fingerprint` in the request, the answer is a `try` with `settled_at: 2` and `usage.calls: 0`.

## POST /v1/heals/{id}/result

```json
{"count": 1, "hit": {"ref": "e0"}, "test_outcome": "passed", "error": null}
```

`count` is how many elements the candidate matched in the live page, `hit.ref` which inventory entry the (single) match was,
`test_outcome` how the exact test fared on re-run (`passed`, `failed`, `failed_later`, `error`; null when it was not re-run).
`failed_later` says the healed line passed and a later line failed; it comes with
`"later": {"error": "<that failure's error, up to 4000 chars>", "file": "<its file>", "line": <its line>, "stack": [{"file", "line"}]}`
(sent with `failed_later` and only then; `stack` is the re-run failure's frames under the roots, innermost first, as
`failure.stack`). A later failure counts only when it is provably after the original one: walking both stacks from the
outermost frame, the first frame that differs is the same file on a later line. No stack, a different file at the same depth,
or an earlier line keeps nothing. The answer:

| What the client saw | The service answers |
| --- | --- |
| `count` 1, `hit` = `expect.ref`, `passed` | `healed` |
| `count` 1, `hit` = `expect.ref`, `failed_later`, provably after the original failure, and the later error is a different locator that found nothing, or it is past the healed statement's own `expect(` check (to that check's last line) | `healed` with `reason: later_locator`: the client keeps the edit and heals the next failure in a new conversation |
| `failed_later` where the later error is anything else (a value that differed, an assertion on data, several matches, the healed locator itself) | treated as `failed`: the heal may have reached the wrong element |
| `count` other than 1 | the next ranked locator for the same element, no model call (`count_mismatch_next_candidate`) |
| `count` 1 but no `hit` | the next locator (unverified) |
| `count` 1, `hit` another element | that pick is ruled out and the model is asked again (`wrong_element_repick`) |
| `count` 1, right element, test not passed | the next locator for the element |
| nothing left, or `max_rounds` reached | `needs_human` |

`healed` response:

```json
{"heal_id": "h_596baec1f2494f1381d91525", "status": "healed", "round": 1, "reason": null, "why": null,
 "usage": {"calls": 1, "input_tokens": 100, "output_tokens": 8, "images": 0, "by_model": []},
 "cache_hit": false, "settled_at": 3, "ended_at": 7}
```

404 for an unknown heal, 409 when the heal is already final.

## GET /v1/heals/{id}

The audit record: where it happened (repo, test, file, line, page path and language), the broken locator and its tier, how it ended
(`outcome`, `reason`, `settled_at`, `ended_at`, `rounds`, `candidates_tried`, `wrong_element`), what was written (`final_locator`,
`final_kind`, `tier_movement`, `below_floor`, `branch`, `covers`), the verify (`verify_count`, `verify_ref`, `verify_passed`), one row per
model call in `calls`, and one per candidate in `tries` (`result`: `ok`, `count`, `unverified`, `wrong_element`, `rerun_failed`).
It holds no file contents, page text, screenshots or model replies. `overlay` says what became of the marked image: `{"state": "kept",
"bytes": 44012, "expires_at": <epoch>}`, `{"state": "expired", "expired_at": <epoch>}` (the bytes were deleted after `HEAL_OVERLAY_RETENTION_DAYS`
or to keep the store under `HEAL_MAX_DB_MB`; the record is unchanged) or `{"state": "none"}` (no vision call made one, or images are not
kept). A record is kept for `HEAL_RETENTION_DAYS` (31 by default), then purged: this route answers 404 for it, but its counts stay in
`/v1/metrics`. `calls` has a row per model call with its `role` (`text` or
`vision`), `images` and `step` (3 or 4); when the request asked for vision, `evidence.vision` says what happened: `mode`, `ran`,
`trigger` (`close_call`, `no_pick`, `refused`, `wrong_element`, `text_none`, `on`), `images`, `marks`, `not_visible`, `result` (`picked`,
`none`, `mark_not_on_list`, `mark_not_visible`, `not_json`, `refused:<gate>`), `changed` (the vision pick replaced the text pick) and,
when it did not run, `skipped` (`vision_not_configured`, `images_not_allowed`, `no_screenshot`, `no_viewport`, `viewport_mismatch`,
`no_visible_candidate`, `no_candidates`, `undecodable` (the PNG would not decode), `overlay_error` (it decoded but drawing failed), `max_calls`, `budget_exhausted`, `image_not_kept`).

## GET /v1/heals/{id}/overlay.png

The marked image the vision model was shown for that heal: the screenshot cropped to the candidates, downscaled to at most 1024 px on the
long side, with a numbered box on each candidate (the number is the candidate's position in the list the model got). `image/png`; 404
when the heal made no vision call, this store keeps no images or its record has been purged; **410** when the image expired (its heal
record is still there, `GET /v1/heals/{id}` says `overlay.state: "expired"`). It sits behind the same token as every other route and is sent
`Cache-Control: private, no-store`. An image is kept for `HEAL_OVERLAY_RETENTION_DAYS` (7 by default), never longer than its record
(`HEAL_RETENTION_DAYS`, 31 by default); with 0 for either, no image is stored. Under `HEAL_MAX_DB_MB` the oldest images expire first.

## GET /v1/heals

The filtered list behind any aggregate, newest first, each row with `links.self`. Every number in `/v1/metrics` has a `drill` entry
holding the query string that lists exactly the heals behind it. Response: `{heals, count, total, limit, offset}` (`total` is every
heal that matches, whatever the page size).

| Filter | Matches |
| --- | --- |
| `repo`, `framework`, `language` (client language), `page_language`, `test_id`, `file`, `locator_key`, `settled_at`, `ended_at` | that value |
| `run` (or `run_id`) | one run |
| `outcome`, `reason`, `tier_movement`, `pr_state`, `mode` | one value, or several separated by commas (`pr_state=edited,reverted`). `reason=unknown` and `pr_state=none` match a record that holds none |
| `drift`, `branch`, `budget_exhausted`, `cache_hit`, `failed_again`, `below_floor`, `verify_passed` | `1` or `0` (`verify_passed=1`: the exact test went green after the patch) |
| `wrong_element=1` | a candidate landed on the wrong element and verify caught it |
| `with_calls=1` | at least one model call was made |
| `led_to_fix=1` | healed and not edited, reverted or closed unmerged |
| `model`, `role`, `local=1` or `0` | a model call by that model, in that role, local or cloud: a heal matches when any of its model calls did |
| `durability` | `pass` (merged unchanged, 10 runs and 30 days, never failed again), `fail` (failed again), `pending` (reported on, too young to judge) |
| `from`, `to` | epoch seconds or an ISO 8601 date |

Numeric filters take 0 to 2^31 (`settled_at`, `ended_at`, `offset`); a list filter takes at most 20 values; more is a 422. `sort=slowest` lists the slowest heals first. `limit` is 1 to 1000 for JSON (default 100); `offset` pages. An unusable value (a flag that is
not 1 or 0, an unknown sort) is a 422.

`format=csv` returns the same rows as a CSV download, one column per field of the record (the evidence summary is left out) and up to 20000
rows (`limit`), streamed in pages of 1000 so the store is never held for the whole export. A cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return is written with a leading single quote, so opening the
file in a spreadsheet never runs a formula. Times are ISO 8601 UTC.

## GET /v1/metrics

The headline numbers and the breakdowns behind them, with the same filters as the list (`repo`, `framework`, `language`, `page_language`,
`run`, `model`, `from`, `to`; with `model` the aggregates cover the heals in which any model call used that model, with all of their calls).
Advise-mode requests are reported on their own under `advise` and are left out of every rate. A figure with nothing
to divide by is `null`. Each definition is in `src/voyageur/metrics.py`.

**Totals outlive the records.** A heal older than `HEAL_RETENTION_DAYS` is counted into a daily, counts-only rollup before it is purged,
and every total, rate, breakdown and by-model row below adds those counts, so a number does not drop on the day its heals expire. The
rollup holds counts under a fixed counter name with the day, repo, framework, client language and page language: no file contents, page
text, locator, test name, path, run id, error text or model reply. `archive` says what it holds: `{"included": true, "heals": 120, "from":
<epoch>, "through": <epoch>, "retained_only": ["locators_fixed", "hot_spots", "runs", "latency"]}`. Those four are computed from the records
still kept because they are not sums. A drill link opens only the heals still kept. Durability is counted at the moment a heal is purged
(eligible, held, broke again, pending) and cannot be recomputed after. The rollup answers to the filters `repo`, `framework`, `language`,
`page_language`, `model` and `from` / `to` (by UTC day: a day is in the window when any of it is); a `run` filter reads the records kept
only and `archive.included` is then `false`. See [What is kept](../README.md#what-is-kept-and-for-how-long).

| Spec number | Keys |
| --- | --- |
| Failures seen, locator drift | `failures_seen`, `locator_drift`, `not_drift`, `open` |
| Success rate, fail rate by reason | `healed`, `needs_human`, `success_rate`, `fail_rate`, `needs_human_by_reason` |
| Tests fixed, locators fixed | `tests_fixed`, `locators_fixed` |
| Model calls, calls that led to a fix | `model_calls`, `calls_per_fixed_test`, `calls_that_led_to_fix`, `calls_that_led_to_fix_rate` |
| Tokens and cost per fix | `input_tokens`, `output_tokens`, `tokens_per_fix`, `tokens_per_kept_heal`, `cost`, `cost_per_fix`, `cost_by_model`, `cost_by_role` (cost only when every model used has a price in `HEAL_MODEL_PRICES`, a JSON object of price per million tokens) |
| Outcomes by model | `outcomes_by_model`: per model, `heals`, `healed`, `verified_heals` (healed, and the exact test went green), `needs_human` and `needs_human_by_reason`, `wrong_element_catches`, `model_calls`, tokens and `cost` (`null` without a price). A heal that used two models counts under both (`outcomes_by_model_note`); `outcomes_without_model` counts the heals that made no model call. Every number has a link in `drill.by_model` |
| Kept rate | `kept_rate` (merged unchanged / PRs closed), `kept`, `pr_closed`, `pr_state` |
| Durability | `durability` (`rate`, `eligible`, `held`, `pending`, `broke_again`) |
| Escaped wrong heals | `escaped_wrong_heals` (healed, then edited or reverted by a human; the target is 0) |
| Wrong-element catches | `wrong_element_catches`, `heals_with_wrong_element`, `rejected_candidates` |
| Outcome mix per run | `outcome_mix`, `runs` (healed, needs human, not drift, open and tokens per run) |
| Settled-at-step | `settled_at`, `ended_at` |
| Tier movement | `tier_movement` |
| Hot spots | `hot_spots` |
| Cost: local vs cloud, budget | `local_token_share`, `cloud_token_share`, `budget_exhausted` |
| Latency | `latency` (heal p50, p95, max; model call p50, p95, average and average by step) |
| Vision | `vision` (calls and heals; whether vision changed a pick is not recorded) |
| Language | `language` (healed heals by page language), `client_language`, `framework`, `branches_added` |
| Cache | `cache` (hits and rate) |

## POST /v1/heals/{id}/followup

What a client saw after the heal's PR, sent on a later run:

```json
{"repo": "shop-e2e", "run_id": "ci-1041", "pr_state": "merged_unchanged", "failed_again": false, "runs_since": 12, "days_since": 31}
```

`repo` is required and must be the repo of the heal record (`""` for a record that has none, which then takes only an empty repo); `run_id`
is optional and, when given, must be the record's run: otherwise 409. `failed_again` stays true once it has been reported, and sending the
same report again changes nothing (the Prometheus counters count a change of state only).

`pr_state` is `merged_unchanged`, `edited`, `reverted`, `open` or `closed_unmerged`; `failed_again` says the healed test failed again with a
locator error; `runs_since` and `days_since` count from the heal. It is stored on the heal record and may be sent again as the PR moves.
Kept rate counts `merged_unchanged` over the closed PRs. Durability counts a heal merged unchanged as held once it has lived 10 runs and 30
days without failing again. Escaped wrong heals are the `edited` and `reverted` ones. 404 for an unknown heal, 409 for a heal that did not
end `healed`, 422 for a value outside the lists or a negative count.

## Advise mode

`options.mode: "advise"` on `POST /v1/heal` returns the decision with no patch and no try rounds:

```json
{"heal_id": "h_...", "status": "advised", "candidate": null,
 "advice": {"target_ref": "e0", "strategy": "test_id", "locator": {"kind": "test_id", "chain": [...]},
            "code": "self.page.get_by_test_id(\"login-user\")", "branches": null, "tier": "testid", "tier_movement": "up",
            "below_floor": false, "why": "...", "alternatives": ["self.page.locator(\"#user\")"]},
 "usage": {"calls": 1, "...": "..."}, "settled_at": 3, "ended_at": 5}
```

Advice is **not checked against the live page** (`advice.verified` is false and `advice.note` says so): it is the best locator for the
element the page inventory shows, and only a heal's verify step proves count 1 and the right element. The same triage, pick, gates, budget and decision cache run as in heal mode; a pick a gate refuses is `needs_human`, not advice. The record
is outcome `advised`, mode `advise`; a result sent to it is a 409. Advice never fills the decision cache, because nothing was verified.
`not_drift` and `needs_human` answers are as in heal mode.

## POST /v1/audit

Rank every `page.locator` / `getBy*` call in some source files against a page inventory, with no model call and no record. The body is a heal
request without the failure: `source` (`roots`, `files`), `page` (`url`, `inventory`, `aria_snapshot`), optional `client.language` and
`test_id_attribute`, under the same limits and refusals as `/v1/heal` (input values, cookies and storage are refused; typed text is removed
from the aria snapshot).

```json
{"locators": [{"file": "pages/login_page.py", "line": 6, "span": {"start": 90, "end": 116}, "call": "self.page.locator('#user')",
               "narrowed": "", "kind": "id", "tier": 1, "copy_based": false, "matches": 1, "unique": true, "target_ref": "e0",
               "status": "upgrade", "better": {"kind": "test_id", "tier": 0, "copy_based": false,
               "code": "self.page.get_by_test_id(\"login-user\")", "locator": {"kind": "test_id", "chain": [...]}},
               "why": "a test_id locator is unique for the same element and sits above this id one"}],
 "truncated": false, "skipped_files": [],
 "summary": {"files": 1, "locators": 1, "upgradeable": 1, "by_status": {"upgrade": 1}, "by_kind": {"id": 1}, "inventory": 3,
             "inventory_complete": true}}
```

`status`: `ok` (one element, nothing more stable is unique for it), `upgrade` (`better` is the locator the writer would produce), `ambiguous`
(matches several listed elements), `positional` (a `.first`, `.last` or `.nth(n)` picks one of `positional_of` matches: it works today, but an
element added before the target would be picked silently; `better` is a stable locator for it when one is unique), `missing` (matches none), `unknown` (the inventory cannot say: a scoped chain, a regex, a frame). A `.first`,
`.last` or `.nth(n)` after a call narrows it. A call over 1000 characters is `unknown` and not evaluated. At most 500 locators are reported, and the audit stops at its own work budget (`HEAL_AUDIT_BUDGET_S`, default 5): the answer is then 200 with the rows done and `truncated: true`, never an error.

## POST /v1/overlay

Mark locators on a screenshot, with no heal, no model call and nothing stored. It is the picture a heal shows a vision model, made on request
for a person (or a tool that wants to see where its locators land): the service looks at nothing and sends nothing anywhere, so it is not
gated on `HEAL_VISION_IMAGES_ALLOWED` and takes no `options.vision` (see [SECURITY.md](SECURITY.md#overlays)). The body is the `page` of a heal request
(`url`, `inventory`, `viewport` with `scroll`, **`screenshot`, required**, and `aria_snapshot` / the probe's copy counts when the client has
them) plus:

| Field | |
| --- | --- |
| `locators` | optional list of 1 to 100 locator expressions as written in a test (`page.get_by_test_id("login")`, `this.page.locator('#go')`; no action after them), each up to 4000 characters; a `.first` / `.last` / `.nth(n)` narrows as in an audit. Over 100 is a 413. Omit it to mark every element (below). |
| `options.labels` | `numbers` (default): a numbered tab on each box, the legend maps number to locator. `locators`: the locator text is drawn on the tab instead of the number where there is room for it, else the number alone (see below). |
| `options.kinds` | with no `locators` only: `["interactive"]`, `["text"]` or both (the default, everything). With `locators` it is a 422. |
| `client.language`, `test_id_attribute` | as in an audit: the language the service writes locators in (default `python`) and the project's test id attribute. |

The screenshot passes the same door as a heal's (4 MB, 8192 px a side, 16 million pixels, PNG signature and chunks, dimensions equal to the
claim) and `page.viewport` places the probe's document-coordinate boxes on it exactly as for a heal. Input values, cookies and storage are refused
as everywhere. A picture whose shape does not match its viewport (within 3 percent), a request with no viewport size or `dpr`, or a PNG that
passes the door but cannot be decoded is a 422 that names the reason.

**With locators**, each is resolved against the inventory by the matcher an audit uses and marked. Its number is its position in the request
(1 is the first), whatever its status. **With none**, every listed element that is not hidden and is on the picture is marked, numbered in
listing order, and labelled with the locator the service would write for it (the same candidates, uniqueness proof and stability ranking a heal
uses). That is every element a test could target, not only controls: headings, labels, paragraphs and status text, and the sections and
landmarks the probe lists, as well as inputs, buttons and links. A text element keeps the ranking every other element has: a test id, a stable id, a
name or another stable attribute wins, and only when it has none does the service use copy. Then it is **role and name** first (a heading is
`get_by_role("heading", name="Profil", level=2, exact=True)`, the level from its tag; a landmark an author named, such as `nav aria-label="Main"`,
is its role with that name), then **exact text** (`get_by_text("Profil", exact=True)`). A paragraph, a list item or a plain label has no
accessible name from its content, so it is text, never a role. A copy locator is written only when the request proves it unique: a role locator
needs `page.aria_snapshot` (counted by role, exact name and level) and a text locator needs the probe's copy counts (`inventory_copy_more`,
`inventory_copy_withheld`, `inventory_copy_withheld_data`). When neither can be shown the element is marked `no_stable_locator` and the
entry says why in `reason` and `why`; the service never guesses a copy locator. A copy locator has `copy_based: true` and is written in the
page's own language (it is never translated: a heal that moves a copy locator to a page in another language returns `branches`, one locator
per language). Elements inside a frame are not marked.

```json
{"image": {"media_type": "image/png", "data": "<base64>", "width": 240, "height": 180},
 "labels": "numbers",
 "legend": [{"number": 1, "locator": "self.page.locator('#pwd')", "label": null, "label_truncated": false, "number_only": 0, "status": "matched",
             "count": 1, "ref": "e1", "refs": ["e1"], "visible": 1, "color": "#c81e1e", "kind": "id", "tier": 1, "copy_based": false,
             "element_kind": null, "reason": null, "why": null},
            {"number": 2, "locator": "self.page.locator('#ghost')", "status": "not_found", "count": 0, "ref": null, "refs": [], "visible": 0, "color": null, "...": "..."},
            {"number": 3, "locator": "self.page.locator('input')", "status": "ambiguous", "count": 2, "ref": null, "refs": ["e0", "e1"], "visible": 2, "color": "#1e3cc8", "...": "..."}],
 "summary": {"locators": 3, "marked": 2, "not_visible": 0, "by_status": {"matched": 1, "not_found": 1, "ambiguous": 1},
             "by_element_kind": {}, "number_only": 0, "inventory": 3, "inventory_complete": true},
 "truncated": false}
```

The answer is JSON, not a bare PNG, because a caller needs both the picture and the legend: the image has the shape of a request's
`page.screenshot` (a client that already holds the base64 helpers decodes it the same way) and the response carries `Cache-Control: private, no-store`. The
picture is the whole viewport, downscaled (never enlarged) to at most 1600 px on its long side; marks are drawn after the scale, so a line and a label are the same size
whatever the viewport. The same request gives the same bytes on one Pillow version.

`status`: `matched` (exactly one listed element, on the picture), `not_visible` (exactly one, but it has no box or is mostly outside the screenshot: no
mark), `ambiguous` (`count` matches; every one the picture holds, at most 50, is marked with the locator's number and color), `not_found`, `unknown` (the inventory cannot say: a
scoped chain, a regex, a frame, a call over 1000 characters), and, with no locators only, `no_stable_locator` (the element is marked, and has no unique stable
locator: `locator` is null, `reason` is the first thing the request lacked and `why` says all of it, never quoting page copy). `reason` is one of
`aria_snapshot_missing` (no `page.aria_snapshot`, so a role and name cannot be counted), `aria_snapshot_unusable` (over its size limits),
`copy_counts_missing` (the request has no copy counts), `listing_incomplete` (the inventory is cut, or the probe left copy out), `copy_not_unique`
(its copy is on more than one element), `not_matched`, `unprovable`, `nothing_stable` (no stable attribute and no copy to name it by) or
`unwritable`; it is null when a locator was written. `ref` is the element when exactly one is matched, `refs` the listed elements matched, `visible` how many of their boxes were drawn.
`kind` and `tier` give the locator's stability rank (`test_id` 0, `id` 1, `name` 2, `css` 3, `role` 4, `label` 5, `placeholder` 6, `text` 7, `chain` 8: lower is more stable)
and `copy_based` whether it depends on page copy. With no locators `element_kind` says `interactive` (a form control, a button, a link, an element with a control's role, or
one a handler acts on) or `text` (everything else: headings, labels, paragraphs, status text, sections and landmarks), and `summary.by_element_kind` counts them.

**Overlapping boxes.** Boxes are drawn biggest first, so a section lies under what it holds. A number whose box overlaps a lower number's box takes the next free color
(the legend's `color` is the one on the picture), a box that sits just inside another is drawn one outline's width further in, and the tabs are placed biggest box first.

**Where a tab goes.** A tab sits outside the box it belongs to (above, below or beside its outline), so it never covers the element's own content, and it covers no other marked
box and no other tab. Places are tried in a fixed order: above and below the box (left aligned, right aligned, centred), then beside it (top aligned, bottom aligned); the first one inside the picture that
is clear wins, and nothing is cut off or drawn off the edge to make one. A number with no clean place may lie in the gaps inside the boxes that hold its element (the tab of a field in the section around
the field), still clear of every other box and tab; a line of locator text never does, since the holding box has content of its own. When even that fails (boxes that touch on every side) the number goes where it
covers the least (px of other boxes and tabs under it, twice that for its own box; above, below, inside the top, inside the bottom, in that order, break a tie), so a mark is never dropped. Only marked boxes are
avoided: page text that is not marked can still lie under a tab.

**Label text.** With `labels: locators` the tab holds the locator itself, drawn from a built-in 5x7 bitmap font for printable ASCII (no font file, no platform font, so the bytes
are the same everywhere). The drawn text is only the locator: ASCII control characters are removed, any other character is drawn as `?`, it is cut to 48 characters with `...`, and
to what fits the picture's width. `legend[].locator` is always the whole locator; `label` is what is on the picture and `label_truncated` says it is shorter. A locator with no box
(not found, not visible) is drawn nowhere. With no locators, an element with no stable locator keeps its number as its tab.

**When the text does not fit.** A line of text is wider than a number, so it may have no place outside its box that covers nothing. The tab then holds the box's **number alone**
(the legend is the key: `locator` is whole and the number in the picture is its `number`). The legend says so: `legend[].number_only` is how many of the entry's boxes were drawn as the number
because the text had no place (0 when the text was drawn; always 0 with `labels: numbers`), `label` is `null` when the number was drawn for all of its boxes, and `summary.number_only` counts the
entries with at least one such box. A locator that matches several elements is placed box by box, so some of its boxes may show text and others the number. An entry with no unique locator
(`locator` null) keeps its number as always and is not counted as a fallback.

`429` (with `Retry-After`) when every slot is taken: an overlay shares the audit's slots (at most workers minus 1 at once, so a heal always gets a worker). The work is done in the killable worker
under the request deadline: past it the answer is a 504 (a worker that died, a 503). The matching stops at `HEAL_AUDIT_BUDGET_S` with `truncated: true` and the legend of the locators done.
At most 600 boxes are drawn on one picture; a locator whose boxes did not all fit is still reported (`visible` says how many were drawn, its status is never turned into `not_visible` by the cap) and `truncated` is `true`.

## POST /v1/diagnose

For a failure that is **not** locator drift: the locator still matches and something else is wrong (an expectation, the page, a wait, the test's
data, a step, or an earlier heal that pointed at the wrong element). **Off by default**: set `HEAL_DIAGNOSE=on`. With it off the route answers
404 with `{"status": "needs_human", "reason": "diagnose_off", "why": "..."}`, reads nothing (the body is not even parsed) and calls no model (`GET /v1/capabilities` says
`diagnose.enabled`). Read [SECURITY.md#diagnose](SECURITY.md#diagnose) before turning it on: error text, aria text and `expect(...)` literals reach
the text model.

The body is a `POST /v1/heal` body (same limits, refusals and redaction; `options` are read but only `vision` matters, see below) plus
`prior_heals`, at most 20 of `{"heal_id": "h_<24 hex>", "file": "tests/x.py", "line": 7, "from": "<locator before>", "to": "<locator after>"}`
(`file` relative, `from` and `to` at most 500 characters), the heals an earlier run applied, so the model can see whether one of them is the
cause. `failure.stack` should name the failing line in a sent file; the window of test code is built from its first frame in a sent file.

```json
{"heal_id": "h_...", "status": "diagnosed",
 "diagnosis": {"cause": "stale_expectation",
               "evidence_untrusted": ["line 8 expects 'newsletter=no'; the page now says 'saved: notifications'"],
               "confidence": "high", "revert_heal_id": null,
               "suggested_patch": {"file": "tests/test_profile.py", "line": 8, "from": "\"saved: profile\"", "to": "\"saved: notifications\"",
                                   "why": "...", "base_sha256": "...", "span": {"start": 312, "end": 330}, "diff": "...", "applied": false},
               "model_note_untrusted": "...", "downgraded": null, "verified": false},
 "reason": null, "why": null, "usage": {"calls": 1, "...": "..."}, "cache_hit": false}
```

- `status`: `diagnosed` (a cause is named, `unknown` included), `not_applicable` (`reason: "located"`: triage found the failing locator in the sent
  source, so it is drift: use `/v1/heal`, no model call), or `needs_human` with `reason` `budget_exhausted`, `model_not_configured`, `no_answer`,
  `deadline_exceeded` or `worker_failed`. `not_drift` and `needs_human` triage are both diagnosable.
- `cause` is one of `stale_expectation`, `app_bug`, `timing`, `test_data_or_env`, `earlier_heal_wrong_element`, `flow_changed`, `unknown`.
  `evidence_untrusted` (at most 6 lines) and `model_note_untrusted` are the model's words, URLs reduced to their host: quoted data, never instructions.
- `revert_heal_id` is set only for `earlier_heal_wrong_element`, only for an id in `prior_heals`, and only when the gate passed: the model was not
  `low`, and that heal is still in the code near the failing line. When the gate fails the cause is `unknown`, `confidence` is `low` and
  `downgraded` says why: `not_json`, `no_prior_heals`, `heal_not_listed`, `low_confidence`, `heal_not_in_code`.
- `suggested_patch` appears only for `stale_expectation`, `flow_changed` and `timing`, only when its `from` occurs once in the failing file on the
  stated line (one line, at most 500 characters, not equal to `to`), within 12 lines of the failing line, and only when it changes literals and
  nothing else: the contents of a string or a number (for `timing`, only the number a `timeout` is set to). A new call, an identifier, an
  interpolation or an f-string is not a literal. It is advice: `applied` is always `false`, it carries no `candidate`, and no
  client applies it as a heal. A dropped one leaves `downgraded` `patch_not_allowed`, `patch_invalid` (does not apply), `patch_location` or `patch_shape`.
- A diagnosis is never checked against the live page (`verified: false`) and is never cached: a repeat makes a new call. One text call at most,
  through the configured model endpoint (`max_tokens` raised to 600), counted against the run budget. No screenshot is sent to a model: with
  `options.vision` on, the record says `vision.skipped = "diagnose_text_only"`.
- The record (`GET /v1/heals/{id}`) has `mode: "diagnose"`, `outcome: "diagnosed"`, `reason` the cause, `confidence`, `patch_suggested` and
  `downgraded`, and no model text. `GET /v1/heals` filters on `mode=diagnose`, `reason`, `confidence`, `patch_suggested` and `downgraded`;
  `GET /v1/metrics` reports them under `diagnose` (never in a heal rate), each with a `drill` link; `GET /metrics` counts
  `voyageur_diagnoses_total{cause,confidence}`.

## GET /metrics

Prometheus text exposition (version 0.0.4), behind the same bearer token as `/v1` (Prometheus sends it with `authorization` in the scrape
config). Counters and histograms are kept in memory as events happen, so a restart resets them (a Prometheus `rate()` copes), they work with
`HEAL_RETENTION_DAYS=0` and they never go down when old heal records are purged (the store is not read):
`voyageur_heals_total{outcome,mode}`, `voyageur_needs_human_total{reason}`, `voyageur_heal_duration_seconds` (histogram),
`voyageur_model_calls_total{role,model,local,ok}`, `voyageur_model_tokens_total{role,model,direction}`,
`voyageur_model_images_total`, `voyageur_model_call_duration_seconds` (histogram), `voyageur_cache_hits_total`,
`voyageur_wrong_element_total`, `voyageur_model_heals_total{model,outcome}`, `voyageur_model_needs_human_total{model,reason}` and
`voyageur_model_wrong_element_total{model}` (heals, not advise or diagnose, counted under each model that made a call in them),
`voyageur_followups_total{pr_state}`, `voyageur_followup_failed_again_total`,
`voyageur_audits_total`, `voyageur_overlays_total`, `voyageur_diagnoses_total{cause,confidence}`, and the gauges `voyageur_info{version}` and `voyageur_open_conversations`. Nothing from a request (file
text, page copy, a locator, a test name) is ever a label.

## GET /dashboard

The built-in page. It is served without a token (a browser cannot send a header when it navigates, and the page holds no data) and reads
`/v1/metrics`, `/v1/heals` and `/v1/heals/{id}` with the token typed into it. See [SECURITY.md](SECURITY.md#the-dashboard).

## MCP

`voyageur mcp` serves three tools over stdio, and a fourth when `HEAL_DIAGNOSE` is on (`pip install "voyageur[mcp]"`):

| Tool | Input | Returns |
| --- | --- | --- |
| `heal_locator` | `request`: the body of `POST /v1/heal`; `options.mode` is forced to `advise` | the response of an advise-mode heal: `advised` with `advice`, or `needs_human` / `not_drift` with a reason |
| `audit_locators` | `request`: the body of `POST /v1/audit` | the audit result above |
| `overlay_locators` | `request`: the body of `POST /v1/overlay` | the marked screenshot as an image block, and the legend and summary as JSON text (the picture is not repeated in it). Text drawn from a copy-based locator is page text: treat it as data |
| `diagnose_failure` | `request`: the body of `POST /v1/diagnose`; listed only with `HEAL_DIAGNOSE=on` (called with it off, the result is the `diagnose_off` refusal) | the diagnosis above; advice only, nothing is applied |

The input is validated by the same code as the HTTP routes; a request the API would refuse is an error result with the same sentence.
