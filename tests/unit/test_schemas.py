"""Request / response schemas: the spec's shapes, strict, with hard limits (413) and refusals (422)."""
import json

import pytest
from pydantic import ValidationError

from voyageur.schemas import (LIMITS, HealRequest, LimitExceeded, RefusedRequest, ResultRequest, check_limits,
                                  parse_request, refuse_secrets, strip_aria_values)

pytestmark = pytest.mark.unit


def base():
    return {
        "client": {"framework": "playwright-ai-framework", "language": "python", "version": "2.1.0"},
        "test": {"id": "tests/test_login.py::test_valid_login", "title": "valid login"},
        "failure": {"error": "Timeout 30000ms exceeded. waiting for locator('#username')",
                    "stack": [{"file": "pages/login_page.py", "line": 18}, {"file": "tests/test_login.py", "line": 9}]},
        "source": {"roots": ["pages", "tests"],
                   "files": [{"path": "pages/login_page.py", "content": "class LoginPage: ..."}]},
        "page": {"url": "https://app.example/login?token=abc",
                 "language": {"html_lang": "fr-CA", "browser_locale": "en-US", "content_language": None},
                 "aria_snapshot": '- textbox "Nom d\'utilisateur"',
                 "inventory": [{"ref": "e7", "tag": "input", "role": "textbox", "name": "Nom d'utilisateur",
                                "attrs": {"data-testid": "login-user", "id": "user", "name": "user", "type": "text",
                                          "data-row-kind": "x"},
                                "label": "Nom d'utilisateur", "placeholder": None, "text": None,
                                "box": {"x": 412, "y": 220, "w": 280, "h": 36}, "visible": True, "enabled": True,
                                "frame": None, "shadow_host": None}],
                 "viewport": {"w": 1280, "h": 720, "dpr": 1}, "screenshot": None},
        "last_pass": {"fingerprint": {"role": "textbox", "name": "Username", "attrs": {"id": "username"},
                                      "box": {"x": 410, "y": 218, "w": 280, "h": 36}}, "language": "en"},
        "options": {"vision": "auto", "max_rounds": 3, "locale_branching": True},
    }


def test_the_spec_example_parses_and_defaults_the_test_id_attribute():
    req = parse_request(json.dumps(base()).encode())
    assert req.test_id_attribute == "data-testid" and req.client.language == "python"
    item = req.page.inventory[0].flat(0)
    assert item["testid"] == "login-user" and item["id"] == "user" and item["name_attr"] == "user"
    assert item["data_row_kind"] == "x" and item["box"] == {"x": 412, "y": 220, "width": 280, "height": 36}
    assert item["ref"] == "e7" and "placeholder" not in item and not item.get("hidden")
    assert req.page.path == "/login"                      # the query string can carry a token and is never kept
    assert req.last_pass.fingerprint.flat()["id"] == "username"


def test_probe_items_are_accepted_as_they_come_and_get_refs_by_position():
    raw = base()
    raw["page"]["inventory"] = [{"tag": "button", "type": "", "role": "", "name": "Go", "aria_label": "", "label": "",
                                 "placeholder": "", "id": "go", "testid": "", "data_test": "", "data_qa": "q1",
                                 "data_cy": "", "class": "btn", "name_attr": "", "title": "", "alt": "", "text": "Go",
                                 "disabled": False, "hidden": False, "box": {"x": 1, "y": 2, "width": 3, "height": 4},
                                 "data_col": "customer", "row": "r1"}]
    req = HealRequest.model_validate(raw)
    flat = req.page.inventory[0].flat(5)
    assert flat["ref"] == "e5" and flat["data_qa"] == "q1" and flat["class"] == "btn" and flat["data_col"] == "customer"


