# Architecture

The service is a pure function of what it is sent, plus a record of what it answered. There is no browser, no page fetch and no
source checkout: a client sends the failure, the source files from the stack and a text inventory of the page, and gets back a
patch to try. The client owns everything that touches the real page, the files and git.

```
client                                   voyageur
------                                   ------------
run suite, collect failure
probe.js on the failing page  ---------> POST /v1/heal
                                           1 triage            (no model)
                                           2 same-element match (no model)
                                           3 text pick          (one model call)
                                           4 vision pick        (one model call, optional: marked screenshot)
                                           5 write locators     (no model)
                                           6 language branch    (no model)
check count + hit, apply patch  <------- try {candidate, patch, expect}
re-run the exact test
POST /v1/heals/{id}/result  ------------> 7 verify (judge the result)
                            <------------ healed | next candidate | needs_human
```

## The ladder

The ladder is climbed from the cheapest step; the first step that settles the question stops it. The numbers are the ones in
`settled_at` (the step that identified the element) and `ended_at` (the step the heal ended at), and in the metrics.

1. **Triage** (`triage.py`, `failing.py`). Reads the error the way a person would: an error that names no locator, or a locator that
   resolved while expected and received values differed, is `not_drift`. Otherwise the broken call is looked up in the sent files
   (`failing.canon`: each quoted string is compared by its value, whatever quotes and escapes either side used, and a `locator()`
   argument is read the way Playwright prints it: `a >> b` as `.locator("a").locator("b")`, no `xpath=` prefix, trimmed; a stack frame
   picks among several matches). No match, or several that nothing tells apart, is `needs_human`
   (`source_not_found`, `ambiguous_source`): the service never edits a call it is not sure is the broken one. When one call is the broken
   one, triage also reports its span, receiver, language, the action on it (`fill`, `click`, `check`..., read past Python's `.first` /
   `.last`) and every other use of the same
   locator in the sent files, so one fix to a shared page object covers each of them.
2. **Same-element match** (`match.py`). If the last passing run left a fingerprint of the element this locator hit, the inventory is
   scored against it. A match is strong only for a stable attribute (id, name, test id, data hook) with the same value, or the same role and
   accessible name in the same place. Exactly one strong match settles the target with no model. Two or none settle nothing.
   A fingerprint recorded with `HEAL_FINGERPRINT_COPY=off` holds `sha256:<hex>` of each copy field (case and spacing folded).
   `hashed_copy.py` hashes the page's copy the same way and puts the page's copy back where a hash matches, before any match or proof
   reads the fingerprint (neighbours too), hashing each distinct page string once per request. A hash that matches nothing, or that
   two spellings fold to (Save / SAVE), stays a hash, and a hash has no words, so it never shares one with page copy. Copy that only
   shares words with the recorded copy is not seen, so proof with the switch off is stricter, never looser.
