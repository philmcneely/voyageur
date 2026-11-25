"""Locator policy: the most stable locator for an element, by attribute priority (copy-based locators last)."""
import pytest

from voyageur.locator_policy import (DEFAULT_ORDER, KINDS, RANK, STABILITY_ORDER, ElementFacts, candidates, facts_of,
                                         is_generated_id, parse_order)

pytestmark = pytest.mark.unit

FULLNAME = ElementFacts(tag="input", id="fullname", name="fullname", role="textbox", accessible_name="Name and surname",
                        label="Name and surname")


def test_a_stable_id_beats_the_label():
    out = candidates(FULLNAME, "python")
    assert out[0] == 'page.locator("#fullname")'
    assert out.index('page.locator("#fullname")') < out.index('page.get_by_label("Name and surname", exact=True)')


def test_full_priority_order_python():
    f = ElementFacts(tag="input", test_id="account-username", id="username", name="username", role="textbox",
                     accessible_name="Login handle", label="Login handle", placeholder="handle")
    assert candidates(f, "python") == [
        'page.get_by_test_id("account-username")',
        'page.locator("#username")',
        "page.locator(\"input[name='username']\")",
        'page.get_by_role("textbox", name="Login handle", exact=True)',
        'page.get_by_label("Login handle", exact=True)',
        'page.get_by_placeholder("handle", exact=True)',
    ]


def test_full_priority_order_typescript_with_a_receiver():
    f = ElementFacts(tag="button", id="continue", role="button", accessible_name="Continue")
    assert candidates(f, "typescript", receiver="this.page") == [
        "this.page.locator('#continue')",
        "this.page.getByRole('button', { name: 'Continue', exact: true })",
    ]


@pytest.mark.parametrize("value", [":r1:", "react-select-3-input", "mui-12345", "ember482", "input-8f3a9c2b1d", "a1b2c3d4e5f6",
                                   "ext-gen1234", "field_1730762311"])
def test_generated_ids_are_skipped(value):
    assert is_generated_id(value)
    f = ElementFacts(tag="input", id=value, name="email", role="textbox", accessible_name="Email")
    assert not any(value in c for c in candidates(f, "python"))


@pytest.mark.parametrize("value", ["fullname", "pwd2", "billing-email", "step-form", "next", "user_handle", "h1-title"])
def test_hand_written_ids_are_kept(value):
    assert not is_generated_id(value)


def test_ids_that_are_not_simple_css_idents_use_an_attribute_selector():
    f = ElementFacts(tag="div", id="a.b c")
    assert candidates(f, "python") == ["page.locator(\"[id='a.b c']\")"]


def test_quotes_in_values_are_escaped():
    f = ElementFacts(tag="button", role="button", accessible_name="Don't \"stop\"")
    py, = candidates(f, "python")
    ts, = candidates(f, "typescript")
    assert py == 'page.get_by_role("button", name="Don\'t \\"stop\\"", exact=True)'
    assert ts == "page.getByRole('button', { name: 'Don\\'t \"stop\"', exact: true })"


def test_an_element_with_nothing_stable_gives_no_candidates():
    assert candidates(ElementFacts(tag="span"), "python") == []


def test_order_is_configurable_and_validated():
    assert parse_order("role,testid") == ("role", "testid")
    assert candidates(FULLNAME, "python", order=parse_order("label,id"))[0] == 'page.get_by_label("Name and surname", exact=True)'
    with pytest.raises(ValueError, match="xpath"):
        parse_order("testid,xpath")
    assert parse_order("testid,css,text") == ("testid", "css", "text")
    assert DEFAULT_ORDER[:3] == ("testid", "id", "name")
    assert "css" not in DEFAULT_ORDER and "text" not in DEFAULT_ORDER          # fallbacks are asked for by name
    assert STABILITY_ORDER == tuple(sorted(KINDS, key=lambda k: RANK[k]))      # one order, the one RANK states


# --- stabilize against the failure-time inventory (the elements the client sent) --------------------------------
from voyageur.inventory import Inventory  # noqa: E402
from voyageur.locator_policy import stabilize  # noqa: E402

