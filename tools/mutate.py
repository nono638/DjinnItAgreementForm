"""Mutation testing of one module with cosmic-ray: do the tests notice when the code changes?

cosmic-ray (https://cosmic-ray.readthedocs.io/en/latest/) makes small changes to the module one at a time
("mutants": `>=` becomes `>`, 50 becomes 51, `and` becomes `or`) and runs the module's tests on each. A mutant
the tests fail on is caught ("killed"). One they pass on "survived": the code can do something else and no test
notices, so either a test is missing or the code isn't needed
(https://cosmic-ray.readthedocs.io/en/latest/theory.html).

Usage, from the repository, with the project's Python:

    .venv\\Scripts\\python tools\\mutate.py invoice_calc             test minute_filler/invoice_calc.py
    .venv\\Scripts\\python tools\\mutate.py invoice_calc --report    the last run's report again
    .venv\\Scripts\\python tools\\mutate.py invoice_calc --resume    finish a run that was stopped

Options: --tests FILE ... (instead of the module's list in MODULES), --workers N (default 8), --work DIR.
Exit code: 0 no survivors, 1 survivors or mutants that broke the run, 2 it could not run (or stopped before
every mutant was tested: --resume finishes it).

How it runs (the comments below say why):
- cosmic-ray edits the module's file in place, so nothing runs in the repository (in Dropbox, which would sync
  the mutants). The working tree (with uncommitted changes, without git-ignored files) is copied to a work
  folder, %LOCALAPPDATA%\\YinItMutate\\<module>\\src (dots as "_": gui_main_window), and from there once for
  each worker (w1, w2, ...).
- cosmic-ray has its own environment, %LOCALAPPDATA%\\YinItMutate\\venv (requirements-mutate.txt), shared by
  every module's runs, not .venv: it is no part of the app and PyInstaller never sees it. It is made again when
  that file changes, or when the Python it was made from is gone. The tests run on .venv's Python.
- 8 workers test mutants side by side, each running only the module's own test files (MODULES), so a mutant
  takes seconds. invoice_calc takes 10 to 20 minutes.
- Mutants that only change a type hint are skipped (no test can see them), and so are comparisons turned
  into `is` (EXCLUDE_OPERATORS) and lines marked `# pragma: no mutate`.
- The report (also in report.txt, with cosmic-ray's own report.html beside it) lists each survivor as
  file:line, the line and the change. Survivors known to change nothing (MODULES[...]["equivalent"]) are
  listed apart, with why.

tools/mutate_cr.py holds the pieces that need cosmic-ray itself, run on its own environment's Python: the
workers, exec and the type-hint filter.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"  # the tests run on the app's own environment
REQUIREMENTS = ROOT / "requirements-mutate.txt"
HELPER = Path(__file__).resolve().parent / "mutate_cr.py"
DOCS = "https://cosmic-ray.readthedocs.io/en/latest"
WORKERS = 8
HINT_SKIP = "Filtered type hint"  # the output mutate_cr.py's filter gives the mutants it skips

# For each module (its name under minute_filler/, "gui.main_window" for one in a folder):
# - "tests": the test files that check it, the quickest first. pytest stops at the first failure (-x), so a
#   mutant the first file catches costs about a second; a survivor runs them all.
# - "equivalent": mutants known to change nothing any test could see: (the line, the line as the mutant has it,
#   why). Lines are compared without spaces or comments, so an entry follows its line when the code moves, and
#   cosmic-ray's own spacing ("max( 0, n)") doesn't matter. An entry that no longer matches is reported stale.
MODULES: dict[str, dict] = {
    "invoice_calc": {
        "tests": ["tests/test_invoice_calc.py", "tests/test_portions.py", "tests/test_show_the_math.py",
                  "tests/test_invoice.py", "tests/test_speeds_and_order.py", "tests/test_joint_invoice.py",
                  "tests/test_deliver.py", "tests/test_agreement_pages.py"],
        "equivalent": [
            ("pages: int | Decimal = 0", "pages: int | Decimal = -1", "every QuoteLine is made with its pages"),
            ("pages: int | Decimal = 0", "pages: int | Decimal = 1", "every QuoteLine is made with its pages"),
            ("return (self.total / max(1, self.parties)).quantize(CENT, ROUND_CEILING)",
             "return (self.total / max(0, self.parties)).quantize(CENT, ROUND_CEILING)",
             "parties is never 0 there: quote() makes it at least 1, and a firm's share returns above"),
            ('if mode == "on":', 'if mode >= "on":', 'of "on", "off" and "auto", only "on" sorts from "on" on'),
            ('if mode == "off" or not days:', 'if mode >= "off" or not days:',
             '"on" has returned above, and "auto" sorts before "off"'),
            ('if rule == "total":', 'if rule >= "total":', '"each" has returned above, and "any" sorts before "total"'),
            ('def quote(days: int | list[int], sp: Speed, parties: int = 1, include_email: bool = True,',
             'def quote(days: int | list[int], sp: Speed, parties: int = 0, include_email: bool = True,',
             "parties is made at least 1 at once"),
            ('index = {True: "auto", False: "off"}.get(index, index)',
             'index = {False: "auto", False: "off"}.get(index, index)',
             'True left as it is works as "auto": index_days reads anything but "on" and "off" as auto'),
            ('line("Index", idx, parties if index_shared == "each" else 1, indexed)',
             'line("Index", idx, parties if index_shared <= "each" else 1, indexed)',
             'only "split" and "each" get there (Settings checks it), and "split" sorts after "each"'),
            ("@dataclass(frozen=True)", "@dataclass(frozen=False)",
             "frozen only stops a Share from being changed and makes it hashable; nothing does either"),
            ("q.lines.append(QuoteLine(label, rate, 1, _decimal(amount), shown,",
             "q.lines.append(QuoteLine(label, rate, 0, _decimal(amount), shown,",
             "a line's qty shows only when above 1, and a share's amount isn't rate x qty"),
        ],
    },
}

# Comparisons turned into `is`/`is not` (`mode == "on"` -> `mode is "on"`): on the literals and small numbers
# compared here they behave like ==/!= (interned), and Python warns about `is` with a literal anyway. A filter by
# operator name, as https://cosmic-ray.readthedocs.io/en/latest/how-tos/filters.html describes (cosmic-ray
# matches these from the start of the name, with re.match: cosmic_ray/tools/filters/operators_filter.py).
EXCLUDE_OPERATORS = [r"core/ReplaceComparisonOperator_(Eq|NotEq|Lt|LtE|Gt|GtE)_(Is|IsNot)$"]


# ------------------------------------------------------------------ type hints
def hint_spans(source: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Where the type hints are: (start, end) of each annotation as (line from 1, column from 0), as cosmic-ray
    gives a mutation's place. 'def f(x: int | None = None) -> str:' -> the spans of 'int | None' and 'str', not
    of the default. ast counts columns in UTF-8 bytes, cosmic-ray (parso) in characters: converted here."""
    lines = source.split("\n")

    def at(line: int, col: int) -> tuple[int, int]:
        return line, len(lines[line - 1].encode("utf-8")[:col].decode("utf-8", errors="ignore"))

    spans = []
    for node in ast.walk(ast.parse(source)):
        hints = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            hints.append(node.returns)
        elif isinstance(node, ast.arg) and node.annotation:
            hints.append(node.annotation)
        elif isinstance(node, ast.AnnAssign):
            hints.append(node.annotation)
        spans += [(at(h.lineno, h.col_offset), at(h.end_lineno, h.end_col_offset)) for h in hints]
    return spans


