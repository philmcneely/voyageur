"""
===============================================================================
Typed-value redaction (ported cases)
===============================================================================
An accessibility snapshot reaches the service without what a person typed, in every quoting and nesting form
Playwright prints.
"""

from voyageur.redact import redact_typed_values

YAML = '''- heading "Sign in" [level=1]
- textbox "Email" [ref=e3]: me@example.com
- textbox: hunter2
- textbox "Notes": "line one line: two"
- searchbox "Find": secret q
- spinbutton "Qty": "42"
- textbox "D" [disabled]: "a: b"
- slider "R": "50"
- combobox:
  - option "Hello" [selected]
- paragraph: Plain: prose stays
- button "Send"'''


def test_values_are_dropped_but_roles_and_names_stay():
    out = redact_typed_values(YAML)
    for typed in ("me@example.com", "hunter2", "line one", "secret q", "42", "a: b", "50"):
        assert typed not in out
    assert '- textbox "Email" [ref=e3]' in out and '- searchbox "Find"' in out
    assert "- paragraph: Plain: prose stays" in out and '- option "Hello" [selected]' in out


def test_yaml_single_quoted_keys_lose_the_value_and_keep_name_and_states():
    out = redact_typed_values("\n".join([
        "- 'textbox \"Email: work\"': jane@x.com",
        "- 'textbox \"Email: work\" [disabled]': jane2@x.com",
        "- 'textbox \"Both: \\\"q\\\" ''x''\"': \"SECRET3: a #b\"",
        "- 'combobox \"Combo: z\"': SECRET9",
        "- 'searchbox \"Srch: s\" [ref=e4]': SECRET7"]))
    assert not any(s in out for s in ("jane", "SECRET"))
    assert "- 'textbox \"Email: work\"'" in out and "- 'textbox \"Email: work\" [disabled]'" in out
    assert "- 'textbox \"Both: \\\"q\\\" ''x''\"'" in out and "- 'searchbox \"Srch: s\" [ref=e4]'" in out


def test_double_quoted_keys_lose_the_value():
    out = redact_typed_values('- "textbox \\"A: b\\" [disabled]": SECRET1\n- "slider \\"R: x\\"": 50')
    assert "SECRET1" not in out and ": 50" not in out
    assert '- "textbox \\"A: b\\" [disabled]"' in out and '- "slider \\"R: x\\""' in out


def test_a_typed_value_printed_as_a_nested_child_is_dropped():
    out = redact_typed_values("\n".join([
        '- textbox "Ph":', '  - /placeholder: "ph: x"', "  - text: SECRET16", "- button \"Go\"",
        "- 'textbox \"CE: name\"':", "  - link \"SECRET17\":", "    - /url: \"#x\"",
        "  - text: SECRET18", "- textbox:", "  - text: SECRET19", "  - /value: SECRET20", "- paragraph: kept"]))
    assert "SECRET" not in out
    assert '- /placeholder: "ph: x"' in out and "- button \"Go\"" in out and "- paragraph: kept" in out
    assert out.count("- textbox") + out.count("- 'textbox") == 3


def test_a_combobox_keeps_its_options_but_not_a_nested_text_value():
    out = redact_typed_values("\n".join([
        "- 'combobox \"Pick: one\"':", "  - 'option \"Alpha: 1\"'", "  - option \"b\" [selected]",
        "  - text: SECRET21", "- 'combobox \"C\"': SECRET22"]))
    assert "SECRET" not in out
    assert "- 'option \"Alpha: 1\"'" in out and '- option "b" [selected]' in out


def test_irregular_spacing_and_indent_still_redact():
    out = redact_typed_values(
        '-  textbox "A": SECRET1\n- form:\n    -   searchbox "B" [x] :   SECRET2\n- textbox "C"  :SECRET3')
    assert "SECRET" not in out
    assert '"A"' in out and '"B" [x]' in out and '"C"' in out


def test_a_value_of_a_multi_line_input_is_dropped_whole():
    out = redact_typed_values('- textbox "N": |\n    SECRET1 line\n    SECRET2 line: x\n- button "Go"')
    assert "SECRET" not in out and '- button "Go"' in out


def test_non_value_lines_are_untouched():
    text = ("- text: \"Email: work\"\n- 'link \"A: b\"':\n  - /url: x\n- paragraph: \"Plain: prose\"\n"
            "- button \"Go\" [disabled]")
    assert redact_typed_values(text) == text


def test_redaction_is_idempotent_and_linear_on_hostile_input():
    import time
    once = redact_typed_values(YAML)
    assert redact_typed_values(once) == once
    hostile = ("- 'textbox \"" + "a' " * 20000 + "\n- textbox \"" + '\\"' * 20000 + "\n-" + " " * 20000 + "textbox: x\n"
               + "- textbox " + "[" * 50000)
    t0 = time.perf_counter()
    redact_typed_values(hostile)
    assert time.perf_counter() - t0 < 1.0



def test_slider_value_child_block_scalar_and_states_inside_quoted_key_are_dropped():
    cases = {
        "- slider \"Vol\":\n  - /value: secret4": "secret4",
        "- textbox \"Notes\": |-\n    secret5 line1\n    secret5 line2": "secret5",
        "- 'textbox \"Email: work\" [disabled]': jane@x.com": "jane@x.com",
    }
    for text, secret in cases.items():
        assert secret not in redact_typed_values(text)