ELEMENTS = [      # what the client's probe lists, one dict per element (the original read these from a text listing)
    {"tag": "input", "type": "text", "label": "Nom et prénom", "id": "fullname", "name_attr": "fullname"},
    {"tag": "input", "type": "email", "label": "Adresse e-mail", "placeholder": "nom@societe.fr", "id": "email",
     "name_attr": "email"},
    {"tag": "input", "type": "text", "label": "Identifiant", "id": "username", "testid": "account-username",
     "name_attr": "username"},
    {"tag": "input", "type": "checkbox", "label": "Musique", "name_attr": "interests"},
    {"tag": "input", "type": "checkbox", "label": "Sport", "name_attr": "interests"},
    {"tag": "button", "type": "submit", "name": "Continuer", "id": "continue", "class": "primary"},
]
INV = Inventory(ELEMENTS, True)


@pytest.mark.parametrize("answer,expected", [
    ('page.get_by_label("Nom et prénom")', 'page.locator("#fullname")'),
    ('page.get_by_label("Identifiant")', 'page.get_by_test_id("account-username")'),
    ('page.get_by_placeholder("nom@societe.fr")', 'page.locator("#email")'),
    ('page.locator("#continue")', 'page.locator("#continue")'),
])
def test_a_copy_based_answer_is_rewritten_to_the_stable_locator(answer, expected):
    assert stabilize(answer, INV, "python", "page") == expected


def test_typescript_and_the_test_receiver_are_kept():
    assert stabilize("this.page.getByLabel('Nom et prénom')", INV, "typescript", "this.page") == "this.page.locator('#fullname')"


def test_shared_name_attribute_falls_through_to_the_next_unique_kind():
    # both checkboxes share name='interests': the name locator is not unique, so the label stays
    assert stabilize('page.get_by_label("Musique")', INV, "python", "page") == 'page.get_by_label("Musique")'


def test_unknown_or_ambiguous_answers_are_left_alone():
    assert stabilize('page.get_by_label("Inconnu")', INV, "python", "page") == 'page.get_by_label("Inconnu")'
    assert stabilize('page.locator("form").get_by_label("Nom et prénom")', INV, "python", "page") == \
        'page.locator("form").get_by_label("Nom et prénom")'


@pytest.mark.parametrize("expr,kind", [
    ('page.get_by_test_id("a")', "testid"), ("page.locator('#fullname')", "id"), ('page.locator("[id=\\"a b\\"]")', "id"),
    ("page.locator('input[name=\"email\"]')", "name"), ("this.page.locator('input.new-todo')", "css"),
    ('page.get_by_role("button", name="Next")', "role"), ("page.getByLabel('Email')", "label"),
    ('page.get_by_placeholder("x")', "placeholder"), ('page.get_by_text("Saved")', "text"),
    ('page.locator("form").get_by_label("x")', "chain"), ('page.locator("div > div > span")', "chain"),
])
def test_kind_of(expr, kind):
    from voyageur.locator_policy import kind_of
    assert kind_of(expr) == kind


def test_an_answer_already_as_stable_as_the_element_allows_is_kept_verbatim():
    # Musique has no id / test id / unique name: the label is the best it has, so the model's own text stays
    assert stabilize('page.get_by_label("Musique")', INV, "python", "page") == 'page.get_by_label("Musique")'
    assert stabilize('page.locator("#continue")', INV, "python", "page") == 'page.locator("#continue")'


# --- copy-only locators branch by page language -----------------------------------------------------------------
from voyageur.locator_policy import locale_branch, page_language  # noqa: E402


def test_page_language_comes_from_html_lang_then_browser_locale():
    assert page_language({"lang": "fr-CA"}) == "fr"
    assert page_language({"lang": "", "browser_lang": "es-MX"}) == "es"
    assert page_language({}) == "" and page_language(None) == ""


def test_a_copy_literal_becomes_a_language_entry_python():
    assert locale_branch('page.get_by_label("Music")', "page.get_by_label('Musique')", "fr", "python") == \
        'page.get_by_label(localized(page, "Music", fr="Musique"))'


def test_role_name_branch_typescript_with_receiver():
    assert locale_branch("this.page.getByRole('button', { name: 'Next' })", "this.page.getByRole('button', { name: 'Suivant' })",
                         "fr", "typescript") == \
        "this.page.getByRole('button', { name: await localized(this.page, 'Next', { fr: 'Suivant' }) })"


