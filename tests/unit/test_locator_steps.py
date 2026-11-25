"""Unit: the locator-chain parser (a locator is read as data, never run; literals only)."""
import json
import random
import string

import pytest

from voyageur.locator_steps import (METHOD_NAMES, OPTION_NAMES, PROPERTY_NAMES, ChainRejected, Regex, chain_to_data,
                                     chain_to_json, parse_chain, parse_py_chain, parse_ts_chain, try_parse)

pytestmark = pytest.mark.unit


def steps(expr, language="typescript"):
    return [(s.method, s.args, s.options, s.prop) for s in parse_chain(expr, language).steps]


# --- what is accepted ------------------------------------------------------------------------
def test_ts_chain_is_split_into_method_and_literal_steps():
    assert steps("page.getByRole('button', { name: 'Close', exact: true }).nth(2)") == [
        ("getByRole", ("button",), {"name": "Close", "exact": True}, False), ("nth", (2,), {}, False)]


def test_ts_regex_template_and_escapes_are_read_as_values():
    got = steps("page.locator('div').getByText(/^a\\/b[/]$/i).getByLabel(`x\\ny`).getByTitle('it\\'s \\u00e9\\x41')")
    assert got[1][1] == (Regex("^a\\/b[/]$", "i"),)
    assert got[2][1] == ("x\ny",)
    assert got[3][1] == ("it's éA",)


def test_ts_nested_chains_in_has_and_or():
    got = steps("page.locator('div', { has: page.locator('#a'), hasNotText: /x/ }).and(page.getByText('t')).first()")
    assert [g[0] for g in got] == ["locator", "and", "first"]
    assert got[0][2]["has"].steps[0].args == ("#a",)
    assert got[1][1][0].steps[0].method == "getByText"


def test_ts_whitespace_trailing_commas_and_quoted_keys():
    assert steps("page . locator ( '#a' , )\n .nth( -1 )")[1][1] == (-1,)
    assert steps("page.getByRole('button', { 'name': \"x\", })")[0][2] == {"name": "x"}


def test_python_chains_with_kwargs_properties_and_nesting():
    got = steps("page.locator('div', has=page.get_by_text('t')).first.get_by_role('button', name='Go', exact=True)",
                "python")
    assert [(g[0], g[3]) for g in got] == [("locator", False), ("first", True), ("get_by_role", False)]
    assert got[2][2] == {"name": "Go", "exact": True}
    assert steps("page.locator('a').nth(-2)", "python")[1][1] == (-2,)


# --- what is refused -------------------------------------------------------------------------
@pytest.mark.parametrize("expr", [
    "page.locator('a')['constructor']",
    "page.locator('a').constructor",
    "page.locator('a')[0]",
    "page.locator(`a${1}`)",
    "page.locator(a)",
    "page.locator('a' + 'b')",
    "page.locator('a'), 1",
    "page.locator('a');",
    "page.locator('a') x",
    "page.locator('a'",
    "page.locator('a'))",
    "page.locator('a')(",
    "page.locator('unterminated)",
    "page.locator('a\nb')",
    "page.locator('a\\1')",
    "page.locator('a\\u12')",
    "page.locator('a\\u{110000}')",
    "page.locator()",
    "page.locator('a', 'b')",
    "page.locator('a', { has: 1 })",
    "page.locator('a', { nope: 1 })",
    "page.locator('a', { has: page.locator('b') }, 1)",
    "page.locator({ has: 'a' })",
    "page.locator('a', { has: page.locator('b') , has: page.locator('c') })",
    "page.locator('a', { ['has']: page.locator('b') })",
    "page.locator('a', { ...x })",
    "page.locator('a', { has })",
    "page.locator('a', { has: { } })",
    "page.getByRole('button', { name: [1] })",
    "page.getByRole('button', { name: undefined })",
    "page.getByRole('button', { name: 1e3 })",
    "page.nth(1)",
    "page.first()",
    "page.click('a')",
    "page.goto('http://x')",
    "page.locator('a').click()",
    "page.locator('a').evaluate('1')",
    "page.locator('a').count()",
    "page.locator('a').nth('1')",
    "page.locator('a').nth(1.5)",
    "page.locator('a').nth(07)",
    "page.locator('a').nth(0x10)",
    "page.locator('a').nth(99999999999)",
    "page.locator('a').nth(1n)",
    "page.locator('a').filter({ hasText: 'x' }, 1)",
    "page.locator('a').and('b')",
    "page.locator('a').getByText(/x/gg)",
    "page.locator('a').getByText(/x/q)",
    "page.locator('a').getByText(//)",
    "page.locator('a').getByText(/*x*/)",
    "page.locator('a').getByText(/x",
    "page.locator('a').getByText(/x/ig2)",
    "page.locator('a') .first()",
    "page.locator('a').first ()",
    "page.locator('a') // c",
    "that.page.locator('a')",
    "this.locator('a')",
    "this.page",
    "await page.locator('a')",
    "",
    " ",
    "page",
    "page.",
    ".locator('a')",
    "x".join(["page.locator('a')"] * 2),
])
def test_ts_refuses(expr):
    with pytest.raises(ChainRejected):
        parse_ts_chain(expr)
    assert try_parse(expr, "typescript") is None


