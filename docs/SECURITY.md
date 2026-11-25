# Security notes

## Network exposure

The service speaks plain HTTP and authenticates with a bearer token (`HEAL_SERVICE_TOKEN`). It binds to loopback by
default. A bind to any other address needs both `--allow-remote` and a token of at least 32 characters (there is no rate limit
on failed tokens, so the token must be too long to guess: `python -c "import secrets; print(secrets.token_urlsafe(24))"`), and it must sit behind a TLS reverse
proxy: without TLS the token and the page inventory (element names and text from the page under test) cross the
network in the clear. Terminate TLS at the proxy, forward only to the service's port, and keep the service port itself
closed to other machines.

## Client data is untrusted

Everything a client sends (source files, the failure text, the inventory, the aria snapshot) is data, never code or
instructions.

- **Regular expressions.** A quoted string in a locator is never a pattern. Only a bare `/body/flags` literal in
  TypeScript source, or a Python `re.compile(...)` argument, is one, and it is run by RE2 (google-re2), which has no
  backtracking and runs in linear time. A pattern RE2 cannot run (lookahead, backreference) or that breaks a limit below is "cannot evaluate": no uniqueness claim is made, and the heal is refused or goes to a human.
- **Scanning.** The locator scanner reads each call within 4 KB and the whole file within a fixed character budget, so
  a file of unclosed calls cannot make a request quadratic. A request is also limited to 1 MB of source in total.
- **Typed values.** Values never leave the client: `value` keys, cookies and storage are refused, typed text in an aria
  snapshot is removed (every key spelling, textarea, nested text children), and string literals handed to `fill`,
  `type`, `press`, `pressSequentially`, `setInputFiles` and `select_option` are replaced with `<redacted>` in the
  source lines sent to a model.
