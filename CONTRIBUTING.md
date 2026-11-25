# Contributing

Issues and pull requests are welcome. There is no agreement to sign.

## Licence of contributions

voyageur is released under the AGPL-3.0, and a commercial licence is also offered. So that both stay possible, contributions
come in under a more permissive licence than the one they go out under:

**By opening a pull request, you license your contribution under the MIT licence**, to the project owner and to everyone else.
You keep the copyright in your work. You also confirm that you wrote it, or otherwise have the right to submit it under those
terms.

MIT text: https://opensource.org/license/mit

## Before you open a pull request

- `uv run pytest -q` passes.
- A behaviour change comes with a test, and a heal-rate change with a testbed run (`testbed/run_acceptance.py`).
- No hostnames, keys or personal data in code, docs, tests or fixtures; examples use `localhost` and `app.example`.
