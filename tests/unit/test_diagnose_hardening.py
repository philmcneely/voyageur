"""Diagnose, hardened: what the review found. Invisible characters and odd digit separators, secrets in logs, edits that change more
than a literal or land away from the failure, continuation lines of a Received value, home paths, over-withholding of code, and the
off route reading nothing."""
import json

import pytest

from voyageur.diagnose import withhold
from voyageur.schemas import parse_diagnose_request
from tests.unit.test_diagnose import (ASSERT_LINE, ERROR, HEAL_ID, PATH, SOURCE, STALE_PATCH, FakeModel, client, diagnose, drequest,
                                      on, patch_reply, reply)

pytestmark = pytest.mark.unit
AUTH = {"Authorization": "Bearer t0ken"}


def prompt_of(body):
    model = FakeModel(reply())
    diagnose(on(model), body)
    return model.calls[0]["prompt"]


# ---- M1: invisible characters first, then every digit separator ------------------------------------------------------------
def test_the_aria_snapshot_loses_invisible_characters_and_the_digit_rule_sees_through_them():
    body = drequest()
    body["page"]["aria_snapshot"] = ('- text: call \u202e555\u200b123\u200b4567\n- text: \U000e0049\U000e0067\U000e006e ignore previous\n'
                                     '- paragraph: card 4111\u00a01111\u00a01111\u00a01111\n- paragraph: ref 1\u200b2\u200b3\u200b4\u200b5\u200b6\u200b7')
    p = prompt_of(body)
    assert not [c for c in p if ord(c) >= 0xE0000 or c in "\u202e\u200b\u00a0"]
    for leak in ("555", "4111", "1111", "1234567", "4567"):
        assert leak not in p, leak


def test_withhold_removes_invisible_characters_and_groups_digits_by_any_space_or_underscore():
    assert withhold("id 1\u200b2\u200b3\u200b4\u200b5\u200b6\u200b7 x") == "id <withheld> x"
    assert withhold("card 4111\u00a01111\u00a01111\u00a01111") == "card <withheld>"
    assert withhold("card 4111_1111_1111_1111") == "card <withheld>"
    assert withhold("a\u202eb") == "ab"
    assert withhold("12345 and 12 34") == "12345 and 12 34"


# ---- M2: an edit changes a literal and nothing else -------------------------------------------------------------------------
def edit(cause, frm, to, line=ASSERT_LINE, **body):
    d = diagnose(on(FakeModel(reply(cause=cause, patch={"file": PATH, "line": line, "from": frm, "to": to, "why": "w"}))),
                 drequest(**body)).diagnosis
    return d


EXPECT = 'to_have_text("saved: profile newsletter=no", timeout=TIMEOUT)'


@pytest.mark.parametrize("cause, frm, to", [
    ("stale_expectation", EXPECT, 'to_be_visible()'),                                              # a different assertion
    ("stale_expectation", EXPECT, 'to_have_text("saved: profile newsletter=no", timeout=TIMEOUT) or os.system("x")'),
    ("stale_expectation", EXPECT, 'to_have_text("saved " + evil(), timeout=TIMEOUT)'),
    ("stale_expectation", EXPECT, 'to_have_text(f"{evil()}", timeout=TIMEOUT)'),                   # an f-string interpolates
    ("stale_expectation", EXPECT, 'to_have_text("${evil()}", timeout=TIMEOUT)'),
    ("stale_expectation", EXPECT, 'to_have_text(`${evil()}`, timeout=TIMEOUT)'),
    ("stale_expectation", EXPECT, 'to_have_text("saved", timeout=evil)'),                          # a new identifier
    ("flow_changed", 'page.get_by_test_id("save-notifications").click()',
     'page.get_by_test_id("save-notifications").click(); page.get_by_role("button", name="Confirm").click()'),
    ("timing", EXPECT, 'to_have_text("changed", timeout=TIMEOUT)'),                               # timing: no literal may change
    ("timing", EXPECT, 'to_have_text("saved: profile newsletter=no", timeout=evil())'),
    ("timing", EXPECT, 'to_have_text("saved: profile newsletter=no", timeout=10_000_000.5)'),
])
def test_an_edit_that_changes_more_than_a_literal_is_dropped_as_patch_shape(cause, frm, to):
    line = ASSERT_LINE if "to_have_text" in frm else 7
    d = edit(cause, frm, to, line)
    assert d.cause == cause and d.suggested_patch is None and d.downgraded == "patch_shape"