def in_hint(spans, start, end) -> bool:
    """The text from start to end lies inside one of the hints (hint_spans)."""
    return any(a <= tuple(start) and tuple(end) <= b for a, b in spans)


# ------------------------------------------------------------------ the report
def _code(line: str) -> str:
    """A line without its comment and spaces, for comparing: 'x = max( 0, n)  # why' -> 'x=max(0,n)'."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "#":
            line = line[:i]
            break
    return "".join(line.split())


@dataclass
class Mutant:
    """One mutant as the report shows it."""
    path: str      # 'minute_filler/invoice_calc.py'
    line: int
    source: str    # the line as it is
    mutated: str   # the line as the mutant has it ("" when the mutant removes it)
    operator: str  # 'core/ReplaceComparisonOperator_GtE_Gt'
    output: str = ""


@dataclass
class Summary:
    """A run's results, sorted (see summarize)."""
    total: int = 0
    caught: int = 0
    timed_out: int = 0  # caught too: cosmic-ray counts a test run that takes too long as killed
    pending: int = 0
    no_test: int = 0    # cosmic-ray found nothing to change
    skipped: dict[str, int] = field(default_factory=dict)  # why -> how many
    survived: list[Mutant] = field(default_factory=list)
    known: list[tuple[Mutant, str]] = field(default_factory=list)  # survivors in "equivalent", with why
    stale: list[tuple[str, str, str]] = field(default_factory=list)  # "equivalent" entries no survivor matched
    problems: list[Mutant] = field(default_factory=list)  # broke the run (incompetent, exception, abnormal)


