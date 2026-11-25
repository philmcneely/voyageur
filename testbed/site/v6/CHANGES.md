# v6 - French

The base site translated to French and nothing else. It separates locators that depend on the copy from locators that depend on the markup: only the first kind breaks, and the right heal moves them to the second kind.

Test lines are looked up by locator text; `py` = tests/python/test_signup.py, `ts` = tests/ts/signup.spec.ts. A line that is not listed still passes.

- Every visible string is French (labels, buttons, headings, placeholders, aria-labels, titles, validation messages, month names). Every id, name, data-testid, option value and data-key is unchanged, so the summaries the assertions read are unchanged.
  - affects `get_by_label("Full name")` (py L13; ts L10): COPY-BASED. The element still has id="fullname" / name="fullname": the correct heal is `page.locator("#fullname")`, which passes in English AND French. Rewriting it to `get_by_label("Nom et prénom")` would pass here and break the English site.
  - affects `get_by_role("button", name="Next")` (py L18; ts L15): COPY-BASED. The button has id="next": `page.locator("#next")`.
- Lines that already use a test id, an id, a name or an option value pass unchanged.
  - no test line is affected (nothing to heal).