@pytest.mark.parametrize("expr", [
    "page.locator('a')['constructor']",
    "page.locator(f'{x}')",
    "page.locator(x)",
    "page.locator('a', has=1)",
    "page.locator('a', **kw)",
    "page.locator(*a)",
    "page.locator('a').first()",
    "page.locator('a').last.first()",
    "page.locator('a').nth(True)",
    "page.locator('a').nth(1.0)",
    "page.locator('a').click()",
    "page.locator('a').filter(nope=1)",
    "page.get_by_role('button', name=/x/)",
    "page.get_by_role('button', name=b'x')",
    "page.get_by_role('button', name=...)",
    "page.get_by_role('button', name=1j)",
    "page.get_by_role('button', name=1e999)",
    "page.get_by_role('button', name=10 ** 400)",
    "page.get_by_foo('x')",
    "page.locator('a') if 1 else page.locator('b')",
    "page.locator('a') and page.locator('b')",
    "(lambda: 1)()",
    "page.locator('a').__class__",
    "getattr(page, 'locator')('a')",
    "page.locator('a')[0]",
    "page",
    "other.locator('a')",
    "that.page.locator('a')",
    "self.locator('a')",
    "page.locator('a'); x",
    "page.locator(",
    "",
])
def test_python_refuses(expr):
    with pytest.raises(ChainRejected):
        parse_py_chain(expr)
    assert try_parse(expr, "python") is None


def test_limits_cap_hostile_size_and_depth():
    long_chain = "page.locator('a')" + ".first()" * 40
    assert try_parse(long_chain, "typescript") is None
    nested = "page.locator('a')"
    for _ in range(8):
        nested = f"page.locator('a', {{ has: {nested} }})"
    assert try_parse(nested, "typescript") is None
    assert try_parse("page.locator('" + "a" * 5000 + "')", "typescript") is None
    py_nested = "page.locator('a')"
    for _ in range(8):
        py_nested = f"page.locator('a', has={py_nested})"
    assert try_parse(py_nested, "python") is None
    assert try_parse("page.locator(" * 500 + ")" * 500, "python") is None


def test_python_and_ts_use_their_own_method_names():
    assert try_parse("page.getByRole('button')", "python") is None
    assert try_parse("page.get_by_role('button')", "typescript") is None


# --- serialisation -----------------------------------------------------------------------------
def test_json_is_ascii_and_round_trips_values():
    chain = parse_ts_chain("page.getByText('café \\ud83d\\ude00', { exact: true }).filter({ has: page.locator('b') })")
    text = chain_to_json(chain)
    assert text.isascii()
    data = json.loads(text)
    assert data[0]["a"] == ["café 😀"] and data[0]["o"] == {"exact": True}
    assert data[1]["o"]["has"] == {"$loc": [{"m": "locator", "a": ["b"], "o": {}, "p": False}]}
    assert json.loads(json.dumps(chain_to_data(chain))) == data


# --- fuzz: nothing hostile comes out as anything but parsed steps ---------------------------
SEEDS = [
    "page.locator('a')['constructor']['constructor']('return process.env')()",
    "page.getByRole('button', { name: /Close/i, exact: true }).nth(1)",
    "page.locator('div', { has: page.locator('#a') }).first()",
    "page.get_by_role('button', name='Go').first.locator('b')",
    "page.locator(`x${process.env.GH_TOKEN}`)",
]
FRAGMENTS = ["['constructor']", "[`constructor`]", "${process.env.X}", "`", "'", '"', "\\", "/*", "*/", "//", "/", "(", ")",
             "{", "}", "[", "]", ",", ":", ";", ".", "=", "=>", "+", "-", "?.", "...", "__proto__", "constructor",
             "process", "require('fs')", "eval", "Function", "import(", "this", "await ", "\n", "\r", " ",
             " ", "\0", "page", "new ", "globalThis", "x => x", "async ", "1e3", "0x1", "07", "1n", "#", "@",
             "$", "%", "&", "|", "^", "~", "!", "<", ">", "\\u{", "\\x", "/gg", "/i"]
ALPHABET = string.printable + "   \ud83dé\0"


def _fuzz_inputs(n: int):
    rnd = random.Random(20261104)
    for _ in range(n):
        kind = rnd.randrange(3)
        if kind == 0:
            yield "".join(rnd.choice(ALPHABET) for _ in range(rnd.randrange(1, 80)))
        elif kind == 1:
            s = rnd.choice(SEEDS)
            for _ in range(rnd.randrange(1, 4)):
                at = rnd.randrange(len(s) + 1)
                s = s[:at] + rnd.choice(FRAGMENTS) + s[at:]
            yield s
        else:
            yield "page." + "".join(rnd.choice(FRAGMENTS + ["locator", "getByRole", "first", "('a')", "('b', {"])
                                    for _ in range(rnd.randrange(1, 12)))