def _mutant(item: dict, result: dict | None, lines_of) -> Mutant:
    m = item["mutations"][0]
    path = Path(m["module_path"]).as_posix()  # cosmic-ray stores it with backslashes on Windows
    row = m["start_pos"][0]
    lines = lines_of(path)
    diff = (result or {}).get("diff") or ""
    added = [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    return Mutant(path, row, lines[row - 1].strip() if row <= len(lines) else "",
                  added[0].strip() if added else "", m["operator_name"], (result or {}).get("output") or "")


def summarize(records: list, lines_of, equivalent=()) -> Summary:
    """Sorts the output of `cosmic-ray dump` ((work item, result or None) for each mutant): caught, skipped and
    why, survivors, and survivors known to change nothing (`equivalent`, as in MODULES). lines_of(path) gives
    the lines of a module as they were mutated."""
    out = Summary(total=len(records))
    known = {(_code(a), _code(b)): (a, b, why) for a, b, why in equivalent}
    matched = set()
    skip_names = {"Filtered operator": "an `is` comparison", HINT_SKIP: "a type hint", None: "pragma: no mutate"}
    for item, result in records:
        if result is None:
            out.pending += 1
            continue
        worker, test = result.get("worker_outcome"), result.get("test_outcome")
        if worker == "skipped":
            why = skip_names.get(result.get("output"), result.get("output"))
            out.skipped[why] = out.skipped.get(why, 0) + 1
        elif worker == "no-test":
            out.no_test += 1
        elif worker == "normal" and test == "killed":
            out.caught += 1
            out.timed_out += result.get("output") == "timeout"  # (cosmic_ray/testing.py: a timeout is killed)
        elif worker == "normal" and test == "survived":
            mutant = _mutant(item, result, lines_of)
            key = (_code(mutant.source), _code(mutant.mutated))
            if key in known:
                matched.add(key)
                out.known.append((mutant, known[key][2]))
            else:
                out.survived.append(mutant)
        else:  # incompetent, or the worker failed: something to look at, not a result
            out.problems.append(_mutant(item, result, lines_of))
    if not out.pending:
        out.stale = [v for k, v in known.items() if k not in matched]
    out.survived.sort(key=lambda m: (m.path, m.line))
    out.known.sort(key=lambda k: (k[0].path, k[0].line))
    return out


def format_report(s: Summary, module_path: str, name: str) -> str:
    """The report printed after a run (and saved as report.txt)."""
    def show(m: Mutant, why: str = "") -> list[str]:
        return [f"  {m.path}:{m.line}: {m.source}",
                f"      -> {m.mutated or '(line removed)'}   ({m.operator})" + (f"\n         {why}" if why else "")]

    ran = s.total - s.pending - sum(s.skipped.values())
    out = [f"Mutation testing of {module_path}: {s.total} mutants, {ran} tested"
           + (f", {s.pending} not yet (run again with --resume)" if s.pending else ""),
           f"  caught: {s.caught}" + (f" ({s.timed_out} by taking too long)" if s.timed_out else ""),
           f"  survived: {len(s.survived)}, and {len(s.known)} known to change nothing"]
    if s.skipped:
        out.append("  skipped: " + ", ".join(f"{n} {why}" for why, n in sorted(s.skipped.items(), key=str)))
    if s.no_test:
        out.append(f"  nothing to change: {s.no_test}")
    if s.survived:
        out += ["", "Survived: no test noticed these changes. Add a test, remove the code if it isn't needed, or, if",
                f"the change can't make a difference, add it to MODULES[{name!r}]['equivalent'] with why",
                f"({DOCS}/theory.html):"]
        for m in s.survived:
            out += show(m)
    if s.problems:
        out += ["", "Broke the run (incompetent, or the worker failed): look at these, they are not results:"]
        for m in s.problems:
            out += show(m) + ["         " + line for line in m.output.strip().splitlines()[-6:]]
    if s.known:
        out += ["", f"Known to change nothing (MODULES[{name!r}]['equivalent'] in tools/mutate.py):"]
        for m, why in s.known:
            out += show(m, why)
    if s.stale:
        out += ["", "Stale: these 'equivalent' entries matched no survivor (the code changed, or a test catches "
                    "them now); update or remove them:"]
        out += [f"  {a}  ->  {b}" for a, b, _ in s.stale]
    return "\n".join(out)


# ------------------------------------------------------------------ the run
def module_of(arg: str) -> tuple[str, str]:
    """'invoice_calc', 'gui.main_window' or 'minute_filler/invoice_calc.py' -> (name, path), e.g.
    ('invoice_calc', 'minute_filler/invoice_calc.py')."""
    p = arg.replace("\\", "/").removesuffix(".py").removeprefix("minute_filler/")
    name = p.replace("/", ".")
    path = "minute_filler/" + name.replace(".", "/") + ".py"
    if not (ROOT / path).is_file():
        raise SystemExit(f"{path}: no such module")
    return name, path


def tests_naming(name: str) -> list[str]:
    """The test files that import the module, as a start for its list in MODULES."""
    last = name.split(".")[-1]
    pattern = re.compile(rf"^\s*(from|import)\s.*\b{re.escape(last)}\b", re.M)
    return sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py")
                  if pattern.search(p.read_text(encoding="utf-8")))


