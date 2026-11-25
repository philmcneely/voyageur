"""Triage: not drift, needs a human, or the one located call with its receiver, language, action and other uses."""
import pytest

from voyageur.schemas import SourceFile
from voyageur.triage import triage

pytestmark = pytest.mark.unit

PAGE_PY = """class LoginPage:
    def __init__(self, page):
        self.page = page

    def login(self, user, pw):
        self.page.locator('#username').fill(user)
        self.page.get_by_label('Password').fill(pw)

    def again(self, user):
        self.page.locator('#username').fill(user)
"""
TEST_PY = """def test_valid_login(page):
    page.locator('#username').fill('a')
    page.get_by_role('button', name='Go').click()
"""
ERR = "Timeout 30000ms exceeded.\nCall log:\n  - waiting for locator('#username')\n"


def f(path, content):
    return SourceFile(path=path, content=content)


def test_no_locator_in_the_error_is_not_drift():
    t = triage("AssertionError: assert 3 == 4", [f("tests/t.py", TEST_PY)], [], "python")
    assert t.status == "not_drift" and t.reason == "no_locator_in_error"


def test_an_assertion_mismatch_on_a_resolved_locator_is_not_drift():
    err = "Error: expect.toHaveText\nlocator resolved to <h1>Hi</h1>\nReceived string: Hi\nExpected string: Bye"
    assert triage(err, [f("tests/t.py", TEST_PY)], [], "python").status == "not_drift"


# Playwright 1.65+ (TS, ANSI dim codes as printed): the action waits while the element it found is inert
INERT_ERR = ("locator.click: Timeout 1500ms exceeded.\nCall log:\n"
             "\x1b[2m  - waiting for locator('#b')\x1b[22m\n"
             "\x1b[2m    - locator resolved to <button id=\"b\">Go</button>\x1b[22m\n"
             "\x1b[2m  - attempting click action\x1b[22m\n"
             "\x1b[2m    2 × waiting for element to be visible, enabled and stable\x1b[22m\n"
             "\x1b[2m      - element is inert\x1b[22m\n")


def test_an_action_on_an_inert_element_the_locator_found_is_not_drift():
    t = triage(INERT_ERR, [f("tests/t.py", TEST_PY)], [], "python")
    assert t.status == "not_drift" and t.reason == "element_inert"


def test_the_python_client_error_for_an_inert_element_is_not_drift():
    err = ("Locator.click: Timeout 800ms exceeded.\nCall log:\n  - waiting for locator(\"#b\")\n"
           "    - locator resolved to <button id=\"b\">Go</button>\n  - attempting click action\n"
           "    2 × waiting for element to be visible, enabled and stable\n      - element is inert\n")
    t = triage(err, [f("tests/t.py", TEST_PY)], [], "python")
    assert t.status == "not_drift" and t.reason == "element_inert"


def test_page_text_saying_inert_inside_the_resolved_element_is_not_read_as_inert():
    err = ("locator.click: Timeout 1500ms exceeded.\n  - waiting for locator('#p')\n"
           "    - locator resolved to <p>a - element is inert</p>\n"
           "  - element was detached from the DOM, retrying\n")
    assert triage(err, [f("tests/t.py", TEST_PY)], [], "python").reason != "element_inert"


def test_an_element_that_was_detached_before_turning_inert_is_not_read_as_inert():
    err = INERT_ERR + "  - element was detached from the DOM, retrying\n"
    assert triage(err, [f("tests/t.py", TEST_PY)], [], "python").reason != "element_inert"


def test_element_not_found_is_not_read_as_inert():
    err = INERT_ERR + "  - element(s) not found\n"
    assert triage(err, [f("tests/t.py", TEST_PY)], [], "python").reason != "element_inert"


def test_one_inert_block_does_not_hide_drift_in_another_block():
    err = ("  1) tests/t.spec.ts:3:1 › a ───\n" + INERT_ERR +
           "  2) tests/t.spec.ts:9:1 › b ───\n"
           "locator.click: Timeout 1500ms exceeded.\n  - waiting for locator('#gone')\n")
    assert triage(err, [f("tests/t.py", TEST_PY)], [], "python").reason != "element_inert"