def test_hidden_and_disabled_come_from_visible_and_enabled():
    raw = base()
    raw["page"]["inventory"][0].update(visible=False, enabled=False)
    flat = HealRequest.model_validate(raw).page.inventory[0].flat(0)
    assert flat["hidden"] is True and flat["disabled"] is True


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(surprise=1),
    lambda r: r["client"].update(extra=1),
    lambda r: r["client"].update(language="ruby"),
    lambda r: r["options"].update(vision="max"),
    lambda r: r["options"].update(max_rounds=0),
    lambda r: r["page"]["inventory"][0].update(onclick="x"),
    lambda r: r["page"]["inventory"][0].update(ref="x1"),
    lambda r: r["page"].update(screenshot={"png_base64": "AAAA"}),
    lambda r: r.update(test_id_attribute="data testid"),
    lambda r: r["source"]["files"][0].update(path="../etc/passwd"),
    lambda r: r["source"]["files"][0].update(path="/abs/file.py"),
    lambda r: r["source"]["files"][0].update(path="other/file.py"),
    lambda r: r["source"]["files"][0].update(sha256="0" * 64),
])
def test_unknown_or_malformed_input_is_a_validation_error(mutate):
    raw = base()
    mutate(raw)
    with pytest.raises(ValidationError):
        HealRequest.model_validate(raw)


def test_a_matching_sha256_is_accepted():
    import hashlib
    raw = base()
    raw["source"]["files"][0]["sha256"] = hashlib.sha256(b"class LoginPage: ...").hexdigest()
    assert HealRequest.model_validate(raw).source.files[0].digest == raw["source"]["files"][0]["sha256"]


# ---- limits: 413, never a silent truncation -------------------------------------------------------------------------
def test_limits_are_the_spec_numbers():
    assert (LIMITS["files"], LIMITS["file_bytes"], LIMITS["inventory"], LIMITS["request_bytes"]) == \
        (20, 256 * 1024, 500, 10 * 1024 * 1024)


def test_too_many_files_is_a_limit_error():
    raw = base()
    raw["source"]["files"] = [{"path": f"pages/f{i}.py", "content": "x"} for i in range(21)]
    with pytest.raises(LimitExceeded, match="files"):
        check_limits(raw)


def test_a_file_over_256_kb_is_a_limit_error():
    raw = base()
    raw["source"]["files"][0]["content"] = "x" * (256 * 1024 + 1)
    with pytest.raises(LimitExceeded, match="256"):
        check_limits(raw)
    raw["source"]["files"][0]["content"] = "é" * (128 * 1024 + 1)          # bytes count, not characters
    with pytest.raises(LimitExceeded):
        check_limits(raw)


def test_more_than_500_inventory_entries_is_a_limit_error():
    raw = base()
    raw["page"]["inventory"] = [{"tag": "a"}] * 501
    with pytest.raises(LimitExceeded, match="inventory"):
        check_limits(raw)
    raw["page"]["inventory"] = [{"tag": "a"}] * 500
    check_limits(raw)


def test_an_inventory_entry_may_say_it_is_copy_only_and_carry_a_labels_for():
    from voyageur.schemas import InventoryItem
    flat = InventoryItem.model_validate({"tag": "label", "for_attr": "birthdate", "text": "Birth date", "copy_only": True}).flat(3)
    assert flat["for_attr"] == "birthdate" and flat["copy_only"] is True and flat["ref"] == "e3"
    assert "copy_only" not in InventoryItem.model_validate({"tag": "a"}).flat(0)
    with pytest.raises(ValidationError):
        InventoryItem.model_validate({"tag": "a", "copy_only": "yes please"})
    with pytest.raises(ValidationError):
        InventoryItem.model_validate({"tag": "a", "for_attr": "x" * 401})


def test_a_page_may_say_how_many_copy_holding_elements_the_probe_left_out():
    from voyageur.schemas import InventoryItem
    raw = base()
    raw["page"]["inventory_copy_more"] = 7
    assert HealRequest.model_validate(raw).page.inventory_copy_more == 7
    assert HealRequest.model_validate(base()).page.inventory_copy_more == 0
    for bad in (-1, 10**6 + 1):
        raw["page"]["inventory_copy_more"] = bad
        with pytest.raises(ValidationError):
            HealRequest.model_validate(raw)
    raw["page"]["inventory_copy_more"] = 10**6
    HealRequest.model_validate(raw)
    assert InventoryItem.model_validate({"tag": "p", "copy_only": True, "copy_cut": True}).flat(0)["copy_cut"] is True
    assert InventoryItem.model_validate({"tag": "div", "copy_wrap": True}).flat(0)["copy_wrap"] is True


