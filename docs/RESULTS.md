# Results

What the acceptance runs showed, measured on 2026-11-23 on this repository at version 0.1.0, and how to repeat each one. The
testbed they run on is described in [testbed/README.md](../testbed/README.md).

## What is being measured

The testbed has three specs of broken Playwright suites: `drift` (one small page, five variants, one situation each), `orders` (a
hard-locator spec: deep chains, hooks, aria, generated classes, xpath, frames, shadow DOM) and `real` (82 locators copied from
public suites). For every broken locator the repository holds the correct heal (the golden file).

`testbed/run_acceptance.py` runs each suite against each drifted variant. At every failure it sends the service a real
`POST /v1/heal` request, applies the patch it gets back to a temporary copy of the test, re-runs the exact test and reports what it saw.
The repository's test files are never edited.

- **Healed.** The re-run got past the patched statement and that statement's own check.
- **needs_human.** The service gave up and said why (`target_missing`, `no_pick`, `no_stable_locator`, and so on).
- **Wrong heals kept.** Healed steps whose element is not the one the golden file names. This is the number that must be 0.
- **Wrong element caught.** Candidates the client rejected after trying them in the page, so they never became a heal.
- **Steps run / golden.** Failures the run reached, against the golden steps of that variant. The two differ because a test only
  reaches a later line when the earlier one passes or is healed, and some failures are not in the golden file.

## Summary

| Run | Healed | needs_human | Wrong heals kept |
| --- | --- | --- | --- |
| Unit, contract and browser tests | 4,801 passed, 0 skipped | | |
| Python client, scripted model, `drift` + `orders` | 29 | 5 | 0 |
| Python client, scripted model, `real` | 50 | 7 | 0 |
| Python client, a real model, `drift` + `orders` | 27 | 7 | 0 |
| Python client, a real model, `real` | 44 | 13 | 0 (1 wrong element caught) |
| TypeScript client, a real model, `drift` + `orders` | 24 | 10 | 0 |

The `real` runs also report 7 steps as `not_drift`: the old locator still matches an element, but the wrong one, and the test fails on its
next assertion. That is a bug in the test or a change in the page's behaviour, not a locator to repair, and the service says so.

The diagnose bench (`POST /v1/diagnose`, for failures where every locator still matches) has nine cases. Under the scripted oracle
all nine answers equal the key. Under four adversarial scripted models the number of wrong causes kept is 0 in every one:

| Scripted model | Answers equal to the key (of 9) | Wrong cause kept | Edit on a non-edit cause kept |
| --- | --- | --- | --- |
| oracle | 9 | 0 | 0 |
| `real-id` (always names a sent heal as the wrong one) | 2 | 0 | 0 |
| `made-up-id` (names a heal that was never sent) | 1 | 0 | 0 |
| `empty-priors` (no earlier heal is sent) | 1 | 0 | 0 |
| `patch-always` (proposes an edit whatever the cause) | 6 | 0 | 0 |

## What the scripted model means

`--model fake` replaces the model with a script. At the pick step it names the element the golden file says is correct, and
it reports a fixed token estimate. It never guesses wrong and never fails to answer.