def test_inert_in_every_block_is_not_drift():
    err = ("  1) tests/t.spec.ts:3:1 › a ───\n" + INERT_ERR +
           "  2) tests/t.spec.ts:9:1 › b ───\n" + INERT_ERR)
    assert triage(err, [f("tests/t.py", TEST_PY)], [], "python").reason == "element_inert"


def test_inert_without_a_resolved_element_is_not_read_as_inert():
    err = "locator.click: Timeout 1500ms exceeded.\n  - waiting for locator('#b')\n  - element is inert\n"
    assert triage(err, [f("tests/t.py", TEST_PY)], [], "python").reason != "element_inert"


def test_a_quoted_source_line_saying_inert_is_not_read_as_inert():
    err = ("locator.click: Timeout 1500ms exceeded.\n  - waiting for locator('#b')\n"
           "  - locator resolved to <button id=\"b\">Go</button>\n"
           "  - waiting for element to be visible, enabled and stable\n"
           "> 12 |   // - element is inert\n")
    assert triage(err, [f("tests/t.py", TEST_PY)], [], "python").reason != "element_inert"


def test_zero_matches_is_needs_human():
    t = triage(ERR, [f("tests/t.py", "def test_x(page):\n    pass\n")], [], "python")
    assert t.status == "needs_human" and t.reason == "source_not_found"


def test_several_matches_without_a_stack_line_is_needs_human():
    t = triage(ERR, [f("pages/login.py", PAGE_PY)], [], "python")
    assert t.status == "needs_human" and t.reason == "ambiguous_source"


def test_a_stack_line_splits_the_matches():
    t = triage(ERR, [f("pages/login.py", PAGE_PY)], [("pages/login.py", 10)], "python")
    assert t.status == "located" and t.line == 10
    assert t.call == "self.page.locator('#username')" and t.receiver == "self.page" and t.language == "python"
    assert PAGE_PY[t.start:t.end] == t.call and t.action == "fill"


def test_a_single_match_needs_no_stack_and_reports_every_other_use_across_the_files():
    files = [f("pages/login.py", PAGE_PY.replace("    def again", "    def _x").replace("'#username'", "'#u2'", 1)
               .replace("'#u2'", "'#username'", 1)), f("tests/t.py", TEST_PY)]
    t = triage(ERR, [files[1]], [], "python")
    assert t.status == "located" and t.path == "tests/t.py" and t.receiver == "page"
    both = triage(ERR, files, [("tests/t.py", 2)], "python")
    assert both.path == "tests/t.py" and {u.path for u in both.uses} == {"pages/login.py"}
    assert all(u.text.endswith("locator('#username')") for u in both.uses) and len(both.uses) == 2


def test_double_quotes_in_the_error_match_single_quotes_in_the_source():
    err = 'waiting for locator("#username")'
    assert triage(err, [f("tests/t.py", TEST_PY)], [], "python").status == "located"


def test_a_typescript_file_is_typescript_and_camel_case_actions_become_snake_case():
    src = "await this.page.locator('#f').setInputFiles('a.txt');\n"
    t = triage("waiting for locator('#f')", [f("pages/p.ts", src)], [], "python")
    assert t.language == "typescript" and t.action == "set_input_files"


def test_the_action_is_none_when_the_locator_is_only_defined():
    t = triage(ERR, [f("pages/p.py", "self.user = self.page.locator('#username')\n")], [], "python")
    assert t.action is None


def test_a_hostile_file_does_not_crash_triage(monkeypatch):
    import voyageur.triage as mod

    def boom(_):
        raise RecursionError

    monkeypatch.setattr(mod, "iter_calls", boom)
    t = triage(ERR, [f("tests/t.py", TEST_PY)], [], "python")
    assert t.status == "needs_human" and t.reason == "source_not_found"
    deep = "page.locator('#username')" + ".locator('#a')" * 3000
    monkeypatch.undo()
    assert triage(ERR, [f("tests/t.py", deep)], [], "python").status in ("located", "needs_human")