def _only_parsed_steps(data, language, depth=0):
    """Every step of a serialised chain uses a listed method and option and nothing but JSON scalars / $re / $loc."""
    assert isinstance(data, list) and data
    for s in data:
        assert set(s) == {"m", "a", "o", "p"}
        assert s["m"] in METHOD_NAMES[language]
        assert s["p"] is False or s["m"] in PROPERTY_NAMES
        for v in list(s["a"]) + list(s["o"].values()):
            if isinstance(v, dict):
                assert list(v) in (["$re"], ["$loc"])
                if "$loc" in v:
                    _only_parsed_steps(v["$loc"], language, depth + 1)
            else:
                assert v is None or isinstance(v, (str, int, float, bool))
        assert set(s["o"]) <= set(OPTION_NAMES[language])


@pytest.mark.parametrize("language", ["typescript", "python"])
def test_fuzzed_input_is_either_refused_or_comes_out_as_parsed_steps(language):
    # Here the parser's own output is checked, which is the
    # property that matters (whatever is accepted is only listed methods and JSON scalars, never the raw text).
    accepted = 0
    inputs = list(_fuzz_inputs(600)) + SEEDS
    for expr in inputs:
        chain = try_parse(expr, language)
        if chain is None:
            continue
        accepted += 1
        data = chain_to_data(chain)
        _only_parsed_steps(json.loads(chain_to_json(chain)), language)
        assert json.loads(chain_to_json(chain)) == data
    # the corpus must exercise both outcomes, or the test proves nothing
    assert accepted >= 1 and accepted < len(inputs)


def test_fuzz_corpus_contains_accepted_and_refused_inputs_for_both_languages():
    assert any(try_parse(s, "typescript") for s in SEEDS)
    assert any(try_parse(s, "python") for s in SEEDS)
    outcomes = [bool(try_parse(s, "typescript")) for s in _fuzz_inputs(600)]
    assert not all(outcomes)


# --- receivers and trailing properties (describe() needs the locators real suites use) -------------------------
@pytest.mark.parametrize("expr,language", [
    ("this.page.locator('#a').first()", "typescript"),
    ("this.page.getByTestId('x')", "typescript"),
    ("self.page.locator('#a').first", "python"),
    ("this.page.locator('#a')", "python"),
    ("page.locator('a').first", "python"),
    ("page.locator('a').nth(1).last", "python"),
    ("page.locator('div', has=self.page.locator('b'))", "python"),
    ("page.locator('div', { has: this.page.locator('b') })", "typescript"),
])
def test_page_receivers_and_trailing_properties_are_accepted(expr, language):
    assert try_parse(expr, language) is not None


def test_a_trailing_property_is_one_step_marked_as_a_property():
    assert steps("page.locator('ul li').last", "python")[-1] == ("last", (), {}, True)
    assert chain_to_data(parse_py_chain("page.locator('a').first"))[-1]["p"] is True


@pytest.mark.parametrize("expr,language", [
    ("page.locator('form > div:nth-child(2) > input')", "python"),
    ("page.locator('li:nth-of-type(3) >> a')", "typescript"),
    ("page.locator('xpath=//div[@id=\"a\"]/button[2]')", "python"),
    ("page.locator('//div/button')", "typescript"),
    ("page.frameLocator('iframe#f').locator('button').nth(2)", "typescript"),
    ("page.frame_locator('iframe').get_by_role('button', name='Go', exact=True)", "python"),
    ("page.locator('my-host button')", "typescript"),
])
def test_structural_xpath_frame_and_shadow_locators_are_plain_strings(expr, language):
    assert try_parse(expr, language) is not None


@pytest.mark.parametrize("expr,language", [
    ("page.getByTestId('save-btn')", "typescript"),
    ("page.locator('#user-name')", "typescript"),
    ("page.locator('input[name=\"email\"]')", "typescript"),
    ("page.locator('[data-qa=\"go\"]')", "typescript"),
    ("page.locator('[data-cy=\"go\"]')", "typescript"),
    ("page.getByRole('button', { name: 'Don\\'t \u00e9', exact: true })", "typescript"),
    ("page.getByLabel('Email', { exact: true })", "typescript"),
    ("page.getByPlaceholder('Search\u2028x', { exact: true })", "typescript"),
    ("page.get_by_test_id(\"save-btn\")", "python"),
    ("page.locator(\"#user-name\")", "python"),
    ("page.locator(\"input[name=\\\"email\\\"]\")", "python"),
    ("page.get_by_role(\"button\", name=\"Caf\u00e9\", exact=True)", "python"),
    ("page.get_by_label(\"Email\", exact=True)", "python"),
    ("page.get_by_placeholder(\"Search\", exact=True)", "python"),
    ("this.page.getByTestId('save-btn')", "typescript"),
    ("self.page.get_by_test_id(\"save-btn\")", "python"),
])
def test_every_locator_policy_candidate_shape_is_accepted(expr, language):
    assert try_parse(expr, language) is not None