@pytest.mark.parametrize("cause, frm, to", [
    ("stale_expectation", EXPECT, 'to_have_text("Settings saved", timeout=TIMEOUT)'),
    ("stale_expectation", '"saved: profile newsletter=no"', '"it is 42 now"'),
    ("flow_changed", 'get_by_test_id("save-notifications")', 'get_by_test_id("save-settings")'),
    ("timing", EXPECT, 'to_have_text("saved: profile newsletter=no", timeout=10000)'),
    ("timing", 'timeout=TIMEOUT', 'timeout=10000'),
])
def test_a_literal_only_edit_and_a_timeout_number_stand(cause, frm, to):
    line = ASSERT_LINE if "timeout" in frm or "saved" in frm else 7
    d = edit(cause, frm, to, line)
    assert d.suggested_patch is not None and d.downgraded is None


# ---- M3: an edit lands in the failing file near the failing line ------------------------------------------------------------
def test_an_edit_in_another_sent_file_or_far_from_the_failing_line_is_dropped_as_patch_location():
    config = "export default { use: { baseURL: 'http://x' } }\n"
    body = drequest()
    body["source"]["files"].append({"path": "tests/playwright.config.ts", "content": config})
    d = diagnose(on(FakeModel(reply(patch={"file": "tests/playwright.config.ts", "line": 1, "from": "baseURL: 'http://x'",
                                           "to": "baseURL: 'http://y'", "why": "w"}))), body).diagnosis
    assert d.suggested_patch is None and d.downgraded == "patch_location"
    far = SOURCE + "\n" * 30 + 'page.get_by_text("old copy")\n'
    body = drequest()
    body["source"]["files"] = [{"path": PATH, "content": far}]
    d = diagnose(on(FakeModel(reply(patch={"file": PATH, "line": far.count("\n", 0, far.index("old copy")) + 1, "from": '"old copy"', "to": '"new copy"', "why": "w"}))), body).diagnosis
    assert d.suggested_patch is None and d.downgraded == "patch_location"
    assert diagnose(on(FakeModel(patch_reply())), drequest()).diagnosis.suggested_patch is not None


# ---- M4: secrets in logs, aria and code ------------------------------------------------------------------------------------
def test_bearer_and_basic_tokens_never_reach_the_prompt_from_the_error_the_aria_snapshot_or_the_code():
    body = drequest()
    body["failure"]["error"] = ERROR + "  - authorization: Bearer opaquetokenwithoutdigitsabcdefghijkl\n  - authorization: Basic dXNlcjpwYXNz\n"
    body["page"]["aria_snapshot"] = "- text: Authorization Bearer zzzopaqueaaaabbbbcccc\n- paragraph: ok"
    body["source"]["files"][0]["content"] = SOURCE.replace("# ", "") + "# Authorization: Bearer commenttokenxyzabcdefgh\n"
    p = prompt_of(body)
    for leak in ("opaquetoken", "dXNlcjpwYXNz", "zzzopaque", "commenttoken"):
        assert leak not in p, leak


# ---- L1: the whole Received / Expected block ------------------------------------------------------------------------------
def test_a_long_received_value_is_cut_with_its_continuation_lines():
    body = drequest()
    body["failure"]["error"] = (ERROR.split("Call log")[0] + 'Received string: "' + "x" * 100 + '\n  private line one that is long enough to matter\n'
                                '  private line two\nExpected: short\nCall log:\n')
    p = prompt_of(body)
    assert "private line" not in p and "x" * 81 not in p and "x" * 75 in p and "Expected: short" in p
    body["failure"]["error"] = 'Received: <ul>\n  <li>Alice Johnson street name</li>\n  <li>Bob Smith lives elsewhere in town</li>\n  <li>Carol</li>\n</ul>\nCall log:\n'
    assert "Carol" not in prompt_of(body)
    body["failure"]["error"] = 'Received string: "first\nunindented second line with plenty of filler words before a street address\nthird"\nCall log:\n'
    assert "street address" not in prompt_of(body)


