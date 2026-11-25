"""Unit: Playwright quotes a whole aria snapshot key when its name holds ": " (`- 'textbox "Email: work"': v`).
Those entries must still be counted and listed, or a locator that matches two of them looks unique."""
import pytest

from voyageur.redact import entry_key
from voyageur.uniqueness import aria_count

pytestmark = pytest.mark.unit

TWO_EMAILS = '- textbox "Email: work"\n- \'textbox "Email: work"\': a@b.c\n'


@pytest.mark.parametrize("line, key", [
    ('- \'textbox "Email: work"\': v', 'textbox "Email: work"'),
    ("- 'button \"It''s: here\"'", 'button "It\'s: here"'),
    ('- "textbox \\"Email: work\\"": v', 'textbox "Email: work"'),
    ('  - textbox "Plain"', 'textbox "Plain"'),
])
def test_entry_key_unquotes_the_key(line, key):
    assert entry_key(line) == key


def test_entry_key_is_none_for_a_line_that_is_not_an_entry():
    assert entry_key("not an entry") is None
    assert entry_key("- 'unterminated") is None


def test_a_quoted_key_entry_is_counted():
    assert aria_count('page.get_by_role("textbox", name="Email: work")', TWO_EMAILS) == 2


def test_a_lone_quoted_key_entry_is_found():
    assert aria_count('page.get_by_role("textbox", name="Email: work")', "- 'textbox \"Email: work\"': v\n") == 1


def test_the_inventory_redacts_typed_values_itself():
    from voyageur.inventory import Inventory
    inv = Inventory([{"tag": "input", "role": "textbox", "name": "Email", "hidden": False}], True,
                    '- textbox "Email": a@b.c\n- \'textbox "Email: work"\': secret-typed\n')
    assert "a@b.c" not in inv.aria and "secret-typed" not in inv.aria


def test_the_role_hint_lists_quoted_key_names():
    from voyageur.inventory import Inventory
    from voyageur.uniqueness import Uniqueness
    items = [{"tag": "input", "role": "textbox", "name": "Email: work", "hidden": False}]
    why = Uniqueness().missing('page.get_by_role("textbox", name="Phone")',
                               Inventory(items, True, "- 'textbox \"Email: work\"': v\n"))
    assert why and "'Email: work'" in why and "v'" not in why


def test_extra_spaces_after_the_dash_still_unquote():
    from voyageur.redact import role_line
    assert role_line("-   'textbox \"Email: work\"': v") == '-   textbox "Email: work": v'


def test_role_line_stays_linear_on_deeply_indented_trees():
    import time
    from voyageur.redact import role_line

    def took(n):
        lines = [" " * (2 * i) + "- 'button \"b: %d\"'" % i for i in range(n)]
        t = time.perf_counter()
        for ln in lines:
            role_line(ln)
        return time.perf_counter() - t, sum(map(len, lines))

    (t1, c1), (t4, c4) = took(250), took(1000)
    assert t4 / max(t1, 1e-6) < 3 * (c4 / c1)       # time per character stays flat (with room for timer noise)
