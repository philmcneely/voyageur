"""
===============================================================================
Deadline (run client-text work where it can be stopped)
===============================================================================
Input caps (bounds.py) make each scan small. They cannot promise that no scan is ever slow: a pattern or a shape nobody
thought of can still hold the interpreter. Python cannot interrupt a thread inside a regex or a C call, and a thread
that holds the GIL stops the whole service. So the CPU-heavy part of a heal (triage, matching, writing, patching) is run
in a separate process that the parent can kill when the request's wall-clock time is spent.

    InlineRunner    runs the function in this process, no limit. Tests and HEAL_WORKERS=0.
    ProcessRunner   a small pool of warm worker processes; a call that outlives its timeout has its worker killed and
                    replaced, and raises DeadlineExceeded. A worker that dies (out of memory, a crash) raises WorkerFailed.
    Budget          the seconds one request may spend in workers in all; the model call is not counted, it stays in the
                    parent.

A function run by a runner must be a module-level function (pickled by reference) with picklable arguments and result.

Features:
    ✓ DeadlineExceeded (reason `deadline_exceeded`) and WorkerFailed (reason `worker_failed`), both StageFailed
    ✓ Workers are spawned (never forked from a threaded server), warmed once, address space limited where the OS allows
    ✓ A killed worker is replaced in the background; the pool never shrinks
    ✓ Budget.run(): one call that spends from the request's budget

Environment Variables:
    None here (HEAL_REQUEST_DEADLINE_S and HEAL_WORKERS come in through config.ServiceSettings).

Author: PMAC
===============================================================================
"""
import logging
import multiprocessing
import queue
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger("voyageur")


class StageFailed(Exception):
    """A stage did not produce an answer. `reason` is the needs_human reason code."""
    reason = "worker_failed"


class DeadlineExceeded(StageFailed):
    reason = "deadline_exceeded"


class WorkerFailed(StageFailed):
    reason = "worker_failed"


class InlineRunner:
    """Runs a function in this process. There is no way to stop it: use it where the input is trusted or tested."""

    def run(self, fn: Callable[..., Any], *args: Any, timeout: Optional[float] = None) -> Any:
        return fn(*args)

    def close(self) -> None:
        pass


def _limit_memory(mb: int) -> None:
    try:                                                # a hard ceiling where the OS enforces one (Linux)
        import resource
        limit = mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, ValueError, OSError):
        pass


def _worker_main(conn: Any, mem_mb: int) -> None:
    """The worker loop: take (fn, args), send ("ok", result) or ("err", exception)."""
    import voyageur.stages  # noqa: F401   (warm: every stage imported before the first request)
    if mem_mb:
        _limit_memory(mem_mb)
    conn.send(("ready", None))                          # the pool hands this worker out only after this
    while True:
        try:
            fn, args = conn.recv()
        except (EOFError, OSError):
            return
        try:
            out = ("ok", fn(*args))
        except MemoryError:
            out = ("err", WorkerFailed("the worker ran out of memory"))
        except BaseException as exc:                    # noqa: BLE001 - everything goes back to the caller
            out = ("err", exc)
        try:
            conn.send(out)
        except Exception as exc:                        # an unpicklable result or exception
            conn.send(("err", WorkerFailed(f"the worker's answer could not be sent ({type(exc).__name__})")))


READY_TIMEOUT_S = 30.0          # a worker that has not imported the stages by then is treated as dead


class _Worker:
    def __init__(self, ctx: Any, mem_mb: int):
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=_worker_main, args=(child, mem_mb), daemon=True)
        self.proc.start()
        child.close()
        # Wait for the worker's imports here, not on a request's clock: a worker handed out while still importing
        # spends that request's deadline on start-up.
        try:
            ready = self.conn.poll(READY_TIMEOUT_S) and self.conn.recv() == ("ready", None)
        except (EOFError, OSError):
            ready = False
        if not ready:
            self.kill()
            raise WorkerFailed("a worker did not start")

    def kill(self) -> None:
        try:
            self.proc.kill()
            self.proc.join(2)
        except Exception:                               # noqa: BLE001
            pass
        try:
            self.conn.close()
        except Exception:                               # noqa: BLE001
            pass


class ProcessRunner:
    """A pool of `size` worker processes. Safe to call from several threads; a call waits for a free worker."""

    def __init__(self, size: int = 2, mem_mb: int = 1024):
        self.size, self.mem_mb = max(1, size), mem_mb
        self._ctx = multiprocessing.get_context("spawn")
        self._idle: "queue.Queue[Optional[_Worker]]" = queue.Queue()
        for _ in range(self.size):
            self._idle.put(None)                        # a slot; its worker is started on first use (or by warm())
        self._closed = False

    def warm(self) -> None:
        """Start every worker now, so the first request does not pay for it."""
        taken = [self._idle.get() for _ in range(self.size)]
        started = [w or self._spawn() for w in taken]
        for w in started:
            self._idle.put(w)

    def _spawn(self) -> _Worker:
        return _Worker(self._ctx, self.mem_mb)

    def _replace_later(self) -> None:
        def go() -> None:
            try:
                self._idle.put(self._spawn() if not self._closed else None)
            except Exception:                           # noqa: BLE001 - the slot stays usable: spawned on demand
                self._idle.put(None)
        threading.Thread(target=go, daemon=True).start()

    def run(self, fn: Callable[..., Any], *args: Any, timeout: Optional[float] = None) -> Any:
        """fn(*args) in a worker; DeadlineExceeded after `timeout` seconds (the worker is killed), WorkerFailed when the
        worker dies. An exception the function raises is raised here."""
        worker = self._idle.get()
        try:
            if worker is None or not worker.proc.is_alive():
                if worker is not None:
                    worker.kill()
                worker = self._spawn()
            worker.conn.send((fn, args))
        except (TypeError, AttributeError) as exc:           # an argument that cannot be pickled
            self._idle.put(worker)
            raise WorkerFailed(f"the stage could not be sent to a worker ({type(exc).__name__})") from exc
        except Exception as exc:                        # noqa: BLE001 - the worker could not be reached
            if worker is not None:
                worker.kill()
            self._replace_later()
            raise WorkerFailed("a worker could not be started or reached") from exc
        try:
            if not worker.conn.poll(timeout):
                worker.kill()
                self._replace_later()
                raise DeadlineExceeded("the stage ran out of time")
            kind, value = worker.conn.recv()
        except (EOFError, OSError) as exc:
            worker.kill()
            self._replace_later()
            raise WorkerFailed("the worker died") from exc
        self._idle.put(worker)
        if kind == "err":
            raise value
        return value

    def close(self) -> None:
        self._closed = True
        while True:
            try:
                w = self._idle.get_nowait()
            except queue.Empty:
                return
            if w is not None:
                w.kill()


class Budget:
    """The seconds one request may spend in workers, shared by every stage it runs. Not thread-safe: one per request."""

    def __init__(self, runner: Any, seconds: Optional[float]):
        self.runner, self.left = runner, seconds

    def run(self, fn: Callable[..., Any], *args: Any) -> Any:
        if self.left is not None and self.left <= 0:
            raise DeadlineExceeded("the request's time is spent")
        started = time.monotonic()
        try:
            return self.runner.run(fn, *args, timeout=self.left)
        finally:
            if self.left is not None:
                self.left -= time.monotonic() - started