def work_root(arg: str | None) -> Path:
    """The work folder. cosmic-ray changes the files it tests, so never the repository, and never Dropbox (it
    would sync every mutant)."""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = (Path(arg) if arg else Path(local) / "YinItMutate").resolve()
    if path == ROOT or ROOT in path.parents or "dropbox" in str(path).lower():
        raise SystemExit(f"{path}: the work folder must be outside the repository and Dropbox")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _base_found(venv: Path) -> bool:
    """Whether the Python an environment was made from (pyvenv.cfg's home) is still there."""
    try:
        cfg = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return False
    home = next((v.strip() for k, _, v in (l.partition("=") for l in cfg.splitlines()) if k.strip() == "home"), "")
    return bool(home) and (Path(home) / "python.exe").is_file()


def ensure_env(work: Path) -> Path:
    """cosmic-ray's own environment (its Scripts folder), made from requirements-mutate.txt the first time and
    again when that file changes. Kept apart from .venv, so the app's build environment stays what PyInstaller
    bundles and cosmic-ray's own packages can't change the app's."""
    venv = work / "venv"
    py = venv / "Scripts" / "python.exe"
    want = _sha(REQUIREMENTS)
    marker = venv / "requirements.sha256"
    if py.exists() and not _base_found(venv):
        # (its python.exe only starts the Python it was made from: with that one gone or moved, every worker
        # would stop at start)
        shutil.rmtree(venv)
    if py.exists() and marker.exists() and marker.read_text() == want:
        return venv / "Scripts"
    print(f"Installing cosmic-ray (requirements-mutate.txt) in {venv} ...", flush=True)
    if not py.exists():
        subprocess.run([str(APP_PYTHON), "-m", "venv", str(venv)], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(REQUIREMENTS)],
                   check=True)
    marker.write_text(want)
    return venv / "Scripts"