So a scripted run is an upper bound on the pipeline: it shows what everything around the model can do (triage, the fingerprint match,
the candidate list, the locator writer, the uniqueness proof, the gates, the client's verify) when the model's pick is right. A step
that still ends `needs_human` under the script is a limit of the pipeline, not of a model. It is also the run to use for a regression
check, since it is offline and repeatable.

It says nothing about how well a model picks. The wrong-element gates are still in the path, so `wrong kept` stays a real test of them.
For a vision-assisted run the script is deliberately not an oracle (see the testbed README).

## The real-model runs

A real run sends the pick to an OpenAI-compatible endpoint (`HEAL_TEXT_*`), unchanged from how the service would be used. The model
is not named here because the numbers belong to that one model on that day. They are lower than the scripted ones, as they should be:

- Python, `drift` + `orders`: 27 healed against 29, and 7 `needs_human` against 5. The `needs_human` answers carry the reasons
  `target_missing` (4, one of them the correct answer for `drift-gone`), `no_pick` (2) and `no_stable_locator` (1): the model said none of
  the listed elements was the one, or named nothing usable, on steps the script places.
- Python, `real`: 44 healed against 50, 13 `needs_human` against 7 (`target_missing` 4, `no_pick` 4, `no_identity_proof` 3,
  `no_candidate_left` 1, `no_stable_locator` 1). One candidate was on the wrong element; the client's check rejected it and no wrong heal
  was kept.
- TypeScript, `drift` + `orders`: 24 healed, 10 `needs_human`, on the same specs through the TypeScript client's own probe, fingerprints and
  verify step.

A real model is also less repeatable than a script: the same run on another day, or with another model, will give other numbers. The two
figures that do not depend on the model are the ones the gates enforce: no heal on the wrong element was kept in any run, and a
step the model cannot place ends `needs_human`, never a guess.

The `drift` spec shows the split best. All five variants end as designed under both models, because four of the five settle at step 2
(a same-element match against the last passing run) and never call a model; the fifth, `drift-gone`, removes the Delete button and must
end `needs_human`.

## Per-variant numbers

Python client, `drift` + `orders`. Columns: steps run / golden, healed, needs_human, model calls, tokens in / out, wrong heals kept.

| Variant | Scripted model | A real model |
| --- | --- | --- |
| `drift-renid` | 1/1, 1, 0, 0 calls, 0 / 0, 0 | 1/1, 1, 0, 0 calls, 0 / 0, 0 |
| `drift-fr` | 3/3, 3, 0, 0 calls, 0 / 0, 0 | 3/3, 3, 0, 0 calls, 0 / 0, 0 |
| `drift-dup` | 1/1, 1, 0, 0 calls, 0 / 0, 0 | 1/1, 1, 0, 0 calls, 0 / 0, 0 |
| `drift-noid` | 1/1, 1, 0, 0 calls, 0 / 0, 0 | 1/1, 1, 0, 0 calls, 0 / 0, 0 |
| `drift-gone` | 1/1, 0, 1, 1 call, 558 / 1, 0 | 1/1, 0, 1, 1 call, 660 / 38, 0 |
| `orders` v7 | 7/7, 6, 1, 3 calls, 3667 / 42, 0 | 7/7, 6, 1, 2 calls, 3579 / 128, 0 |
| `orders` v8 | 10/8, 9, 1, 5 calls, 3837 / 70, 0 | 10/8, 8, 2, 5 calls, 5208 / 285, 0 |
| `orders` v9 | 10/10, 8, 2, 3 calls, 2819 / 29, 0 | 10/10, 7, 3, 3 calls, 4027 / 163, 0 |

Python client, `real`:

| Variant | Scripted model | A real model |
| --- | --- | --- |
| v10 | 12 run, 7 healed, 0 needs_human, 5 not_drift, 4 calls | 12 run, 4 healed, 3 needs_human, 5 not_drift, 5 calls |
| v11 | 36 run, 34 healed, 1 needs_human, 1 not_drift, 20 calls | 36 run, 31 healed, 4 needs_human, 1 not_drift, 23 calls |
| v12 | 16 run, 9 healed, 6 needs_human, 1 not_drift, 11 calls | 16 run, 9 healed, 6 needs_human, 1 not_drift, 11 calls |

The runner also compares each step with the golden file and the known gaps (`testbed/acceptance-gaps.json`, the steps the pipeline is
known not to heal yet, each with its reason) and prints `as expected`. Under the scripted model every variant of all three specs is as
expected. Under a real model more rows are marked as not expected, for the reasons above: those are steps the model did not place, not
wrong heals. In every run `wrong kept` was 0.

## Repeat it

One-time setup:

```bash
uv sync --all-extras
uv run playwright install chromium
```

The test suite:

```bash
uv run pytest -q
```

The scripted runs (offline, no model, no network):

```bash
uv run python testbed/run_acceptance.py --model fake --spec drift orders     # about 3 minutes
uv run python testbed/run_acceptance.py --model fake --spec real             # about 5 minutes
uv run python testbed/run_diagnose.py --model fake                           # the diagnose bench, seconds
```

A real model. The service reads its own `HEAL_TEXT_*` settings; the runner sets nothing:

```bash
export HEAL_TEXT_PROVIDER=openai HEAL_TEXT_BASE_URL=http://localhost:11434/v1 HEAL_TEXT_MODEL=your-model-id
uv run python testbed/run_acceptance.py --model env --spec drift orders
uv run python testbed/run_acceptance.py --model env --spec real
```

The TypeScript client, from a checkout of its repository with `npm ci` done:

```bash
uv run python testbed/run_acceptance.py --model env --spec drift orders --client ts --ts-client-dir /path/to/ts-client
```

Each run writes `acceptance.json` (every step) and `acceptance.md` (the tables above, plus one row per step) to `testbed/.runs/acceptance/`;
`--out` changes the directory. The exit status is 1 when a step misses its expectation, a healed step is not on the golden element, or a
base run is red.

To fill the dashboard with a run, start a service with its own record and point the runner at it:

```bash
export HEAL_DB=/tmp/demo.db HEAL_RETENTION_DAYS=31
uv run voyageur serve --port 8790 &
uv run python testbed/run_acceptance.py --model env --spec drift orders --service-url http://127.0.0.1:8790
# then open http://127.0.0.1:8790/dashboard
```

The dashboard and heal-record screenshots in the README were taken this way, from a real-model run of `drift` + `orders` (27 healed, 7
`needs_human`).
