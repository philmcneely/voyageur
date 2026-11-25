"""Deadline: CPU-heavy stages run in a killable worker; a runaway regex that holds the GIL is stopped on time."""
import os
import re
import time

import pytest

from voyageur import stages
from voyageur.config import ServiceSettings
from voyageur.core import HealSession
from voyageur.deadline import Budget, DeadlineExceeded, InlineRunner, ProcessRunner, WorkerFailed
from voyageur.schemas import HealRequest, ResultRequest
from voyageur.store import Store
from tests.unit.scenarios import ENDPOINT, FakeModel, request

pytestmark = pytest.mark.unit

BOMB = ("(a+)+$", "a" * 60 + "b")          # Python's re backtracks on this for hours, holding the GIL


def spin_analyze(*args):
    """A stage that never returns: Python `re` on a catastrophic pattern (module level: a worker imports it by name)."""
    return re.match(*BOMB)


@pytest.fixture(scope="module")
def pool():
    runner = ProcessRunner(2)
    runner.warm()
    yield runner
    runner.close()


def test_a_runaway_regex_holding_the_gil_is_killed_on_time(pool):
    started = time.monotonic()
    with pytest.raises(DeadlineExceeded):
        pool.run(re.match, *BOMB, timeout=0.5)
    assert time.monotonic() - started < 3.0


def test_the_pool_still_works_after_a_kill_and_keeps_its_size(pool):
    for _ in range(3):                                           # more kills than workers
        with pytest.raises(DeadlineExceeded):
            pool.run(re.match, *BOMB, timeout=0.2)
    started = time.monotonic()
    assert pool.run(sum, [1, 2, 3], timeout=10) == 6
    assert pool.run(sum, [4], timeout=10) == 4
    assert time.monotonic() - started < 10


def test_an_exception_in_the_worker_is_raised_in_the_parent(pool):
    with pytest.raises(ValueError):
        pool.run(int, "not a number", timeout=10)


def test_a_worker_that_dies_is_a_worker_failure_not_a_hang(pool):
    started = time.monotonic()
    with pytest.raises(WorkerFailed):
        pool.run(os._exit, 1, timeout=10)
    assert time.monotonic() - started < 5.0
    assert pool.run(sum, [1], timeout=10) == 1


def test_the_budget_is_shared_by_every_stage_of_a_request(pool):
    budget = Budget(pool, 0.6)
    started = time.monotonic()
    budget.run(time.sleep, 0.4)
    with pytest.raises(DeadlineExceeded):
        budget.run(time.sleep, 0.4)
    with pytest.raises(DeadlineExceeded):                         # nothing left: not even started
        budget.run(sum, [1])
    assert time.monotonic() - started < 4.0


def test_inline_runner_keeps_the_in_process_path_for_tests():
    assert InlineRunner().run(sum, [1, 2]) == 3
    assert Budget(InlineRunner(), None).run(sum, [1, 2]) == 3


# ---- the session --------------------------------------------------------------------------------------------------
def make(runner, model=None, deadline=10.0):
    settings = ServiceSettings(request_deadline_s=deadline)
    return HealSession(settings, Store(":memory:", 14), model if model is not None else FakeModel(), ENDPOINT,
                       runner=runner)


def req():
    return HealRequest.model_validate(request())


def test_a_stage_that_runs_out_of_time_ends_the_heal_needs_human_deadline_exceeded(pool, monkeypatch):
    monkeypatch.setattr(stages, "analyze", spin_analyze)
    model = FakeModel('{"ref": "e0", "why": "x"}')
    s = make(pool, model, deadline=0.5)
    started = time.monotonic()
    r = s.start(req())
    assert time.monotonic() - started < 4.0
    assert r.status == "needs_human" and r.reason == "deadline_exceeded" and r.ended_at == 1
    assert model.calls == []
    rec = s.get(r.heal_id)
    assert rec["outcome"] == "needs_human" and rec["reason"] == "deadline_exceeded"


def test_a_stage_that_fails_in_a_later_round_also_ends_the_heal(pool, monkeypatch):
    s = make(pool, FakeModel('{"ref": "e0", "why": "x"}'), deadline=5.0)
    first = s.start(req())
    assert first.status == "try"
    monkeypatch.setattr(stages, "patches_for", spin_analyze)
    s.settings = ServiceSettings(request_deadline_s=0.5)
    done = s.result(first.heal_id, ResultRequest.model_validate({"count": 2, "test_outcome": "failed"}))
    assert done.status == "needs_human" and done.reason == "deadline_exceeded"


def test_the_model_call_stays_in_the_parent_and_is_not_counted_against_the_deadline(pool):
    class SlowModel(FakeModel):
        def call(self, ep, prompt, images=(), heal_id=""):
            time.sleep(1.0)                                       # longer than the deadline below
            return super().call(ep, prompt, images, heal_id)
    s = make(pool, SlowModel('{"ref": "e0", "why": "x"}'), deadline=0.9)
    r = s.start(req())
    assert r.status == "try" and r.usage.calls == 1


def test_a_normal_heal_gives_the_same_answer_through_workers_as_in_process(pool):
    inline = make(InlineRunner(), FakeModel('{"ref": "e0", "why": "x"}')).start(req())
    pooled = make(pool, FakeModel('{"ref": "e0", "why": "x"}')).start(req())
    assert pooled.status == inline.status == "try"
    assert pooled.candidate.code == inline.candidate.code
    assert pooled.candidate.patch.diff == inline.candidate.patch.diff


def test_overhead_of_the_worker_path_is_small_and_reported(pool):
    body = req()
    n = 20
    t0 = time.monotonic()
    for _ in range(n):
        make(InlineRunner(), FakeModel('{"ref": "e0", "why": "x"}')).start(body)
    inline = (time.monotonic() - t0) / n
    t0 = time.monotonic()
    for _ in range(n):
        make(pool, FakeModel('{"ref": "e0", "why": "x"}')).start(body)
    pooled = (time.monotonic() - t0) / n
    print(f"\nheal start: in-process {inline * 1000:.1f} ms, worker pool {pooled * 1000:.1f} ms, "
          f"overhead {(pooled - inline) * 1000:.1f} ms")
    assert pooled - inline < 0.25


def test_a_spawned_worker_is_ready_before_it_is_handed_out():
    runner = ProcessRunner(1)
    try:
        runner.warm()                                   # returns only once the worker has imported every stage
        started = time.monotonic()
        assert runner.run(sum, [1, 2], timeout=5) == 3
        assert time.monotonic() - started < 0.15        # a ready worker answers at once: no import on the clock
    finally:
        runner.close()