def test_a_request_over_10_mb_is_a_limit_error_before_any_parsing():
    with pytest.raises(LimitExceeded, match="10"):
        parse_request(b" " * (10 * 1024 * 1024 + 1))


def test_an_oversized_aria_snapshot_is_a_limit_error():
    raw = base()
    raw["page"]["aria_snapshot"] = "- a\n" * (256 * 1024)
    with pytest.raises(LimitExceeded, match="aria"):
        check_limits(raw)


def test_a_body_that_is_not_json_or_not_an_object_is_refused():
    with pytest.raises(RefusedRequest):
        parse_request(b"{nope")
    with pytest.raises(RefusedRequest):
        parse_request(b"[1]")


# ---- refusals: values, cookies, storage ----------------------------------------------------------------------------
@pytest.mark.parametrize("where", [
    lambda r: r["page"]["inventory"][0].update(value="hunter2"),
    lambda r: r["page"]["inventory"][0]["attrs"].update(value="hunter2"),
    lambda r: r["page"].update(cookies=[{"name": "sid"}]),
    lambda r: r.update(storage_state={"cookies": []}),
    lambda r: r["page"].update(localStorage={"a": "b"}),
    lambda r: r["last_pass"]["fingerprint"].update(value="x"),
    lambda r: r["page"]["inventory"][0].update(nested={"deep": [{"Value": "x"}]}),
])
def test_any_value_cookie_or_storage_key_is_refused_wherever_it_sits(where):
    raw = base()
    where(raw)
    with pytest.raises(RefusedRequest):
        refuse_secrets(raw)
    with pytest.raises(RefusedRequest):
        parse_request(json.dumps(raw).encode())


def test_a_data_value_attribute_is_not_a_value_key_and_value_text_in_content_is_fine():
    raw = base()
    raw["page"]["inventory"][0]["attrs"]["data-value"] = "7"
    raw["source"]["files"][0]["content"] = 'x = {"value": 1}  # a source file may say value'
    refuse_secrets(raw)
    parse_request(json.dumps(raw).encode())


def test_refusal_never_echoes_the_secret():
    raw = base()
    raw["page"]["inventory"][0]["value"] = "hunter2-secret"
    with pytest.raises(RefusedRequest) as e:
        refuse_secrets(raw)
    assert "hunter2" not in str(e.value) and "value" in str(e.value)


def test_a_deeply_nested_body_does_not_crash_the_walk():
    nest = {}
    cur = nest
    for _ in range(5000):
        cur["a"] = {}
        cur = cur["a"]
    refuse_secrets(nest)


# ---- aria values ---------------------------------------------------------------------------------------------------
def test_the_value_after_the_colon_is_stripped_from_input_lines_only():
    # Everything nested under a textbox is typed text and goes, so the nested combobox sits under a group here.
    text = ('- textbox "Email": jane@example.com\n- group:\n  - combobox "Country": France\n- spinbutton "Qty": 3\n'
            '- searchbox "Find": cats\n- heading "Note: read me" [level=1]\n- button "Save"\n- list:\n  - listitem: Alpha\n'
            '- textbox "Empty"')
    out = strip_aria_values(text).splitlines()
    assert out[0] == '- textbox "Email"' and out[2] == '  - combobox "Country"' and out[3] == '- spinbutton "Qty"'
    assert out[4] == '- searchbox "Find"' and out[5] == '- heading "Note: read me" [level=1]'
    assert out[6] == '- button "Save"' and out[7] == "- list:" and out[8] == "  - listitem: Alpha"
    assert out[9] == '- textbox "Empty"'


def test_the_request_model_strips_values_from_the_aria_snapshot():
    raw = base()
    raw["page"]["aria_snapshot"] = '- textbox "Email": jane@example.com'
    assert HealRequest.model_validate(raw).page.aria_snapshot == '- textbox "Email"'


