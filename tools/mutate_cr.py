"""The parts of tools/mutate.py that need cosmic-ray itself. mutate.py runs them on the Python of cosmic-ray's
own environment (cosmic-ray isn't in .venv); they aren't meant to be run by hand.

    mutate_cr.py worker PORT PARENT              a worker for the http distributor, in the current folder
    mutate_cr.py exec CONFIG SESSION PARENT      `cosmic-ray exec`
    mutate_cr.py skip-hints SESSION --root SRC   marks the mutants that only change a type hint as skipped

PARENT is mutate.py's process id: the worker and exec end when it ends (die_with).
"""
import ctypes
import logging
import os
import sys
import threading
from pathlib import Path

from cosmic_ray.tools.filters.filter_app import FilterApp
from cosmic_ray.work_item import WorkerOutcome, WorkResult

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mutate import HINT_SKIP, hint_spans, in_hint  # noqa: E402  (mutate.py has the code the tests check)


def die_with(parent: int) -> None:
    """Ends this process as soon as `parent` (mutate.py) ends, however it ends. mutate.py stops its workers
    itself, but not when it is killed outright (a stopped background task): workers left behind would run on
    with nobody to stop them, and an exec left behind would mark the mutants still to test "abnormal" when they
    went, so that --resume would find none. Ending with mutate.py, exec leaves them to be tested."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, parent)  # SYNCHRONIZE: enough to wait for it
    if not handle:
        os._exit(1)  # already gone

    def wait() -> None:
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
        os._exit(1)

    threading.Thread(target=wait, daemon=True).start()


def worker(port: int, parent: int) -> None:
    """cosmic-ray's http worker (cosmic_ray.distribution.http.run_worker), listening on 127.0.0.1 only. Its
    own `cosmic-ray http-worker --port` listens on every network address, which can make Windows ask about the
    firewall. It mutates and tests in the folder it is started in
    (https://cosmic-ray.readthedocs.io/en/latest/tutorials/distributed/index.html)."""
    from aiohttp import web
    from cosmic_ray.distribution.http import handle_mutate_and_test

    die_with(parent)
    logging.basicConfig(level=logging.INFO)
    app = web.Application()
    app.add_routes([web.post("/", handle_mutate_and_test)])
    web.run_app(app, host="127.0.0.1", port=port)


class HintFilter(FilterApp):
    """Marks as skipped the mutants that only change a type hint ("int | None" -> "int & None"). A hint isn't
    behaviour, and with `from __future__ import annotations` it is never even evaluated, so no test can catch
    such a mutant: it would only cost a test run and show up as a survivor. A filter as cosmic-ray's own are
    (https://cosmic-ray.readthedocs.io/en/latest/how-tos/filters.html), run after init."""

    def description(self):
        return self.__doc__

    def add_args(self, parser):
        parser.add_argument("--root", required=True, help="the folder the session's module paths are under")

    def filter(self, work_db, args):
        spans = {}

        def hinted(m) -> bool:
            path = Path(args.root) / m.module_path
            if path not in spans:
                spans[path] = hint_spans(path.read_text(encoding="utf-8"))
            return in_hint(spans[path], m.start_pos, m.end_pos)

        skip = [item.job_id for item in work_db.pending_work_items
                if item.mutations and all(hinted(m) for m in item.mutations)]
        if skip:
            work_db.set_multiple_results(skip, WorkResult(output=HINT_SKIP, worker_outcome=WorkerOutcome.SKIPPED))
        print(f"{len(skip)} mutants only change a type hint: skipped", flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["worker"]:
        worker(int(sys.argv[2]), int(sys.argv[3]))
    elif sys.argv[1:2] == ["exec"]:
        from cosmic_ray.cli import main

        die_with(int(sys.argv[4]))
        sys.exit(main(["exec", sys.argv[2], sys.argv[3]]))
    elif sys.argv[1:2] == ["skip-hints"]:
        sys.exit(HintFilter().main(sys.argv[2:]))
    else:
        sys.exit(__doc__)