def test_a_second_language_is_added_and_an_existing_entry_replaced():
    one = 'page.get_by_label(localized(page, "Music", fr="Musique"))'
    assert locale_branch(one, 'page.get_by_label("Música")', "es", "python") == \
        'page.get_by_label(localized(page, "Music", fr="Musique", es="Música"))'
    assert locale_branch(one, 'page.get_by_label("Musique (option)")', "fr", "python") == \
        'page.get_by_label(localized(page, "Music", fr="Musique (option)"))'


@pytest.mark.parametrize("old,new", [
    ('page.get_by_label("Music")', 'page.get_by_role("checkbox", name="Musique")'),     # a different method
    ('page.get_by_label("Music")', 'page.get_by_label("Musique", exact=True)'),         # more than the copy changed
    ('page.get_by_role("button", name="Next")', 'page.get_by_role("link", name="Suivant")'),   # two literals changed
])
def test_no_branch_unless_only_the_copy_changed(old, new):
    assert locale_branch(old, new, "fr", "python") is None


# --- a hostile page cannot write code into the test file ---------------------------------------------------------
@pytest.mark.parametrize("lang", ["x=exec(page.title()),y", "fr'}); evil(); ({a:'", "f r", "fr9", "ab=c", "", "toolong"])
def test_a_hostile_html_lang_is_no_language(lang):
    assert page_language({"lang": lang}) == ""


@pytest.mark.parametrize("lang", ["x=exec(1),y", "fr'}); evil(); ({a:'", "FR", "f"])
def test_locale_branch_refuses_a_key_that_is_not_a_language(lang):
    assert locale_branch('page.get_by_label("Music")', 'page.get_by_label("Musique")', lang, "python") is None
    assert locale_branch("page.getByLabel('Music')", "page.getByLabel('Musique')", lang, "typescript") is None


def test_typescript_literals_escape_line_breaks():
    out, = candidates(ElementFacts(tag="button", role="button", accessible_name="a\nb\r c d'e"), "typescript")
    assert "\n" not in out and "\r" not in out and " " not in out and " " not in out
    assert out == "page.getByRole('button', { name: 'a\\nb\\r\\u2028c\\u2029d\\'e', exact: true })"


@pytest.mark.parametrize("new,expected", [
    ('page.get_by_label("\\x41bc")', 'page.get_by_label(localized(page, "Music", fr="Abc"))'),
    ("page.get_by_label('\\x41bc')", 'page.get_by_label(localized(page, "Music", fr="Abc"))'),
    ('page.get_by_label("\\N{BULLET} x")', 'page.get_by_label(localized(page, "Music", fr="• x"))'),
])
def test_python_escapes_in_an_answer_are_decoded_not_fatal(new, expected):
    assert locale_branch('page.get_by_label("Music")', new, "fr", "python") == expected


def test_an_undecodable_literal_leaves_the_answer_alone():
    assert locale_branch('page.get_by_label("Music")', 'page.get_by_label("\\N{NO SUCH NAME}")', "fr", "python") is None


@pytest.mark.parametrize("lit", ['"\\ud800"', "'\\udc80x'"])
def test_a_lone_surrogate_is_refused_so_the_file_stays_valid_utf8(lit):
    assert locale_branch('page.get_by_label("Music")', f"page.get_by_label({lit})", "fr", "python") is None


# --- data-qa / data-cy / data-test count as test ids -------------------------------------------------------------
def test_test_automation_attributes_rank_with_test_ids():
    f = ElementFacts(tag="button", test_attrs=(("data-qa", "export-csv"), ("data-cy", "export")), id="btn-7",
                     role="button", accessible_name="Export")
    out = candidates(f, "python")
    assert out[:3] == ["page.locator(\"[data-qa='export-csv']\")", "page.locator(\"[data-cy='export']\")",
                       'page.locator("#btn-7")']
    from voyageur.locator_policy import kind_of
    assert kind_of("page.locator(\"[data-qa='status']\")") == "testid" and kind_of("page.locator('[data-cy=export]')") == "testid"