3. **Text pick** (`pick.py`, `llm.py`, `transport.py`). One model call. The prompt holds the broken line with its neighbours (string literals
   typed by `fill` and friends are redacted), the page language and up to `HEAL_CANDIDATE_CAP` candidate lines. Every page value is a quoted,
   capped, control-character-free string on its own line. When more elements fit the action than the cap, the ones sharing most words
   with the broken call's strings and with the recorded fingerprint come first, then the role/tag hint, then the controls before the elements
   the probe listed only for their copy (`copy_only`; they are candidates only when the broken call's action is known), then page order:
   this decides only what the model sees, never what is accepted. The reply must be JSON `{ref, why}` with a listed `ref`; anything else is no pick.
   The model never writes locator text and never sees a source file or a DOM.
4. **Vision pick** (`vision.py`, `overlay.py`, `screenshot.py`; `core._vision_pass`). Optional, and only with an image: the request's
   `options.vision` is `auto` or `on`, `page.screenshot` is present, and the vision role is configured with
   `HEAL_VISION_IMAGES_ALLOWED=true`. Otherwise the image (already checked at the door, header and chunks only) is never decoded, drawn, held or sent, and the heal goes on with text, the record saying why.
   The service draws the evidence: a numbered box on each candidate of the pick list (mark = list position) over the client's viewport
   screenshot, from the inventory `box` (CSS px, document coordinates) placed with the viewport size, dpr and scroll, cropped to the
   candidates plus a margin and downscaled to at most 1024 px. Candidates with no box, or mostly outside the screenshot, get no mark
   and are listed in the prompt as "not visible in the image". No page text is drawn: the marks are digits. The prompt is the broken
   line, the page language and the same candidate lines as the text pick with `[n]` in place of the ref; the reply is JSON
   `{mark, why}` or `{mark: null}`. A mark that is not on the list, or is not drawn, is refused; the mark maps back to an inventory ref
   by position, and the pick then goes through the same gates and identity proof as a text pick. Vision never writes a locator.
   *When it runs.* `on`: after the text pick of every heal that reaches it, whatever the text said (the text model's `none` included);
   a vision pick that passes the gates replaces a text pick, one that does not leaves the text pick standing. `auto`: only when
   - the text pick gave no usable answer (`no_pick`), or a gate refused the pick (`refused`): the vision call takes the place of the
     second text call a refusal would otherwise get, with the refused element left out of the list;
   - the text pick passed the gates but is a **close call**: another element of the list the model saw is a twin of it, the same tag,
     role, name/label/text/placeholder (case and spacing folded), row and frame (`vision.twins_of`). The text prompt cannot separate
     twins (two "Save" buttons in one toolbar), where each sits on the screen can. This is defined from what `pick.py` leaves us: it
     returns one ref and no confidence, so the test is on the list, not on the answer; or
   - the client's live check found the picked element was the wrong one (`wrong_element`): the next pick is a vision pick.
   A text `none` is the model saying the element was removed, and `auto` does not second-guess it. Settled-at for a vision pick that
   decided the target is 4; a vision pick that only confirmed the text pick leaves it 3. *Limits.* One vision call and one overlay per
   heal, counted in `HEAL_MAX_CALLS` and the run token budget (so with the default of 2, one text call and one vision call); a call
   that is not allowed is skipped, never queued. Image count and tokens are in `usage`; the marked image is kept with the heal record.
   The decision cache key includes whether an image was in play, so a refusal reached on text alone is not served to a run that can look.
5. **Write** (`locator_policy.py`, `uniqueness.py`, `inventory.py`). The service writes locators for the chosen element, most stable first:
   test id, id, name, css on a stable attribute (a plain `data-*` attribute such as `mat-menu[data-menu="tag"]`, a label's `for`
   as `label[for="birthdate"]`, else up to two plain classes), role and name, label, text. A `data-*` attribute is not written when it holds a state or position the page
   changes as it is used (`data-state`, `data-index`), belongs to a framework (`data-reactid`, `data-radix-*`, Vue's
   `data-v-<hash>`), or its value is a flag, a number, a generated id or may have been cut short by the probe (80 characters).
   A class a component library generates (`MuiSelect-select`) counts as generated unless `HEAL_LIBRARY_CLASSES` lists its library
   (`mui`, `ant`, `chakra`, `mantine`, `mat`); then it is a css class candidate, below test ids, ids, names and `data-*` hooks and above
   role, label and text, except a hashed or counted variant (`css-1x2y3z`, `jss123`, `MuiBox-root-123`) or a state (`Mui-focused`,
   `ant-select-open`), which are never written. CSS-in-JS classes (`css-...`, emotion's `css-<hash>-<label>`, `jss...`, `sc-...`) are never written. The oracle is
   unchanged, so a library class is written only where it can be proved unique: on a listed tag, or (a class the broken call names,
   e.g. `data-test=x >> .MuiSelect-select`) as `tag[role=r].class` beside the element's explicit role, also inside a hooked container.
   Each is kept only if it is unique on the page (the inventory is
   the oracle), is that element, parses in both languages and passes the replacement validator. Structural and positional locators are
   never written, so an element with nothing stable and unique ends `needs_human` (`no_stable_locator`).
   **Text elements** (headings, paragraphs, list items, plain and wrapping labels, named landmarks) follow the same ranking, so a stable attribute
   on one is still the locator. With none, the copy-based tiers apply, each written only when the request proves it unique. *Role and name*
   comes first: a heading is `get_by_role("heading", name=..., level=N, exact=True)` (the level is the tag's, `h2` is 2, and is left out when the
   listing cannot say it, e.g. a `div role=heading`), and a landmark the author named (`nav`, `aside`, `form`, `section`, `article`, `dialog`, `table`
   with an `aria-label` or `aria-labelledby`) is its role with that name, never its inner copy; a `p`, `li` or plain label has no accessible
   name from its content, so it is never written as a role. The proof is `page.aria_snapshot` (`uniqueness.aria_count` counts the role, the
   exact name and the level), and the listing must agree that the one match is this element. *Exact text* (`get_by_text(..., exact=True)`) is
   next, proven from the probe's copy counts (`Inventory._text_count`; a form control named by a label is not a text match, the control's
   own copy is). A copy-based result is `copy_based` and tier `role` 4 or `text` 7 wherever the API says so, and it is never turned into another
   language: on a page whose language differs from the passing run it goes through step 6 (a branch per language, the old copy from the
   broken call or the recording, else `language_branch_unavailable`). When neither proof is possible the element has no locator and
   `WriteResult.gap` / `why` (the overlay legend's `reason` / `why`, a heal's `why`) say which: `aria_snapshot_missing`,
   `aria_snapshot_unusable`, `copy_counts_missing`, `listing_incomplete`, `copy_not_unique`, `not_matched`, `unprovable`, or `nothing_stable`
   (no copy and no stable attribute). A reason names no page copy.
   **Row scope** (`row_scope.py`): when none of the element's own locators is unique on the page (a `data-qa=status` badge in
   every order row), the probe's `row` and `row_attr` name the nearest ancestor with a test hook. The writer then looks for a
   locator unique among that row's elements (those inside the row's box, those whose nearest hook is the row, and, followed
   to a fixed point, those whose nearest hook is a group already found inside it) and puts the row's own hook in front:
   `page.locator('[data-qa="order-row-1042"]').locator(...)`. A row whose hook cannot be written (a react-select listbox with a
   generated id) is climbed past: the element holding it is listed exactly once and is an ancestor of the target, so its own
   row is one too (at most 8 hops, none when the hook is not listed exactly once). Inside a row two more forms are written,
   after the css ones and never page-wide: an explicit `role` attribute (`div[role="combobox"]`, the select that a library
   class names) and the stable tail of a generated id as an `$=` selector (`react-select-4-option-6` is
   `[id$="-option-6"]`; the tail starts after the last token that holds a digit and something follows it, a colon or a hash,
   and needs a three-letter word). Both are css-tier and must be unique among the row's elements. The probe lists every hooked element, so the row must be
   listed exactly once and have a box. No scope for a hook that may be cut short (80 UTF-16 units), holds whitespace, is a
   generated id or changes run to run (a 6+ digit run, a hash-like token). Inside a row only kinds counted from the list
   are written (no text or label, whose count needs the snapshot). The candidate's `why` names the row. A hook selector
   is not a structural locator, so this does not loosen the rule above.
   **Text filter** (`text_filter.py`): when nothing stable of the element's own is unique (two rows share
   `data-testid=subscription-row`), a stable locator it shares is narrowed by a text the broken call itself names (a
   `get_by_text` argument, a role name, `has_text`, `:has-text()`, `:text-is()`, `text=`):
   `page.get_by_test_id("subscription-row").filter(has_text="My subscription")`. It is kept only when every element the
   base matches is listed, the element's text holds that text and no other match's does, read as `has_text` reads it
   (case-insensitive, whitespace collapsed, a substring); a match whose listed text may be cut short (80 characters)
   cannot be ruled out, so the text is not used. It is copy-based (text tier) and comes after the writer's own
   copy-based candidates, never ahead of a stable one.
   **Tier floor:** the broken locator's tier is the floor. Candidates at or above it are used when any is unique; only when none is does
   the heal move down, and then the candidate says so (`tier_movement: "down"`, `below_floor: true`).