# ---- result --------------------------------------------------------------------------------------------------------
def test_result_shape():
    r = ResultRequest.model_validate({"count": 1, "hit": {"ref": "e7"}, "test_outcome": "passed", "error": None})
    assert r.hit.ref == "e7"
    for bad in ({"count": -1}, {"hit": {"ref": "7"}}, {"test_outcome": "maybe"}, {"extra": 1}, {"error": "x" * 501}):
        with pytest.raises(ValidationError):
            ResultRequest.model_validate(bad)


@pytest.mark.parametrize("role", ["textbox", "searchbox", "spinbutton", "slider", "combobox"])
def test_typed_text_is_stripped_for_each_input_role(role):
    assert strip_aria_values(f'- {role} "Field": typed text') == f'- {role} "Field"'
    assert strip_aria_values(f"- {role}: typed text") == f"- {role}"


def test_a_password_field_loses_its_typed_text_too():
    assert strip_aria_values('- textbox "Password": hunter2') == '- textbox "Password"'


def test_states_survive_the_strip():
    out = strip_aria_values('- textbox "Email" [disabled]: me@x.io\n- checkbox "Ok" [checked]\n'
                            '- slider "Vol" [active] [focused]: 7')
    assert out.splitlines() == ['- textbox "Email" [disabled]', '- checkbox "Ok" [checked]',
                                '- slider "Vol" [active] [focused]']


def test_typed_text_never_reaches_the_request_model_for_any_role():
    raw = base()
    raw["page"]["aria_snapshot"] = '- slider "V": 7\n- combobox "C": secret-choice'
    assert "7" not in HealRequest.model_validate(raw).page.aria_snapshot.replace('"V"', "")
    assert "secret-choice" not in HealRequest.model_validate(raw).page.aria_snapshot


def test_duplicate_refs_are_refused():
    raw = base()
    raw["page"]["inventory"] = [{"ref": "e1", "tag": "a"}, {"ref": "e1", "tag": "b"}]
    with pytest.raises(ValidationError):
        HealRequest.model_validate(raw)


def test_the_probe_counts_of_withheld_copy_are_bounded_and_the_flags_are_real_booleans():
    from voyageur.schemas import InventoryItem
    raw = base()
    for key in ("inventory_copy_withheld", "inventory_copy_withheld_data"):
        assert HealRequest.model_validate(raw).page.model_dump()[key] is None
        raw["page"][key] = 10**6
        assert HealRequest.model_validate(raw).page.model_dump()[key] == 10**6
        for bad in (-1, 10**6 + 1):
            raw["page"][key] = bad
            with pytest.raises(ValidationError):
                HealRequest.model_validate(raw)
        raw["page"][key] = 0
    for flag in ("copy_wrap", "hidden", "copy_only", "copy_cut", "pw_cut", "top_at_center", "disabled"):
        for bad in ("yes", 1, "true"):
            with pytest.raises(ValidationError):
                InventoryItem.model_validate({"tag": "a", flag: bad})
        assert InventoryItem.model_validate({"tag": "a", flag: True}) is not None


def test_withheld_counts_a_client_did_not_send_are_none_not_zero():
    page = HealRequest.model_validate(base()).page
    assert page.inventory_copy_withheld is None and page.inventory_copy_withheld_data is None
    raw = base()
    raw["page"]["inventory_copy_withheld"] = 0
    assert HealRequest.model_validate(raw).page.inventory_copy_withheld == 0


def test_a_later_failure_is_only_taken_with_failed_later_and_is_bounded():
    r = ResultRequest.model_validate({"count": 1, "hit": {"ref": "e7"}, "test_outcome": "failed_later",
                                      "later": {"error": "x", "file": "a.py", "line": 3}})
    assert r.later.line == 3
    for bad in ({"test_outcome": "failed_later"}, {"test_outcome": "failed", "later": {"error": "x"}},
                {"test_outcome": "failed_later", "later": {"error": "x" * 4001}},
                {"test_outcome": "failed_later", "later": {"error": "x", "line": 0}},
                {"test_outcome": "failed_later", "later": {"error": "x", "file": "a" * 501}},
                {"test_outcome": "failed_later", "later": {"error": "x", "extra": 1}}):
        with pytest.raises(ValidationError):
            ResultRequest.model_validate(bad)