def test_facts_of_reads_test_automation_attributes():
    from voyageur.locator_policy import facts_of
    f = facts_of({"tag": "td", "data-qa": "status", "data_cy": "st"})
    assert ("data-qa", "status") in f.test_attrs and ("data-cy", "st") in f.test_attrs


def test_a_unique_name_attribute_is_counted_and_used():
    inv = Inventory([{"tag": "input", "type": "email", "label": "E-mail", "name_attr": "email"},
                     {"tag": "input", "type": "text", "label": "Ville", "name_attr": "city"}], True)
    assert stabilize('page.get_by_label("E-mail")', inv, "python", "page") == "page.locator(\"input[name='email']\")"
    assert stabilize("page.getByLabel('Ville')", inv, "typescript", "page") == "page.locator('input[name=\"city\"]')"


def test_stabilize_only_uses_a_candidate_that_hits_the_same_element():
    class Inv:                      # a candidate that counts 1 but resolves to a different element
        def __init__(self):
            self.a = {"tag": "input", "label": "E-mail", "name_attr": "email"}
            self.b = {"tag": "input", "label": "Other", "name_attr": "email2"}

        def matched(self, expr):
            return [self.b] if "name=" in expr else [self.a]

        def count(self, expr):
            return 1
    assert stabilize('page.get_by_label("E-mail")', Inv(), "python", "page") == 'page.get_by_label("E-mail")'


# --- chained copy locators branch per literal --------------------------------------------------------------------
from voyageur.locator_policy import copy_based  # noqa: E402


def test_copy_based_finds_copy_calls_anywhere_in_a_chain():
    assert copy_based('page.get_by_role("dialog").get_by_label("I agree to the terms")')
    assert copy_based('page.get_by_role("row", name="Team").get_by_role("button", name="Select")')
    assert not copy_based('page.get_by_test_id("orders").locator("tbody tr").nth(3)')
    assert not copy_based('page.locator("#fullname")')


def test_a_scoped_chain_branches_each_copy_literal_that_changed():
    old = 'page.get_by_role("row", name="Team").get_by_role("button", name="Select")'
    new = 'page.get_by_role("row", name="Équipe").get_by_role("button", name="Choisir")'
    assert locale_branch(old, new, "fr", "python") == (
        'page.get_by_role("row", name=localized(page, "Team", fr="Équipe"))'
        '.get_by_role("button", name=localized(page, "Select", fr="Choisir"))')


def test_a_dialog_scope_keeps_its_unchanged_literal():
    old = 'page.get_by_role("dialog").get_by_label("I agree to the terms")'
    new = 'page.get_by_role("dialog").get_by_label("J\'accepte les conditions")'
    assert locale_branch(old, new, "fr", "python") == \
        'page.get_by_role("dialog").get_by_label(localized(page, "I agree to the terms", fr="J\'accepte les conditions"))'


def test_a_structural_change_alongside_the_copy_is_not_branched():
    assert locale_branch('page.get_by_role("button", name="Select").nth(1)',
                         'page.get_by_role("button", name="Choisir").nth(2)', "fr", "python") is None


def test_a_text_locator_is_not_rewritten_to_the_container_that_merely_contains_the_text():
    """real v10 s6: get_by_text finds the smallest element with the text (an unlisted inner div); the listed #app only
    contains it, so its innerText reads the same."""
    from voyageur.inventory import Inventory
    inv = Inventory([{"tag": "div", "id": "app", "name": "Earn 10 coins", "hidden": False}], True)
    expr = 'page.get_by_text("Earn 10 coins")'
    assert stabilize(expr, inv, "python", "page") == expr


def test_a_text_locator_on_a_control_is_still_stabilized():
    from voyageur.inventory import Inventory
    inv = Inventory([{"tag": "button", "id": "save", "name": "Save draft", "hidden": False}], True)
    assert stabilize('page.get_by_text("Save draft")', inv, "python", "page") == 'page.locator("#save")'


# --- new in this repo: tag validation, css and text candidates, Content-Language, a plain unquote ------------------
@pytest.mark.parametrize("tag", ["div'][onclick=alert(1)", "DIV", "a b", "1x", "x]", "input\n", ""])
def test_a_tag_that_is_not_a_plain_element_name_is_never_written_into_a_selector(tag):
    assert facts_of({"tag": tag, "name_attr": "q"}).tag == ""
    out = candidates(ElementFacts(tag=tag, name="q"), "python")
    assert out == ["page.locator(\"[name='q']\")"]                         # the tag is left out, never the hostile text