- **Page text in the inventory.** The probe sends the visible copy of controls and hooked elements and, for text locators, of elements
  that hold copy (`copy_only`). `HEAL_MASK` blacks out a screenshot only: it does not mask inventory text. The probe's own filters
  are the protection. It never reads a form control, a `textarea`, a `select`, an `output` or an editable region, nor the copy
  inside a text-entry role; a `copy_only` item is never taken from inside a grid, list, tree, option, table row or cell. The
  `text`, `name`, `label`, `aria_label`, `placeholder`, `title` and `alt` of every item are withheld (sent empty) when they hold an
  e-mail address, a token-like word (24+ characters with a digit and a letter) or a run of nine or more digits (six for a `copy_only`
  item; digits of any script, one separator between digits allowed), and so is `pw_text`. A `copy_only` item is not listed
  when its copy is over 80 characters. A read of copy is bounded in length, depth and nodes. What is withheld is counted
  (`copy_withheld`, `copy_withheld_data`, `copy_more`) so the service never calls a text unique from a listing that is missing copy.
  What remains: ordinary page copy that passes the filters (names, headings, amounts) is sent, and up to the candidate cap of it
  reaches the model prompt; a code split one digit per element (an OTP in six boxes) or with separators such as `123 - 456` is
  not a run the filter sees; role widgets (`grid`, `listbox`, `tree`) are themselves listed with their first 80 characters of
  copy, as any element with a role is, and so is an option that carries an id, a `data-*` attribute or a role (a custom
  dropdown's options do): its label passes the same filters, but a name or a street address is not something they catch. A page that must not leave the browser needs a client that does not run the probe on it.
- **Counts from the listing choose candidates; the live check decides.** A count or a match read from the inventory (a text, an
  attribute, a label) is a guess at what the page holds: it can be short, since the listing leaves out copy it withholds or has no
  room for. It can pick a candidate or rule one out, never keep a heal. A heal is kept only when the client's own live check finds
  one match, the element the service picked, and the test passes (`gates.judge_result`). What the listing can do wrong is a decision
  about WHICH element is the pick (identity proofs, a frame placement, a renamed row): those fail closed when copy may be missing
  (`copy_more`, `copy_withheld_data`; a client that sends no counts with a `copy_only` listing is read as not clean).
- **Invisible characters.** Format, separator and control characters (bidi overrides and isolates, zero-width,
  U+2028 / U+2029) are removed from everything put in a model prompt, and written as `\uXXXX` escapes in every
  locator the service writes.
- **Model output.** In a heal the model only chooses a listed ref. Its one-line reason is returned as `model_note_untrusted`:
  show it as quoted text and never act on it. `POST /v1/diagnose` is the one route where the model writes more than a ref: see
  [Diagnose](#diagnose).

## Screenshots

A screenshot is untrusted bytes headed for an image decoder, and the most sensitive thing a client can send (it shows the page). The
rules, in the order they are applied, cheapest first:

1. **Off means off.** `options.vision` `off` with a screenshot in the request is refused (422): the client and the operator disagree
   about what leaves the machine. With vision off no image is accepted at all. When vision is on but cannot run (no vision model, images not allowed), a screenshot is still checked at the door (rules 2 to 5, no pixels decoded) and is then never decoded, drawn, kept or sent.
2. **Size before decode.** The base64 text is bounded (over 4 MB decoded is a 413) before it is decoded; the alphabet and padding are
   strict (no whitespace, no `data:` prefix, no URL-safe variant).
3. **The type is read from the bytes.** `media_type` must be `image/png`, and the file must start with the PNG signature and an IHDR
   chunk. Pillow is told to open PNG only (`formats=["PNG"]`), so a JPEG, GIF, SVG, WebP or PDF is never handed to its decoders.
4. **The header decides the size, with no decoder.** Width and height are read from IHDR: each at most 8192, the product at most 16
   million pixels (a 2560 x 1440 viewport at dpr 2 fits), and both equal to what the client claimed. A pixel bomb is refused here.
5. **Integrity.** Pillow's own decompression-bomb limit is set to the same 16 million pixels and its warning is an error; `verify()`
   checks every chunk's CRC. A truncated or corrupt file is refused. Pixels are decoded only later, in the killable worker under the
   request deadline.
6. **No image is logged or echoed.** The base64 field is excluded from model reprs, error bodies name the rule and never the input,
   the heal record holds no screenshot, and logs hold counts only.

Masking is the client's job and is done before the screenshot leaves the browser (password inputs and anything the project lists in
`HEAL_MASK` are blacked out by Playwright's `mask`); the service cannot un-send a field a client did not mask. A client that cannot mask
sends no screenshot.

**An image goes only where it is allowed.** The vision role never inherits the text role's endpoint: it has its own settings, and an
unset one means vision is unavailable. An image is sent only to a vision endpoint whose `HEAL_VISION_IMAGES_ALLOWED` is true (checked
twice: before the overlay is built, and again when the request is built, which refuses an image for any other endpoint). The egress
policy applies on every call exactly as for text: https anywhere, plain http only to a loopback or private-network host, no proxy from
the environment. A cloud vision model needs both settings on purpose. The call goes through the operator's configured proxy or gateway
like any other, never around it.

**What the model sees.** The marked image (a crop of the client's screenshot with digits and colored outlines drawn by the service; no
page text is drawn) and the same quoted, capped, control-character-free candidate lines as the text pick. Text inside the screenshot is
the page's own and is as untrusted as any page text: the model can only answer with a mark number, a mark that is not on the list (or not
drawn) is refused, and the pick then passes the same gates and identity proof as a text pick before anything is written. A page that
paints "pick button 3" in its pixels can steer which listed element is picked, never what locator is written and never past a gate.

**The overlay is kept, and expires.** The marked image is stored with the heal record so a reviewer can see what the model saw, behind
the same bearer token as every other route (`GET /v1/heals/{id}/overlay.png`, `Cache-Control: private, no-store`). It is purged with its
heal by `HEAL_RETENTION_DAYS`, and with `0` it is not stored at all. The client's original screenshot is never stored: it is held in
memory only while an open conversation may still need a vision call (at most 64 MB in all) and dropped after.

## Overlays

`POST /v1/overlay` (and the `overlay_locators` MCP tool) marks locators on a screenshot and answers with the marked picture and a legend. It is a separate route, not
an option of `/v1/audit` or `/v1/heal`, so what it may and may not do is stated once:

- **No model call, ever.** The code path has no model client: the picture is drawn in the service's own worker and returned to the caller that sent it. Nothing is
  sent to any endpoint, so the screenshot rules that exist to control egress do not apply, and the route is **not gated on `HEAL_VISION_IMAGES_ALLOWED`** (nor on a configured
  vision model, nor on `options.vision`, which the request does not have). That setting decides whether an image may be *sent to a model*; an overlay sends none. The
  "off means off" rule above is about the same thing and is not weakened: a heal request with vision off and a screenshot is still a 422.
- **The same door.** The screenshot goes through every layer of [Screenshots](#screenshots) before a pixel is decoded: 4 MB decoded (413), strict base64, PNG read from the
  bytes, a side of at most 8192 px and at most 16 million pixels from the header, dimensions equal to the claim, CRC check. The body limit (10 MB), the 413 / 422 rules, the
  refusal of `value`, cookie and storage keys, the removal of typed text from the aria snapshot and the bearer token are those of every other `/v1` route.
- **Nothing is kept.** No heal record, no overlay row (`overlays` holds a heal's vision overlay only), no cache, no log of the image or of a locator; the counter
  `voyageur_overlays_total` counts requests and nothing else. The answer carries `Cache-Control: private, no-store`. With `HEAL_RETENTION_DAYS=0` nothing changes.
- **Killable and bounded.** Decoding, matching and drawing run in the worker pool under the request deadline (a worker that outlives it is killed: 504; one that dies is a 503), in one
  of the audit's slots (429 when taken, so a heal always gets a worker). At most 100 locators of 4000 characters, 50 boxes per locator, 500 inventory entries; the picture is
  downscaled to at most 1600 px on its long side.
- **Drawn text is untrusted, and bounded.** The only text ever drawn is a locator, from the built-in bitmap (no font file, no font parser). It is cleaned first: ASCII control
  characters removed, any other character drawn as `?` (a right-to-left override or a look-alike letter cannot be drawn), cut to 48 characters and to the picture's width. A locator the
  caller sent is the caller's own text. A locator the service wrote can hold page copy (`get_by_text("...")`, a role name), and that copy is drawn on the picture: the legend marks it
  `copy_based`. A caller that shows the picture to a model (the MCP tool returns it as an image) is showing it page text, which is data and never an instruction. The service
  itself reads nothing back from the picture.
- **Image decoding.** Pillow opens PNG only (`formats=["PNG"]`) and any failure while decoding is a 422 with the reason `undecodable`, never an echo of the bytes. A picture whose shape does
  not match its viewport (3 percent) makes no overlay.

## Resource limits

Every input is bounded before it is scanned, and an input over a limit is refused or marked, never silently truncated.

| Input | Limit | Over the limit |
| --- | --- | --- |
| request body | 10 MB | 413 |
| source files | 20 files, 256 KB each, 1 MB in total | 413 |
| inventory | 500 entries; a field is at most 400 characters | the field is dropped and the inventory is marked incomplete |
| aria snapshot | 256 KB, 8 KB per line | 413 |
| screenshot | 4 MB decoded; 8192 px a side; 16 million pixels | 413 over 4 MB; 422 otherwise |
| overlay locators | 100 locators, 4000 characters each; 50 boxes drawn per locator; drawn text 48 characters | 413 over 100; 422 over 4000 characters |
| error text | 64 KB in total, 8 KB per line | 422 |
| a locator expression | 4000 characters | "cannot evaluate" |
| a regular expression | RE2 only; pattern over 1000 characters, repeat-bound product over 100, text over 4096 characters, or a work budget over 1,000,000 characters | "unknown": no uniqueness claim is made; never truncated |

The CPU-heavy stages of a heal run in a killable worker pool (`HEAL_WORKERS`, default 2; `0` runs them in the service process) under a
per-request deadline (`HEAL_REQUEST_DEADLINE_S`, default 10). A heal that runs past it ends `needs_human` with `deadline_exceeded`,
and a worker that dies ends it with `worker_failed`; the worker is replaced. A hostile file can therefore cost one request its answer,
not the service its availability.

## Memory and retention

An open conversation keeps only what a result round needs (not the request), at most 500 are held, and one the client
has not answered for `HEAL_OPEN_TTL_S` seconds (default 900) is closed with the reason `open_expired`. Stored records
are purged by age (`HEAL_RETENTION_DAYS`) on every read as well as on every new heal. `/v1/metrics` reads at most the
newest 20000 heals and says `truncated` when more matched.

## The dashboard

`GET /dashboard` is served without a token, because a browser cannot attach an `Authorization` header when it navigates and the page holds no
data and no secret. Everything it shows comes from `/v1/metrics`, `/v1/heals` and `/v1/heals/{id}`, which still need the token.

- **Token handling.** The person types the token into the page. It is kept in `sessionStorage` (this tab, gone when it closes), sent only in an
  `Authorization` header on `fetch` calls to the same server with `credentials: "omit"` and no referrer, and never put in a URL, a cookie or the
  location hash. The CSV export is fetched with the header and saved from a blob, not linked by URL.
- **Offline, no third party.** One file with inline script and style: no CDN, font, image or analytics request. The response carries a
  Content-Security-Policy of `default-src 'none'`, the page's own script and style allowed by SHA-256 hash (computed from the file),
  `connect-src 'self'`, `img-src data:`, no frames, no forms and no base tag; with `nosniff`, `no-referrer`, `no-store` and `X-Frame-Options: DENY`.
- **Records are page copy and source text** (locators, test ids, reasons, page paths). The page inserts them with `textContent` only: it never
  turns a string into markup, and a test fails if the page uses `innerHTML`, `eval` or the like, and drives a record whose every text is a script
  tag to check it stays text.
- **CSV cells** that start with `=`, `+`, `-`, `@`, a tab or a carriage return are written with a leading single quote.

## Audit, advise, follow-ups and metrics

- `POST /v1/audit` and the MCP tools take the same bodies under the same limits, refusals and redaction as `POST /v1/heal`; the audit runs in the
  killable worker pool under the per-request deadline and reports at most 500 locators.
- `POST /v1/overlay` and `overlay_locators` take the same limits and refusals and run in the same pool and slots; see [Overlays](#overlays) for what they never do.
- `heal_locator` (MCP) forces advise mode: it can never open a conversation or return a patch. A stdio MCP server has no bearer token because only
  the process that launched it can reach it; it keeps its record in `HEAL_DB` like `serve`.
- `POST /v1/heals/{id}/followup` takes a fixed set of PR states and two bounded integers, and only for a heal that ended `healed`.
- `GET /metrics` needs the bearer token. Its labels come only from fixed codes and the operator's own model names; nothing from a request is ever a
  label, so a hostile file or page cannot create a series or inject exposition text (label values are escaped as well).
- `GET /v1/heals` filters are bound parameters (a column name comes only from a fixed list); an unusable value is a 422.

## Diagnose

`POST /v1/diagnose` (and the MCP tool `diagnose_failure`) is **off by default**: with `HEAL_DIAGNOSE` unset it answers 404 `{"status":
"needs_human", "reason": "diagnose_off"}`, reads nothing (it does not parse the body), writes no record and calls no model. It is the one route that sends text a heal never
sends: the error text, the aria snapshot and `expect(...)` literals reach a model. Turning it on is an operator decision, as pointing the text
model at a cloud endpoint is.

- **What is sent, text only.** The test code (a window of 12 lines round the failing line, string literals of `fill` / `type` / `press` and the
  like are `<redacted>`), the first 20 lines of the error (200 characters each, URLs reduced to their host), the heals earlier runs applied, the
  page path (no query), its language, up to `HEAL_CANDIDATE_CAP` element lines and the aria snapshot cut to 4000 characters, in a prompt of at most
  24000 characters. No screenshot is sent to a model: `vision` in the request is read, the record says `vision.skipped = "diagnose_text_only"`, and
  the image is not decoded.
- **The value filter.** Before the prompt is built, the error text, the aria snapshot, the code window and every element line first lose invisible
  characters (Unicode tag characters, bidi controls, zero-width characters), then Bearer / Basic credentials, configured keys and URL paths and
  queries are scrubbed, and an absolute path of a machine (`/Users/...`, `/home/...`, `C:\\...`) is cut to its file name. Then the same shapes the probe
  withholds: an e-mail address, a run of six or more digits (digits of any script, one space, no-break space, dot, dash or underscore between digits
  allowed) and a token-like word (24+ characters with a digit and a letter) become `<withheld>`; in code lines the address and token rules apply inside
  string literals only. A `Received:`, `Expected:` or `Actual value:` value over 80 characters is cut together with its continuation lines. In code,
  a literal assigned to a name that looks like a credential (pass, secret, token, key, cred) is `<redacted>`, as are `fill` / `type` / `press` literals. `expect(...)` literals of ordinary length stay, because a stale expectation cannot be told without them. What remains is what
  the filters cannot see: a name, a street address, an amount, ordinary copy.
- **Untrusted in, untrusted out.** Page, code and error text are quoted data and the prompt says they are not instructions. The reply must be one JSON
  object: an unknown key, a cause outside the list, an overlong field or any other text is `unknown` with `downgraded: "not_json"`. `evidence_untrusted` and
  `model_note_untrusted` are the model's words (URLs reduced to their host): show them as quoted text and never act on them.
- **Checks the model cannot talk its way past.** `earlier_heal_wrong_element` stands only when the model named an id the client sent in `prior_heals`,
  its confidence is not `low`, and that heal is still in the code near the failure (a sent file, a stack frame in it within 12 lines of the heal, the
  healed text on the line the heal names). Otherwise the answer is `unknown` and `downgraded` says why (`no_prior_heals`, `heal_not_listed`,
  `low_confidence`, `heal_not_in_code`), and `revert_heal_id` is only ever set when it stands. A suggested edit is kept only for `stale_expectation`,
  `flow_changed` and `timing` (for a bug or a bad heal it would hide the failure: `patch_not_allowed`); only if it applies (`from` occurs once in the
  failing file, on the stated line, on one line, differs from `to`, no control or invisible character: `patch_invalid`); only within 12 lines of the
  failing line of the failing file (`patch_location`: no edit of a config or another test); and only if it changes literals and nothing else (the
  contents of a string or number; for `timing` only a `timeout` number), so a steered model cannot turn an assertion into a call or add an
  interpolation (`patch_shape`). In a Python f-string only the text between replacement fields counts as a literal: every `{...}` field must
  stay exactly as it was, none added or dropped, and an f-string whose fields hold braces of their own (a nested format spec) cannot be edited
  at all; a JavaScript `${` in the new text is refused outright. It comes back as
  `suggested_patch` with `applied: false`: it is not a `Patch` and no client applies it as a heal.
- **What this cannot tell.** A prior heal that is still in the code next to the failing assertion is a candidate for `earlier_heal_wrong_element`
  whether it was wrong or right (a right heal followed by a stale expectation looks the same from the text). The answer is advice with a confidence,
  never verified (`verified: false`); a person or the client's live check decides before anything is reverted.
- **Nothing is cached, and no model text is stored.** A diagnosis is unverified, like advice, so it never fills the decision cache and a repeat makes
  a new call. The record (mode `diagnose`, outcome `diagnosed`, the cause as its reason) keeps the cause, the confidence, counts, whether an edit or a
  revert was suggested and why the answer was changed; never the evidence, the note or the edit. It goes through the same proxy path as every model
  call, is counted against the run's token budget and `HEAL_MAX_CALLS`, and is reported under `diagnose` in `/v1/metrics`, never in a heal rate.

## Open mode, content type, audits and follow-ups

- **Open loopback mode.** With no `HEAL_SERVICE_TOKEN` the service accepts only a `Host` of `127.0.0.1`, `localhost` or `[::1]` (any port) or
  the address it was told to bind, so a web page cannot reach it through a DNS name rebound to loopback. Every POST must carry
  `Content-Type: application/json`, so a cross-site form or a "simple" request cannot trigger one. The Python and TypeScript clients send it.
- **The API page.** `GET /docs` (Swagger UI), its three assets under `/docs/` and `GET /openapi.json` are served without a token, like the
  dashboard: they are static files from the package and the OpenAPI document, which holds routes, schemas and the contract fixtures as
  examples, never a setting, an endpoint, a model name or a key. The assets are looked up by name in a fixed list (no path is joined). The
  page loads nothing from anywhere else (`script-src 'self'`, `connect-src 'self'`, no frames, `frame-ancestors 'none'`); `style-src` allows
  inline styles because Swagger UI sets them on elements, with no script able to run from them. "Try it out" sends only to this service, and
  a token typed into Authorize stays in the page's memory (`persistAuthorization` off). The Host check above covers these routes too.
- **Audit work.** An audit has its own work budget (`HEAL_AUDIT_BUDGET_S`) and answers with the rows done and `truncated: true` past it. At
  most `workers - 1` audits run at once (a single worker cannot reserve one), so a heal can always get a worker; the rest are 429. A call over
  1000 characters is not evaluated. The MCP `audit_locators` tool takes the same path, and a decoded MCP body is held to the same 10 MB total.
- **Follow-ups belong to their record.** The body must name the heal's repo (and, optionally, its run); a mismatch is a 409. `failed_again`
  cannot go back to false, and a repeated report is a no-op for the counters.
- **Filters and export.** Numeric filters are bounded, list filters capped at 20 values, the CSV is streamed in pages, and a cell is quoted when
  it starts with a formula character after any leading whitespace (newline, vertical tab, NBSP, zero-width characters included).
- **Advice is unchecked.** The advise response says so (`verified: false`, `note`): nothing in it has been tried on the live page.