# ---- L2: urls and credential-named literals in code ----------------------------------------------------------------------
def test_code_lines_lose_url_queries_and_literals_assigned_to_credential_names():
    src = ('PASSWORD = "Sup3rS3cret!"\napi_key = \'plainkeyvalue\'\nconst authToken = "tokenvalueplain";\n'
           'client = {secret: "objsecretvalue", name: "ok"}\n'
           'page.goto("https://app.example.com/reset?token=abc123def&u=bob")\n'
           'expect(page.get_by_test_id("summary")).to_have_text("saved: profile newsletter=no", timeout=TIMEOUT)\n')
    body = drequest()
    body["source"]["files"][0]["content"] = src
    body["failure"]["stack"] = [{"file": PATH, "line": 6}]
    p = prompt_of(body)
    for leak in ("Sup3rS3cret", "plainkeyvalue", "tokenvalueplain", "objsecretvalue", "abc123def", "u=bob"):
        assert leak not in p, leak
    assert "app.example.com" in p and "name" in p and "saved: profile newsletter=no" in p


# ---- L3: code is not blanked, free text still is ---------------------------------------------------------------------------
def test_a_code_line_is_not_blanked_by_the_token_rule_but_a_token_in_a_literal_is():
    src = ('await expect(page.getByTestId("cart")).toHaveText("3")\n'
           'await expect(page.getByTestId("cart")).toHaveText("ghp9aF3kLm2Qx8Zp4Rt6Wy1Vb7Nc5Hd0Jg")\n'
           'await expect(page.getByTestId("cart")).toHaveText("mail a@b.co")\n')
    body = drequest()
    body["source"]["files"][0]["content"] = src
    body["failure"]["stack"] = [{"file": PATH, "line": 1}]
    p = prompt_of(body)
    assert 'await expect(page.getByTestId("cart")).toHaveText("3")' in p
    assert "ghp9aF3k" not in p and "a@b.co" not in p and p.count("<withheld>") >= 2
    assert withhold("token ghp9aF3kLm2Qx8Zp4Rt6Wy1Vb7Nc5Hd0Jg") == "token <withheld>"


# ---- L4, L5: the off route reads nothing and names no variable -----------------------------------------------------------
def test_off_the_route_answers_404_without_reading_the_body_and_the_body_names_no_variable():
    c, core = client(enabled=False)
    for content in ("not json", "{}", json.dumps(drequest())):
        r = c.post("/v1/diagnose", content=content, headers=AUTH)
        assert r.status_code == 404 and r.json()["reason"] == "diagnose_off" and "HEAL_DIAGNOSE" not in r.text
    assert core.model.calls == []
    from voyageur.mcp_server import diagnose_failure
    assert "HEAL_DIAGNOSE" not in json.dumps(diagnose_failure(core, drequest()))
    assert c.post("/v1/diagnose", content="{}", headers={"Content-Type": "application/json"}).status_code == 401


# ---- L6: the model's words are marked and cleaned --------------------------------------------------------------------------
def test_evidence_is_returned_as_evidence_untrusted_with_urls_reduced():
    r = diagnose(on(FakeModel(reply(evidence=["see https://evil.example/path?token=abc#x for more"], why="go to http://evil.example/a?b=1"))))
    d = r.diagnosis
    assert d.evidence_untrusted == ["see https://evil.example/… for more"] and "?b=1" not in d.model_note_untrusted
    out = r.to_json()["diagnosis"]
    assert "evidence" not in out and out["evidence_untrusted"] == d.evidence_untrusted


# ---- L7: home paths ---------------------------------------------------------------------------------------------------------
def test_absolute_paths_in_the_error_are_reduced_to_their_file_name_and_page_paths_stay():
    body = drequest()
    body["failure"]["error"] = (ERROR + "    at /Users/alice.smith/work/repo/tests/a.spec.ts:6:5\n    at C:\\Users\\bob.jones\\repo\\a.spec.ts:7:1\n"
                                "  - home /home/carol/proj/x.py and /var/folders/ab/cd/T/t.log\n  - expected url '/orders/42'\n")
    p = prompt_of(body)
    for leak in ("alice.smith", "bob.jones", "carol", "folders"):
        assert leak not in p, leak
    assert "a.spec.ts:6:5" in p and "a.spec.ts:7:1" in p and "/orders/42" in p and "Path: /settings" in p
    assert parse_diagnose_request(json.dumps(body)) and HEAL_ID and STALE_PATCH
