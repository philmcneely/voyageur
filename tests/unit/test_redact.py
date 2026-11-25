"""Unit: credentials and URLs are removed from any error text before it is recorded (cases lifted from the original
healer tests, rewritten against redact.py)."""
import time

import pytest

from voyageur import redact
from voyageur.redact import redact_urls, register_secret, scrub_secrets

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_registered_secrets():
    redact._SECRETS.clear()
    yield
    redact._SECRETS.clear()


@pytest.mark.parametrize("raw,host,secrets", [("https://user:p@ss@proxy/v1", "https://proxy/", ["user", "p@ss", "ss@"]),
                                               ("'https://u:pw@proxy:8/x?t=1'", "https://proxy:8/", ["u:pw", "t=1", "/x"]),
                                               ("wss://a:b@h/p#f", "wss://h/", ["a:b", "#f", "/p"]),
                                               ("https://u:p@@host/p", "https://host/", ["u:p", "/p"]),
                                               ("https://h:pw@x@y/z", "https://y/", ["pw", "x@", "/z"])])
def test_redact_urls_drops_all_userinfo(raw, host, secrets):
    out = redact_urls(raw)
    assert host in out and "@" not in out and not [s for s in secrets if s in out]


@pytest.mark.parametrize("hostile", ["a" * 20000, "a+" * 10000, "a.-" * 7000, "x://" * 5000 + "@" * 5000])
def test_redact_urls_is_fast_on_hostile_text(hostile):
    t0 = time.monotonic()
    redact_urls(hostile)
    assert time.monotonic() - t0 < 0.5


def test_redact_urls_keeps_closing_delimiters():
    assert redact_urls("(https://h/v1?key=sk-1), next") == "(https://h/…), next"
    assert redact_urls("<https://u:p@h/x>") == "<https://h/…>"


def test_scrub_removes_bearer_tokens_and_the_key():
    text = "Illegal header value b'Bearer sk-SECRETKEY123\\n' for url 'https://u:p@h/v1?t=1'"
    out = scrub_secrets(text, "sk-SECRETKEY123")
    assert "SECRETKEY" not in out and "u:p" not in out and "(withheld)" in out


@pytest.mark.parametrize("key", ['sk-AA"TAILSECRET1\x00', "sk-AA'TAILSECRET2\x00", "sk-AA TAILSECRET3\x00",
                                 "sk-AAAA\r\nX-Evil: TAILSECRET4", "Bearer sk-TAILSECRET5\x00", "Basic dXNlcjpwYXNz\x00"])
def test_scrub_withholds_every_form_of_a_bad_key(key):
    text = "LocalProtocolError: Illegal header value " + repr(("Bearer " + key).encode())
    out = scrub_secrets(text, key)
    assert "TAILSECRET" not in out and "dXNlcjpwYXNz" not in out and "sk-AA" not in out


def test_registered_secrets_are_scrubbed_without_being_passed():
    register_secret("sk-REGISTERED-123")
    assert "REGISTERED" not in scrub_secrets("boom: key=sk-REGISTERED-123 in text")


def test_registered_secrets_are_bounded_and_the_newest_survive():
    for i in range(redact.MAX_REGISTERED_SECRETS + 25):
        register_secret(f"secret-value-{i:04d}")
    assert len(redact._SECRETS) == redact.MAX_REGISTERED_SECRETS
    assert "secret-value-0000" not in redact._SECRETS
    newest = f"secret-value-{redact.MAX_REGISTERED_SECRETS + 24:04d}"
    assert "…" in scrub_secrets(f"boom {newest}") and newest not in scrub_secrets(f"boom {newest}")


def test_short_strings_are_not_secrets_and_empty_ones_are_ignored():
    register_secret("ab")
    register_secret(None)
    assert scrub_secrets("a b ab", "ab", "") == "a b ab"


def test_the_input_is_capped_before_it_is_scanned():
    assert len(scrub_secrets("x" * 50000)) <= redact.SCRUB_INPUT_CHARS


# ---- typed values in an accessibility snapshot --------------------------------------------------------------------
@pytest.mark.parametrize("role", ["textbox", "searchbox", "spinbutton", "slider", "combobox"])
def test_redact_typed_values_drops_the_value_for_each_input_role(role):
    from voyageur.redact import redact_typed_values
    assert redact_typed_values(f'- {role} "Field": typed') == f'- {role} "Field"'


def test_redact_typed_values_keeps_states_prose_and_other_roles():
    from voyageur.redact import redact_typed_values
    text = '- textbox "Password" [disabled] [focused]: hunter2\n- heading "Note: x"\n- paragraph: Hello: world\n- button "Go"'
    assert redact_typed_values(text).splitlines() == [
        '- textbox "Password" [disabled] [focused]', '- heading "Note: x"', "- paragraph: Hello: world", '- button "Go"']