def test_the_selector_literal_finds_a_get_by_call_the_printed_form_cannot():
    err = "waiting for get_by_label(\"Password\")"
    t = triage(err, [f("pages/login.py", PAGE_PY)], [], "python")
    assert t.status == "located" and "get_by_label('Password')" in t.call


# Playwright prints a selector as a double-quoted JSON-style string; the source may use either quote and its own escapes.
@pytest.mark.parametrize("src, printed", [
    ("""page.locator('button[aria-controls="box-busca-refinada"]').click()""",
     r'locator("button[aria-controls=\"box-busca-refinada\"]")'),
    ("""page.locator("xpath=//label[contains(text(), \\"What's the patient's birth date?\\")]").click()""",
     r'''locator("xpath=//label[contains(text(), \"What's the patient's birth date?\")]")'''),
    ("""page.locator('div:has(span:text-is("合集"))').click()""", r'locator("div:has(span:text-is(\"合集\"))")'),
    ("""page.locator('[aria-label="CSETv1:Physical Objects:no"]').check()""",
     r'locator("[aria-label=\"CSETv1:Physical Objects:no\"]")'),
])
def test_a_selector_matches_whatever_quotes_and_escapes_each_side_used(src, printed):
    err = f"TimeoutError: Locator.click: Timeout 1500ms exceeded.\nCall log:\n  - waiting for {printed}\n"
    t = triage(err, [f("tests/t.py", f"def test_x(page):\n    {src}\n")], [("tests/t.py", 2)], "python")
    assert t.status == "located", t.why


def test_values_that_differ_still_do_not_match():
    err = 'TimeoutError: x\nCall log:\n  - waiting for locator("button[aria-controls=\\"other\\"]")\n'
    src = """page.locator('button[aria-controls="box"]').click()"""
    t = triage(err, [f("tests/t.py", f"def test_x(page):\n    {src}\n")], [], "python")
    assert t.reason == "source_not_found"


@pytest.mark.parametrize("src, printed", [
    ("""page.locator("xpath=//label[contains(text(), \\"What's that?\\")]").click()""",
     r'''locator("//label[contains(text(), \"What's that?\")]")'''),
    ("""page.locator("data-test=category-space-permission >> .MuiSelect-select").click()""",
     'locator("data-test=category-space-permission").locator(".MuiSelect-select")'),
    ("""page.locator('form:nth-of-type(2) >> [data-qa="add"]').click()""",
     r'locator("form:nth-of-type(2)").locator("[data-qa=\"add\"]")'),
    ("""page.locator('[data-id="0"] > [data-field="fqdn"] ').click()""",
     r'locator("[data-id=\"0\"] > [data-field=\"fqdn\"]")'),
])
def test_selector_spellings_playwright_prints_differently_still_match(src, printed):
    err = f"TimeoutError: Locator.click: Timeout 1500ms exceeded.\nCall log:\n  - waiting for {printed}\n"
    t = triage(err, [f("tests/t.py", f"def test_x(page):\n    {src}\n")], [("tests/t.py", 2)], "python")
    assert t.status == "located", t.why


def test_a_chain_marker_inside_a_quoted_text_selector_is_not_split():
    from voyageur.failing import canon
    assert canon("""locator('text="a >> b"')""") == 'locator("text=\\"a >> b\\"")'


@pytest.mark.parametrize("tail, action", [(".first.click()", "click"), (".last.fill('x')", "fill"),
                                          (".nth(2).check()", "check"), (".first().click()", "click")])
def test_the_action_is_read_past_first_last_and_nth(tail, action):
    err = 'TimeoutError: x\nCall log:\n  - waiting for locator("[data-qa=\\"row\\"]")\n'
    src = f"""page.locator('[data-qa="row"]'){tail}"""
    t = triage(err, [f("tests/t.py", f"def test_x(page):\n    {src}\n")], [("tests/t.py", 2)], "python")
    assert t.status == "located" and t.action == action, (t.status, t.action, t.call)


def test_a_lone_surrogate_escape_does_not_crash_the_comparison():
    from voyageur.failing import canon
    out = canon('locator("\\ud800")')
    out.encode()                                            # no UnicodeEncodeError
    assert canon('locator("\\ud83d\\ude00")') == canon('locator("\U0001F600")')
