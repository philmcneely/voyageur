"""
===============================================================================
Hashed copy tests (HEAL_FINGERPRINT_COPY=off: the fingerprint holds sha256 of its copy, not the copy)
===============================================================================
A client that records with HEAL_FINGERPRINT_COPY=off sends `sha256:<hex>` of each copy field (case and spacing folded)
instead of the text. The service hashes the copy of the elements on the page the same way, so a recorded hash finds
the element that still says the same thing, and copy proof works with the switch off. A hash that matches nothing on
the page stays a hash: no element says that copy any more, which is what an unmatched hash means.

Author: PMAC
===============================================================================
"""
import hashlib

import pytest

from voyageur.hashed_copy import hash_copy, is_hashed, reveal_all, reveal_copy
from voyageur.proof import _place_sound, prove_with_fingerprint, words
from voyageur.schemas import HealRequest
from tests.unit.scenarios import FakeModel, request, session

pytestmark = pytest.mark.unit


def client_hash(text):
    """The client's formula (playwright-ai-framework utils/heal_fingerprints.hash_copy), restated so a drift shows."""
    return "sha256:" + hashlib.sha256(" ".join(str(text).split()).casefold().encode("utf-8")).hexdigest()


def test_the_service_hashes_copy_exactly_as_the_client_does():
    for text in ("Sign in", "  SIGN\tin \n", "Straße", "Ünïcödé  copy", "x" * 400):
        assert hash_copy(text) == client_hash(text)
    assert is_hashed(client_hash("a")) and not is_hashed("sha256:short") and not is_hashed("Sign in")


def test_a_recorded_hash_is_replaced_by_the_copy_on_the_page_that_hashes_the_same():
    fp = {"tag": "button", "text": client_hash("Sign in"), "aria_label": client_hash("Log in now")}
    page = [{"ref": "e0", "tag": "button", "text": "SIGN  IN"}, {"ref": "e1", "tag": "a", "aria_label": "Log in now"}]
    out = reveal_copy(fp, page)
    assert out["text"] == "SIGN  IN" and out["aria_label"] == "Log in now" and out["tag"] == "button"
    assert fp["text"] == client_hash("Sign in")                     # the request's own dict is not changed


def test_a_hash_no_element_matches_stays_a_hash_and_a_plain_fingerprint_is_untouched():
    fp = {"tag": "button", "text": client_hash("Delete account")}
    assert reveal_copy(fp, [{"ref": "e0", "tag": "button", "text": "Sign in"}])["text"] == client_hash("Delete account")
    plain = {"tag": "button", "text": "Sign in"}
    assert reveal_copy(plain, [{"ref": "e0", "text": "Sign in"}]) is plain
    assert reveal_copy(None, []) is None


def test_ids_test_ids_and_tags_are_never_treated_as_copy():
    fp = {"tag": "button", "id": client_hash("Sign in"), "data_testid": client_hash("Sign in")}
    out = reveal_copy(fp, [{"ref": "e0", "tag": "button", "text": "Sign in"}])
    assert out["id"] == fp["id"] and out["data_testid"] == fp["data_testid"]


def test_copy_proof_works_with_the_copy_switch_off():
    renamed = {"ref": "e0", "tag": "button", "id": "submit-2", "text": "Sign in", "box": {"x": 400, "y": 600, "width": 80, "height": 30}}
    other = {"ref": "e1", "tag": "button", "id": "cancel", "text": "Cancel", "box": {"x": 10, "y": 10, "width": 80, "height": 30}}
    fp = {"tag": "button", "id": "submit", "text": client_hash("Sign in"), "box": {"x": 10, "y": 90, "width": 80, "height": 30}}
    items = [renamed, other]
    assert prove_with_fingerprint(renamed, fp, items) is None             # a hash says nothing until it is matched
    assert prove_with_fingerprint(renamed, reveal_copy(fp, items), items)[0] == "copy"
    assert prove_with_fingerprint(other, reveal_copy(fp, items), items) is None


def test_a_heal_with_hashed_copy_settles_like_one_with_plain_copy():
    plain = {"tag": "input", "role": "textbox", "name": "Username", "label": "Username", "attrs": {"id": "username"},
             "box": {"x": 10, "y": 10, "w": 200, "h": 30}}
    hashed = {**plain, "name": client_hash("Username"), "label": client_hash("Username")}
    runs = []
    for fp in (plain, hashed):
        model = FakeModel()
        r = session(model).start(HealRequest.model_validate(request(last_pass={"fingerprint": fp, "language": "en"})))
        runs.append((r.status, r.settled_at, r.candidate.locator if r.candidate else None, len(model.calls)))
    assert runs[0] == runs[1] and runs[0][0] == "try" and runs[0][3] == 0


def test_an_unmatched_hash_has_no_words_so_place_alone_cannot_pass_on_a_hex_run():
    # "bad", "fed", "decade" are hex runs: as words they could pass the place check the hash should fail
    fp = {"tag": "button", "text": "sha256:" + "decade" + "bad0fed1" * 7 + "ab", "box": {"x": 10, "y": 90, "width": 80, "height": 30}}
    assert is_hashed(fp["text"]) and words(fp["text"]) == set()
    here = {"ref": "e0", "tag": "button", "text": "Decade bad fed", "box": fp["box"]}
    assert _place_sound(here, fp) is False


def test_a_hash_two_spellings_fold_to_stays_unmatched():
    fp = {"tag": "button", "text": client_hash("Save")}
    page = [{"ref": "e0", "tag": "button", "text": "Save"}, {"ref": "e1", "tag": "button", "text": "SAVE"}]
    assert reveal_copy(fp, page)["text"] == fp["text"]
    assert reveal_copy(fp, page[:1])["text"] == "Save"


def test_the_page_copy_is_hashed_once_for_the_fingerprint_and_every_neighbour(monkeypatch):
    import voyageur.hashed_copy as hc
    calls = []
    real = hc.hash_copy
    monkeypatch.setattr(hc, "hash_copy", lambda t: calls.append(t) or real(t))
    page = [{"ref": f"e{i}", "tag": "button", "text": f"Item {i % 50}", "title": "Same title"} for i in range(500)]
    neighbours = [{"tag": "button", "text": real(f"Item {i}")} for i in range(100)]
    fp, ns = reveal_all({"tag": "button", "text": real("Item 7")}, neighbours, page)
    assert fp["text"] == "Item 7" and ns[3]["text"] == "Item 3"
    assert len(calls) == 51                                          # 50 distinct texts and one distinct title