6. **Language** (`branches.py`). A locator that depends on page copy only works in one language. If the page now renders in another
   language than the passing run (html `lang`, then browser locale, then `Content-Language`), the service returns one locator per
   language and code that selects by the page's language. The old language's locator is the broken call itself or is written from the
   copy in the fingerprint; copy is never translated, and if the old copy is not known no branch is built and the heal ends
   `needs_human` (`language_branch_unavailable`). A locator that does not depend on copy (test id, id, name) needs no branch.
7. **Verify** (`gates.py`, `core.py`). The client reports `{count, hit, test_outcome}`. Only count 1, the expected element and a passing
   exact test make a heal `healed`. A bad count or a failed re-run moves to the next ranked locator for the same element, with no model
   call; a hit on another element rules that pick out and goes back to step 3. After `max_rounds`, or when nothing is left, the heal ends
   `needs_human`.

## Gates

Small checks that keep a plausible answer from becoming a wrong heal (`gates.py`):

- A pick (or a fingerprint match) must be on the list the model was shown, visible, something the action can be taken on, and not provably
  a different element than the passing run used (the old id or test id now belonging to another element, or the pick sitting in another
  row, or being that row: a pick whose hook is the recorded element's row is its container, which can share its box exactly).
  A row counts as renamed, not as another row, only when the pick is in the recorded place, the recorded row is on no listed element
  any more, and the pick keeps the recorded element's own test hook or named data-* value (with the same copy) that no other element has, or the row and the element's id
  were renumbered by the same number with the copy unchanged (a whole react-select widget going from `-3-` to `-4-`), or the row id was
  generated before and now (React `useId`, `:r2u:` to `:r5k:`) with the copy and place unchanged and no element of another row holding that
  copy, which is only said of a complete listing with no copy-holding element left out.
- `renamed_neighbour`: a removed button is not healed onto its renamed neighbour (the chosen element has the size of another recorded
  element, not the step's, and neither is still on the page with its old copy).
- `moved_into_frame`: a page-level twin is not the step's element when that element now sits in a frame.
- **Identity proof** (`proof.py`, applied by `verify_pick`): a pick becomes a heal only with positive proof it is the element the step
  meant; a passing re-run is not proof. No proof ends the pick as `no_identity_proof` (the model's next answer is tried while calls
  remain, else `needs_human`).
  - With a fingerprint, at least one of: *identity* (test id, id, name or another hook unchanged; a hook many elements share counts only
    with the same row; a generated id never counts), *copy* (the same name/label/text/placeholder/title/alt and a compatible kind: the
    same role, else the same tag), *place* (overlapping box, or the same offset from the recorded neighbours still on the page, and a
    compatible kind). Place also needs the element to be the top-most at its box centre when the probe says so (`top_at_center`, or
    `hit`, on the inventory entry: false refuses). When the probe does not say, place alone is not proof if the copy differs
    completely (both sides have copy and no meaningful word in common). Exception: when the page language differs from
    `last_pass.language` (both known) the copy is expected to differ because it was translated, so place plus a compatible kind
    proves with no shared word (the top-most check still applies when supplied). Same box swap with an unchanged language: refused.
    The same fingerprint also proves a control **inside a bare wrapper** (*within*): the recorded element was a container tag or a
    custom element with no copy, hook, id, name or role of its own (a `div` around a field, a vendor's internal component), so
    there is nothing to compare and its kind is not a control's. The pick must be a control, lie inside the wrapper's box (1 px
    tolerance) with no other listed element inside that box (a larger element, the wrapper's parent, is not inside it), and be
    top-most at its box centre when the probe says. It must also fill the wrapper: at least 80% of its width or height and 10% of
    its area, so a big form, section or list with one widget left in it is not that widget's wrapper (a `164 x 29.6` input in its
    `1198 x 29.6` row is: full height, 13.7% of the area). The listing must be complete with no copy-holding element left out
    (`copy_more`), as for the *selector* proof of a healed frame hop and for the regenerated-row rule below; a listing that is not
    cannot say "no other element".
  - Without a fingerprint, from the broken locator's own facts (its last selector, role+name, text arguments, filter text): *hook_stem*
    (a stable value or the copy of the pick equals the old id/name/hook value, or one has every meaningful word of the other, a word
    counting when it is the other's stem: `display-name` to `display-name-input`, `username` to `user`; digit-only tokens are
    never words, and a number counts only beside a matching word, `order-1` is not `order-2`), *copy* (the same role+name
    normalized, or at least two shared meaningful words, stopwords and words under three letters excluded, stems only between whole
    tokens of five or more letters with a five-letter common prefix; or, narrowly, the step's whole name inside the copy of the only
    listed element that says it: `Expiry` to `Expiry date`), *row* (the pick sits in a row holding the filter's text), *renamed* (a
    CSS-only locator whose old id/class/attribute values are all gone, the pick is the only visible element of its tag and its
    copy equals one of the selector's names: a lone element of the tag is never proof by itself). With no fingerprint a renumbered id
    (`menu-1042` to `menu-1043`) proves nothing: a renumbered widget and a removed row's lone survivor look the same; and an old
    value with a number is never proven by copy without that number (`#invoice-3` by "Invoice").
  - Contradiction (`different_element`), checked first and never outvoted: a hook attribute present on both sides with different values
    while the copy differs too (`data-cy=export-csv` against `data-cy=export-pdf`), or, with no fingerprint, a hook of the pick that is
    not the old hook value's stem. A removed button's freed place does not make its neighbour the button.
- **Frames** (`frames.py`): when the broken call has a frame hop (`frame_locator` / `frameLocator`, or a locator followed by
  `content_frame`), only that frame's elements are candidates (the hop's selector is resolved to a listed iframe by id, name, title or
  hook, honouring the attribute operator: `[name^="__privateStripeFrame"]` is a prefix), the fingerprint is matched among them only,
  and the written locator is the same hop (with its `.first` / `.last` / `.nth(n)`) plus the new in-frame locator, in both
  languages. A page-level element, or one of another frame, is never offered; an unresolvable hop offers nothing (`target_missing`)
  unless the hop itself can be healed (below). A `content_frame` hop restricts the pool but cannot be written (the chain grammar has no
  such step): `no_stable_locator`.
- **Frame hop healing** (`frame_heal.py`, run by `analyze` before the fingerprint match): the hop is the broken part only when its
  selector is short and can be read (ids, names, titles and test hooks, with the attribute operators; the pattern cannot backtrack),
  names no listed iframe, and the inventory is complete; any other hop is left as above (it may still resolve, and the inner locator be what broke). Then each place an element
  could now be (the page, and every listed frame) is asked whether it holds an element that identity proof accepts as the step's, and
  exactly one must: with a fingerprint an unchanged stable attribute, the same copy and kind, or the step's own unchanged selector
  selecting it alone with the recorded tag and classes (`selector`, `proof.prove_from_kept`: only on a complete listing with no
  copy-holding element left out, and only for a selector the listing covers, never a bare class or tag); without one the step's own
  hook values or copy. Only an unchanged stable attribute or the selector proof, with a recording, is strong. Everything else is
  weak: it never outvotes a strong place, never drops the hop to the page (a header "Pay" button is not the checkout frame's), and
  for a frame needs continuity, a meaningful word (not `iframe`) of the old hop's selector in the new iframe's id, name or title.
  The same copy also places nothing when the step or its recording names the element by an id, name or hook (the element would lack
  what named it). A card number field in two frames, or none, stays `target_missing` and says so. A frame place is written as a hop for its iframe element, among the iframes beside it
  only: test id or other hook, hand-written id, name unless it ends in a counter or has a random suffix, plain `data-*`, title, and
  last a counter-like name, each unique; hops before it (a parent frame) are kept, and a frame at a depth those hops do not reach
  is refused. When the place is the page (a step with one hop) the hop is dropped. The candidates are the step's own locator behind
  the new hop first (it was not what broke; the client's live check says whether it holds), then the locator written for the
  element in its new place, then the hop's lower-ranked spellings. A pick in the new place is gated as any pick, with the step's own
  selector as one more proof for it.
- **None** (`pick.py`): the prompt offers `{"ref": null, "why": ...}` when no listed element is the one the step meant; it ends the heal
  `needs_human` / `target_missing`.
- Hit checks: a locator that hits another element than expected is `wrong_element`, one that cannot be tied to an inventory entry is
  `unverified`, and neither can be recorded as healed.

Cost controls are a per-run token budget (`HEAL_RUN_TOKEN_BUDGET`, checked before every model call), a cap on calls per heal, and a decision
cache keyed on the broken call, the page inventory and the language: a repeat of the same drift costs no tokens and is still verified by the
client.

## Advise, audit and MCP

Advise mode runs the same ladder up to step 6 and stops: `HealSession._advise` takes the first locator `write()` produced for the settled
element (with the language plan from `stages.plan_for`) and answers `advised` with it, never building a patch or opening a conversation. The
gates, the run budget and the decision cache apply unchanged, and a pick a gate refuses is `needs_human`. `audit.run_audit` is the same
`write()` run over the locators already in the files: it reports a locator's tier and uniqueness from the inventory and the locator a heal
would have written for the same element, and runs under the request deadline in a worker like any client-text stage. `mcp_server.py` is a
thin shell: its tool functions call `parse_request` / `parse_audit_request` (the HTTP routes' own validation) and then `core.start` (advise
forced) or `core.audit`.

## Overlay (`marks.py`, `overlay.render_marks`)

`POST /v1/overlay` is a side door like audit, deliberately its own route: a heal's overlay is a step of the ladder that feeds a model and is kept with the
record, while this one is made on request, shown to a person and kept nowhere, so it shares the drawing code and nothing of the ladder.
`marks.run_overlay` resolves each locator the request names with the matcher an audit uses (`audit._hits`), or, with none named, takes every listed element that is not
hidden and is on the picture (`overlay.visible_boxes`, from the PNG header alone) and asks `stages.write_for` for the locator a heal would write, the same
candidates, uniqueness proof and stability ranking. It then hands marks (a number, a box, the label text) to `overlay.render_marks`, which decodes the screenshot once, scales it
to at most 1600 px, draws every outline (biggest first, colors chosen so overlapping boxes differ) and then every tab (placed so tabs do not cover each other), from
the same built-in bitmap as the digits, extended to printable ASCII, with no font file. `HealSession.overlay` runs it in the worker pool under the request deadline in one of the audit's
slots (so a heal always gets a worker) and counts `voyageur_overlays_total`; it never touches the store or a model. The route answers JSON (the PNG as base64 shaped like a
request's screenshot, the legend, a summary) rather than a bare PNG because a caller needs both; the MCP tool `overlay_locators` returns the picture as an image block and the
rest as text. See [SECURITY.md](SECURITY.md#overlays) for why it is not gated on the vision settings.

## Diagnose

`HealSession.diagnose` (off unless `HEAL_DIAGNOSE=on`) is a side door next to the ladder, not a step of it. It runs `stages.analyze` for the triage
only: a located failure is `not_applicable` (it is drift: `/v1/heal`), a `not_drift` or `needs_human` one goes on. `diagnose.diagnose_input` builds
the prompt in a worker (the test code round the failing frame, the head of the error, the prior heals, a ranked element list, the aria snapshot, all
through `withhold`) and says which prior heals are still in the code near the failure; one text call goes through the same endpoint a pick uses
(only `max_tokens` is raised); `diagnose.interpret` parses the reply strictly and applies the heal gate and the edit check. Nothing is cached, no
conversation stays open, no screenshot is read. The record is mode `diagnose` with the cause as its reason (`confidence`, `patch_suggested` and
`downgraded` are its own columns), and metrics, filters and Prometheus count it apart from heals.

## Silent wrong elements

A locator that picks by position (`.first`, `.nth(1)`, a `:nth-child`) among several matches can drift without failing: a new element
above the target is picked instead, the action lands on it, and the test fails later at an assertion, or not at all. There is no locator
error to point at, so triage classes that failure `not_drift` and the service never heals it: guessing which earlier line moved would mean
writing a locator nobody asked about, which is how wrong heals happen. The service catches it before it happens instead: the audit reports
such a call as `positional` with the number of elements it picks from and, when one is unique, the stable locator to replace it with. Run the
audit (`POST /v1/audit` or the `audit_locators` MCP tool) on the pages a suite uses and fix the positional calls it lists.

## The screenshot (`screenshot.py`, `overlay.py`, `stages.vision_input`)

An image is untrusted input headed for a decoder, so it is checked in layers before any pixel is decoded (see
[SECURITY.md](SECURITY.md#screenshots)), and decoding and drawing run in the killable worker under the request deadline, like every other
stage that reads client data. The overlay is deterministic (integer crop arithmetic, a fixed palette, digits from a built-in bitmap,
Lanczos resampling, no metadata): the same screenshot, boxes and viewport give the same bytes on one Pillow version. A screenshot is held
in memory for an open conversation only while a vision call could still be needed (the wrong-element case), at most 64 MB in all
(`MAX_HELD_IMAGE_BYTES`); past that, or once the vision call has run, it is dropped and a later round uses text.

## The heal record (`store.py`, `metrics.py`)

One SQLite row per heal, one row per model call, one row per candidate tried. A row holds decisions, usage and short facts, never file
contents, page text, the client's screenshot or model replies. The one image kept is the **overlay**, the marked and cropped picture a
vision model was shown, in a table of its own (`overlays`, one per heal, served at `/v1/heals/{id}/overlay.png`): it lives and goes with
its heal, and with `HEAL_RETENTION_DAYS=0` it is never stored:

- where: repo, framework, language, test, file, line, page path and language
- what broke: the broken locator, its kind and tier, a hash of the inventory
- how it ended: `outcome`, `reason`, `settled_at`, `ended_at`, rounds, candidates tried, wrong-element catches, budget and cache flags
- what was written: final locator, kind and tier, `tier_movement`, `below_floor`, `branch`, `covers` (tests a shared fix covered)
- the verify: `verify_count`, `verify_ref`, `verify_passed`
- cost: per call, role (`text` or `vision`), model, local or cloud, tokens, images, latency
- vision: `evidence.vision` (mode, whether it ran and why, marks drawn, the result, whether it changed the text pick, or why it was skipped)
- after the PR (`POST /v1/heals/{id}/followup`): `pr_state`, `failed_again`, `runs_since`, `days_since`, `followup_at`
- `mode`: `heal`, `advise` for a request that asked for advice only (outcome `advised`), or `diagnose` (outcome `diagnosed`, the cause as the reason)

`/v1/metrics` computes every figure from these rows with one definition each, and `/v1/heals` returns the rows behind any figure.
Every figure has a `drill` entry (the `/v1/heals` query string that lists its heals), and `/v1/heals?format=csv` exports the list. A store
written by an earlier version gains the columns it lacks when it is opened. Advise-mode heals are counted under `advise` and diagnoses under `diagnose`, and both are left out of every
rate. Records older than `HEAL_RETENTION_DAYS` are purged, but not before `rollup.heal_counts` has turned each
into additive counters (a heal, its calls and its tries) and added them to the `rollup` table, one row per day, scope and dimension, in the same
transaction that deletes it, so a failed purge loses nothing and a second one counts nothing twice. `metrics.aggregate` adds the rollup's
counters to the ones it computes from the records kept (`archive` in the answer says how many heals that is); the figures that are not sums
(distinct locators, hot spots, runs, latency percentiles) stay on the records kept. A scope per model keeps the `model` filter exact.
`HEAL_MAX_DB_MB` frees the oldest images first, then the oldest records past the durability window, then younger ones with a warning.
Open conversations are held in memory, bounded, and closed after `HEAL_OPEN_TTL_S`.

## Modules

| Area | Modules |
| --- | --- |
| API and CLI | `app.py`, `cli.py`, `schemas.py`, `config.py`, `export.py` (CSV), `telemetry.py` (`/metrics`), `dashboard.py` and `dashboard/dashboard.html` |
| Other ways in | `mcp_server.py` (MCP tools), `audit.py` (`/v1/audit`), `marks.py` (`/v1/overlay`), advise mode in `core.py` |
| Core | `core.py` (the session), `triage.py`, `match.py`, `pick.py`, `vision.py`, `gates.py`, `proof.py`, `frames.py`, `frame_heal.py`, `branches.py` |
| Image | `screenshot.py` (checks), `overlay.py` (marks, crop, scale, locator text, colors) |
| Locators | `locators.py`, `locator_steps.py`, `locator_policy.py`, `uniqueness.py`, `inventory.py`, `elements.py`, `element_facts.py`, `failing.py` |
| Model | `llm.py`, `transport.py`, `netpolicy.py`, `redact.py` |
| Record | `store.py`, `rollup.py`, `metrics.py` |
| Client script | `probe/probe.js` |