def tree_files() -> list[str]:
    """The working tree's files as git sees them: committed or not, but not git-ignored ones (the private
    samples, __pycache__)."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8")
    return [f for f in out.split("\0") if f and (ROOT / f).is_file()]


def copy_tree(dest: Path, files: list[str]) -> None:
    """A fresh copy of the working tree (tree_files) in dest."""
    if dest.exists():
        shutil.rmtree(dest)
    for f in files:
        (dest / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dest / f)


def free_ports(n: int) -> list[int]:
    """n free ports on this computer, from the system."""
    socks = []
    for _ in range(n):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        socks.append(s)
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


def write_config(path: Path, module_path: str, tests: list[str], ports: list[int], timeout: float) -> None:
    """cosmic-ray's config.toml (keys as in the tutorial, https://cosmic-ray.readthedocs.io/en/latest/tutorials/
    intro/index.html, and the http distributor, .../tutorials/distributed/index.html). cosmic-ray splits
    test-command with shlex, so the path to Python is quoted and has forward slashes, and so is a test file's
    name with a space in it."""
    command = " ".join([f'"{APP_PYTHON.as_posix()}"', "-m pytest -x -q -p no:cacheprovider",
                        *(shlex.quote(t) for t in tests)])
    path.write_text("\n".join([
        "# Written by tools/mutate.py",
        "[cosmic-ray]",
        f"module-path = {json.dumps(module_path)}",
        f"timeout = {timeout:.1f}",
        "excluded-modules = []",
        f"test-command = {json.dumps(command)}",
        "",
        "[cosmic-ray.distributor]",
        'name = "http"',
        "",
        "[cosmic-ray.distributor.http]",
        f"worker-urls = {json.dumps([f'http://127.0.0.1:{p}' for p in ports])}",
        "",
        "[cosmic-ray.filters.operators-filter]",
        f"exclude-operators = {json.dumps(EXCLUDE_OPERATORS)}",
        "",
    ]), encoding="utf-8")


def worker_env(home: Path) -> dict[str, str]:
    """The environment of the workers, and so of the tests they run. PYTHONUTF8: cosmic-ray reads pytest's
    output as UTF-8 (cosmic_ray/testing.py), and without it a failed run's Windows-encoded output crashes that,
    so a caught mutant is counted "incompetent". Off-screen Qt: no windows open. APPDATA, USERPROFILE and HOME in
    the work folder: even a mutant that broke conftest's isolation can't reach the real records."""
    for d in ("appdata", "home"):
        (home / "home" / d).mkdir(parents=True, exist_ok=True)
    return {**os.environ, "PYTHONUTF8": "1", "QT_QPA_PLATFORM": "offscreen",
            "APPDATA": str(home / "home" / "appdata"), "USERPROFILE": str(home / "home" / "home"),
            "HOME": str(home / "home" / "home")}


def start_workers(py: Path, copies: list[Path], ports: list[int], env: dict, home: Path) -> list:
    """One worker in each copy. Each needs a copy of its own, run from where the tests would be run
    (https://cosmic-ray.readthedocs.io/en/latest/tutorials/distributed/index.html). Not cosmic-ray's
    cr-http-workers: it clones the committed code only (so new tests that aren't committed yet would be
    missing), and its own docstring says its clones may stay behind on Windows."""
    procs = []
    for i, (copy, port) in enumerate(zip(copies, ports), 1):
        with open(home / f"w{i}.log", "wb") as log:
            procs.append(subprocess.Popen([str(py), str(HELPER), "worker", str(port), str(os.getpid())], cwd=copy,
                                          env=env, stdout=log, stderr=subprocess.STDOUT))
    end = time.time() + 90
    waiting = dict(enumerate(ports))
    while waiting:
        for i, port in list(waiting.items()):
            if procs[i].poll() is not None:
                raise RuntimeError(f"worker {i + 1} stopped at start: see {home / f'w{i + 1}.log'}")
            try:  # cosmic-ray counts work sent to a worker that isn't listening yet as failed ("abnormal")
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                del waiting[i]
            except OSError:
                pass
        if waiting and time.time() > end:
            raise RuntimeError("the workers did not start: see the w*.log files in " + str(home))
        time.sleep(0.2)
    return procs


def stop_workers(procs: list) -> None:
    """Stops the workers this run started, by their process ids (with /T, any test run each has going too).
    Never by name: that would stop other Python programs."""
    for p in procs:
        if p.poll() is None:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)


def _dump(scripts: Path, session: Path, env: dict) -> list:
    """`cosmic-ray dump`: (work item, result or None) for each mutant (https://cosmic-ray.readthedocs.io/en/
    latest/reference/cli.html)."""
    out = subprocess.run([str(scripts / "cosmic-ray.exe"), "dump", str(session)], env=env, capture_output=True,
                         check=True).stdout.decode("utf-8")
    return [json.loads(l) for l in out.splitlines() if l.strip()]


def _progress(session: Path) -> str:
    """'212/331 done', read from the session without touching it."""
    try:
        with sqlite3.connect(session.as_uri() + "?mode=ro", uri=True) as db:
            done = db.execute("select count(*) from work_results").fetchone()[0]
            total = db.execute("select count(*) from work_items").fetchone()[0]
        return f"{done}/{total} done"
    except sqlite3.Error:
        return "..."


def report(name: str, module_path: str, home: Path, scripts: Path, env: dict) -> int:
    """Prints the run's report and saves it as report.txt (and cosmic-ray's report.html); 1 when something
    survived or broke the run, else 2 when mutants are still to be tested (exec stopped part way), else 0."""
    src = home / "src"
    cache: dict[str, list[str]] = {}

    def lines_of(path: str) -> list[str]:
        if path not in cache:
            cache[path] = (src / path).read_text(encoding="utf-8").splitlines()
        return cache[path]

    s = summarize(_dump(scripts, home / "session.sqlite", env), lines_of, MODULES.get(name, {}).get("equivalent", ()))
    text = format_report(s, module_path, name)
    (home / "report.txt").write_text(text + "\n", encoding="utf-8")
    html = subprocess.run([str(scripts / "cr-html.exe"), str(home / "session.sqlite")], env=env,
                          capture_output=True).stdout
    (home / "report.html").write_bytes(html)
    print(text + f"\n\n(saved in {home / 'report.txt'}; cosmic-ray's own report: {home / 'report.html'})", flush=True)
    return 1 if s.survived or s.problems else 2 if s.pending else 0


def run(name: str, module_path: str, tests: list[str], workers: int, work: Path, resume: bool) -> int:
    """A whole run: copies, workers, baseline, session, filters, exec and the report (or, with resume, exec on
    the session a stopped run left)."""
    home = work / name.replace(".", "_")
    session, config, stamp = home / "session.sqlite", home / "config.toml", home / "run.json"
    fingerprint = {"module": _sha(ROOT / module_path), "tests": {t: _sha(ROOT / t) for t in tests}}
    if resume:
        old = json.loads(stamp.read_text()) if stamp.exists() else {}
        if not session.exists() or old.get("fingerprint") != fingerprint:
            print("Can't resume: no stopped run, or the module or its tests changed since. Run it again without "
                  "--resume.")
            return 2
    elif home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True, exist_ok=True)
    scripts = ensure_env(work)
    env = worker_env(home)
    copy_tree(home / "src", tree_files())  # never mutated: init reads it, and the report quotes its lines
    copies = [home / f"w{i}" for i in range(1, workers + 1)]
    for c in copies:  # fresh ones on --resume too: a worker stopped in the middle may have left its mutant
        if c.exists():
            shutil.rmtree(c)
        shutil.copytree(home / "src", c)
    ports = free_ports(workers)
    cr = str(scripts / "cosmic-ray.exe")
    procs = []
    try:
        print(f"{module_path}: {workers} workers in {home}", flush=True)
        if resume:
            # worker-urls is read by exec only: changing it needs no new session (unlike the keys the
            # tutorial lists, https://cosmic-ray.readthedocs.io/en/latest/tutorials/intro/index.html)
            write_config(config, module_path, tests, ports, json.loads(stamp.read_text())["timeout"])
            procs = start_workers(scripts / "python.exe", copies, ports, env, home)
        else:
            write_config(config, module_path, tests, ports, 600)
            procs = start_workers(scripts / "python.exe", copies, ports, env, home)
            # The tests must pass on the module as it is, or the results mean nothing (the tutorial, as above)
            started = time.time()
            subprocess.run([cr, "baseline", str(config), "--session-file", str(home / "baseline.sqlite")],
                           cwd=home / "src", env=env, capture_output=True)
            seconds = time.time() - started
            base = _dump(scripts, home / "baseline.sqlite", env)
            outcome = base[0][1] if base and base[0][1] else {}
            if outcome.get("test_outcome") != "survived":
                print("The tests fail on the module as it is, so mutants can't be judged:\n"
                      + (outcome.get("output") or "(no output)")[-3000:])
                return 2
            # A run that takes longer than the timeout counts as caught (cosmic_ray/testing.py; the concepts page,
            # https://cosmic-ray.readthedocs.io/en/latest/concepts.html, says "incompetent"), so a short timeout
            # would hide survivors: ten times the baseline, as the workers run side by side and slow each other
            timeout = max(120.0, 10 * seconds)
            print(f"Baseline: the tests pass in {seconds:.0f} s; timeout {timeout:.0f} s", flush=True)
            write_config(config, module_path, tests, ports, timeout)
            # init makes the session; the filters then mark mutants skipped, before exec
            # (https://cosmic-ray.readthedocs.io/en/latest/how-tos/filters.html)
            subprocess.run([cr, "init", str(config), str(session)], cwd=home / "src", env=env, check=True)
            subprocess.run([str(scripts / "cr-filter-operators.exe"), str(session), str(config)], cwd=home / "src",
                           env=env, check=True)
            subprocess.run([str(scripts / "cr-filter-pragma.exe"), str(session)], cwd=home / "src", env=env,
                           check=True)
            subprocess.run([str(scripts / "python.exe"), str(HELPER), "skip-hints", str(session), "--root",
                            str(home / "src")], env=env, check=True)
            stamp.write_text(json.dumps({"fingerprint": fingerprint, "timeout": timeout}))
        print(f"Testing mutants: {_progress(session)}", flush=True)
        with open(home / "exec.log", "wb") as log:
            # `cosmic-ray exec`, ending with this script however it ends (mutate_cr.die_with), so --resume works
            ex = subprocess.Popen([str(scripts / "python.exe"), str(HELPER), "exec", str(config), str(session),
                                   str(os.getpid())], cwd=home / "src", env=env, stdout=log, stderr=subprocess.STDOUT)
            shown = time.time()
            while ex.poll() is None:
                time.sleep(1)
                if time.time() - shown >= 30:
                    shown = time.time()
                    print(f"  {_progress(session)}", flush=True)
        if ex.returncode:
            print(f"cosmic-ray exec stopped (exit {ex.returncode}): see {home / 'exec.log'}")
    except (RuntimeError, subprocess.CalledProcessError) as e:
        print(f"Stopped: {e}")
        return 2
    finally:
        stop_workers(procs)
    return report(name, module_path, home, scripts, env)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(errors="replace")  # a line of code may hold a character the console can't show
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("module", help="'invoice_calc', 'gui.main_window' or 'minute_filler/invoice_calc.py'")
    p.add_argument("--tests", nargs="+", help="test files to run instead of the module's in MODULES")
    p.add_argument("--workers", type=int, default=WORKERS)
    p.add_argument("--work", help="the work folder (default %%LOCALAPPDATA%%\\YinItMutate)")
    p.add_argument("--resume", action="store_true", help="finish the last run, if the module and tests are unchanged")
    p.add_argument("--report", action="store_true", help="the last run's report again")
    a = p.parse_args(argv)
    name, module_path = module_of(a.module)
    work = work_root(a.work)
    if not APP_PYTHON.is_file():
        print(f"No {APP_PYTHON}: make the project's environment first (README, Building from source).")
        return 2
    # (a pip install without a network, git missing, a copy that failed: exit code 2, not 1, which says survivors)
    try:
        if a.report:
            home = work / name.replace(".", "_")
            if not (home / "session.sqlite").exists():
                print(f"No run of {module_path} in {work}.")
                return 2
            return report(name, module_path, home, work / "venv" / "Scripts", worker_env(home))
        tests = a.tests or MODULES.get(name, {}).get("tests")
        if not tests:
            print(f"{name} has no test files in MODULES (tools/mutate.py). Those that import it:\n  "
                  + "\n  ".join(tests_naming(name) or ["(none)"]) + "\nAdd its list there, or pass --tests.")
            return 2
        missing = [t for t in tests if not (ROOT / t).is_file()]
        if missing:
            print("No such test file: " + ", ".join(missing))
            return 2
        return run(name, module_path, tests, max(1, a.workers), work, a.resume)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"Could not run: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