@pytest.mark.parametrize("tag", ["input", "my-widget", "h1", "a"])
def test_a_plain_tag_is_kept(tag):
    assert facts_of({"tag": tag}).tag == tag


def test_every_written_locator_survives_the_replacement_validator_even_for_hostile_facts():
    from voyageur.locators import validate_replacement
    f = facts_of({"tag": "div", "id": "a'b\"c", "name_attr": "n'\"\\", "label": "l'\"", "placeholder": "p\\'", "role": "button",
                  "name": "x\"'\\", "class": "ok-1 bad'class", "text": "t'\"\\\n"})
    for language, call in (("python", "page.locator('#x')"), ("typescript", "page.locator('#x')")):
        for cand in candidates(f, language, order=STABILITY_ORDER):
            assert validate_replacement(call, cand, language) is None, cand


def test_css_candidate_is_one_compound_selector_on_plain_non_generated_classes():
    f = facts_of({"tag": "input", "class": "new-todo wide extra", "placeholder": "x"})
    assert candidates(f, "python", order=("css",)) == ['page.locator("input.new-todo.wide")']       # at most two
    assert candidates(f, "typescript", "this.page", ("css",)) == ["this.page.locator('input.new-todo.wide')"]
    assert candidates(facts_of({"tag": "div", "class": "css-8f3a9c2b1d x:y bad'q"}), "python", order=("css",)) == []
    from voyageur.locator_policy import kind_of
    assert kind_of('page.locator("input.new-todo.wide")') == "css"


def test_css_and_text_are_fallbacks_not_part_of_the_default_order():
    f = facts_of({"tag": "p", "class": "note", "text": "Order shipped"})
    assert candidates(f, "python") == []
    assert candidates(f, "python", order=STABILITY_ORDER) == ['page.locator("p.note")',
                                                              'page.get_by_text("Order shipped", exact=True)']
    assert candidates(f, "typescript", order=("text",)) == ["page.getByText('Order shipped', { exact: true })"]


def test_text_candidate_is_capped_and_ranks_last():
    f = facts_of({"tag": "p", "text": "x" * 500})
    assert len(f.text) == 200
    from voyageur.locator_policy import kind_of
    assert kind_of('page.get_by_text("Saved", exact=True)') == "text" and max(RANK.values()) == RANK["chain"]


def test_page_language_falls_back_to_content_language_last():
    assert page_language({"content_language": "fr-CA"}) == "fr"
    assert page_language({"content_language": "de, en"}) == "de"
    assert page_language({"lang": "es", "content_language": "fr"}) == "es"
    assert page_language({"browser_lang": "it-IT", "content_language": "fr"}) == "it"
    assert page_language({"content_language": "x=1,y"}) == "" and page_language({"lang": 5}) == ""
    assert page_language("not a dict") == ""


@pytest.mark.parametrize("lit,value", [
    ("'plain'", "plain"), ('"it\'s"', "it's"), ("'it\\'s'", "it's"), ('"a\\nb"', "a\nb"), ("'\\\\'", "\\"),
    ("'\\x41'", "A"), ("'\\u00e9'", "é"), ("'\\U0001F600'", "\U0001F600"), ("'\\101'", "A"), ("'\\N{BULLET}'", "•"),
    ("'\\d'", "\\d"), ("'a\\\nb'", "ab"), ("'{__import__(1)}\\'x'", "{__import__(1)}'x"),
])
def test_unquote_decodes_with_python_rules_and_nothing_is_evaluated(lit, value):
    from voyageur.locator_policy import _unquote
    assert _unquote(lit) == value


@pytest.mark.parametrize("lit", ["", "x", "'", "'a", "a'", "'a'b'", "'a\nb'", "'\\x4'", "'\\u{1F600}'", "'\\N{NOPE}'",
                                 "'\\ud800'", "'\\U00110000'", "'trailing\\'", "f'x'"])
def test_unquote_refuses_what_is_not_one_clean_literal(lit):
    from voyageur.locator_policy import _unquote
    with pytest.raises(ValueError):
        _unquote(lit)
