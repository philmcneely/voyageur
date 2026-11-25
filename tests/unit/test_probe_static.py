"""Unit: static checks on probe.js (no browser): it never reads what a person typed, and it ships with the package."""
import re
from importlib import resources
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROBE = Path(__file__).resolve().parents[2] / "src" / "voyageur" / "probe" / "probe.js"


def code_of(source: str) -> str:
    """The JS without its comments (block and line), so a word in the prose is not read as code."""
    no_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(re.sub(r"(?<![:'\"\\])//.*$", "", line) for line in no_block.splitlines())


def test_the_probe_is_part_of_the_package_data():
    text = resources.files("voyageur").joinpath("probe/probe.js").read_text(encoding="utf-8")
    assert text == PROBE.read_text(encoding="utf-8") and text.rstrip().endswith("}")
    pyproject = (PROBE.parents[3] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"probe/*.js"' in pyproject and 'voyageur = [' in pyproject


def test_the_probe_reads_a_value_only_for_button_submit_and_reset_inputs():
    code = code_of(PROBE.read_text(encoding="utf-8"))
    uses = [m.start() for m in re.finditer(r"\.value\b", code)]
    assert len(uses) == 1, "the only read of an element value is a button-like input's label"
    before = code[:uses[0]]
    guard = before.rfind("button|submit|reset")
    assert guard != -1 and "return ''" in before[guard:], "the value read must come after a button/submit/reset guard"
    line = code[code.rfind("\n", 0, uses[0]) + 1:code.index("\n", uses[0])]
    assert "input" in line.lower()


def test_the_probe_never_takes_the_whole_text_of_an_element_by_textcontent_or_innertext():
    # textContent of a textarea is its typed content; the probe's own `copy` walk leaves form controls out instead
    code = code_of(PROBE.read_text(encoding="utf-8"))
    assert "textContent" not in code and "innerText" not in code and "innerHTML" not in code and "outerHTML" not in code
    assert "checked" not in code and "selectedOptions" not in code and "selectedIndex" not in code


def test_the_probe_drops_credential_named_data_attributes_and_strips_control_characters():
    code = code_of(PROBE.read_text(encoding="utf-8"))
    assert "token|csrf|auth|session|secret|email|password" in code
    assert "\\u0000-\\u001f" in code and "\\u2028" in code
    assert "document.cookie" not in code and "localStorage" not in code and "sessionStorage" not in code
    assert "location.search" not in code and "location.hash" not in code and "location.href" not in code


def test_the_probe_gives_no_ref_and_keeps_the_only_mode_and_the_cap():
    code = code_of(PROBE.read_text(encoding="utf-8"))
    assert not re.search(r"\bref\b", code)
    assert "only instanceof Element" in code and "const MAX = 500" in code and "more++" in code
    assert "return {items, more, copy_more: copyMore, copy_withheld: withheld, copy_withheld_data: withheldData, page}" in code
