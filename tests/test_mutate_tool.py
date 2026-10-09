"""tools/mutate.py, the mutation-testing tool (cosmic-ray, for developers). The modules and test files it names
exist, and its known-equivalent lines are still in the code: a rename or a move would leave it testing nothing.
It tells type hints from the code around them, its report sorts a run's results, its work folder is never the
repository or Dropbox, and cosmic-ray stays out of the app's requirements."""
from pathlib import Path

import pytest

from tools import mutate

ROOT = Path(__file__).resolve().parent.parent


def test_every_module_test_file_and_known_equivalent_it_names_exists():
    for name, spec in mutate.MODULES.items():
        _, path = mutate.module_of(name)
        for t in spec["tests"]:
            assert (ROOT / t).is_file(), (name, t)
        lines = {mutate._code(l) for l in (ROOT / path).read_text(encoding="utf-8").splitlines()}
        for source, mutated, why in spec.get("equivalent", ()):
            assert mutate._code(source) in lines, (name, source)
            assert mutate._code(mutated) != mutate._code(source) and why, (name, source)


def test_type_hints_are_told_from_the_code_around_them():
    src = "def f(x: int | None = None, *a: str) -> dict[str, int]:\n    y: list[int] = []\n    return {}\n"
    spans = mutate.hint_spans(src)
    assert mutate.in_hint(spans, (1, 13), (1, 14))  # the "|" of int | None
    assert not mutate.in_hint(spans, (1, 22), (1, 26))  # its default, None
    assert mutate.in_hint(spans, (1, 40), (1, 54))  # dict[str, int]
    assert mutate.in_hint(spans, (2, 7), (2, 16)) and not mutate.in_hint(spans, (2, 19), (2, 21))  # [], not a hint
    # columns in characters, as cosmic-ray counts them (ast counts bytes: "é" is two)
    assert mutate.hint_spans("def g(é: int | None): pass\n") == [((1, 9), (1, 19))]


def test_the_report_sorts_a_runs_results():
    lines = {"minute_filler/m.py": ["def f(n):", "    return max(1, n)  # at least one", "    x = n >= 50"]}

    def item(row):
        return {"job_id": str(row), "mutations": [{"module_path": "minute_filler\\m.py", "operator_name": "core/X",
                                                   "occurrence": 0, "start_pos": [row, 4], "end_pos": [row, 5]}]}

    def result(test=None, output="", plus="", worker="normal"):
        diff = f"--- a\n+++ b\n@@ -1 +1 @@\n-old\n+{plus}\n" if plus else None
        return {"worker_outcome": worker, "test_outcome": test, "output": output, "diff": diff}

    records = [
        (item(2), result("survived", plus="    return max( 0, n)  # at least one")),  # cosmic-ray's spacing
        (item(3), result("survived", plus="    x = n > 50")),
        (item(3), result("killed", "1 failed")),
        (item(3), result("killed", "timeout")),  # took too long: caught all the same
        (item(1), result(worker="skipped", output=mutate.HINT_SKIP)),
        (item(1), result(worker="skipped", output="Filtered operator")),
        (item(2), result("incompetent", "Traceback\nboom")),
        (item(2), None),
    ]
    equivalent = [("return max(1, n)", "return max(0, n)", "n is at least 1"), ("x = 1", "x = 2", "gone")]
    s = mutate.summarize(records, lines.__getitem__, equivalent)
    assert (s.total, s.caught, s.timed_out, s.pending) == (8, 2, 1, 1)
    assert s.skipped == {"a type hint": 1, "an `is` comparison": 1}
    assert [(m.path, m.line, m.mutated) for m in s.survived] == [("minute_filler/m.py", 3, "x = n > 50")]
    assert [(m.line, why) for m, why in s.known] == [(2, "n is at least 1")]
    assert [m.output for m in s.problems] == ["Traceback\nboom"]
    assert s.stale == []  # not judged while mutants are still to be tested
    text = mutate.format_report(s, "minute_filler/m.py", "m")
    assert "  minute_filler/m.py:3: x = n >= 50\n      -> x = n > 50" in text and "--resume" in text
    assert mutate.summarize(records[:-1], lines.__getitem__, equivalent).stale == [("x = 1", "x = 2", "gone")]


def test_the_work_folder_is_never_the_repository_or_dropbox(tmp_path):
    for bad in (ROOT, ROOT / "build", Path("C:/Users/someone/Dropbox/work")):
        with pytest.raises(SystemExit):
            mutate.work_root(str(bad))
    assert mutate.work_root(str(tmp_path / "w")) == (tmp_path / "w").resolve()


def test_cosmic_ray_is_a_developer_tool_only():
    """Pinned in requirements-mutate.txt (its own environment), not in the app's requirements."""
    assert any(l.startswith("cosmic-ray==") for l in (ROOT / "requirements-mutate.txt").read_text().splitlines())
    app = [l for l in (ROOT / "requirements.txt").read_text().splitlines() if not l.lstrip().startswith("#")]
    assert not any("cosmic" in l.lower() for l in app)


def test_a_run_that_cannot_start_exits_2_not_1(tmp_path, monkeypatch, capsys):
    """Exit code 1 says mutants survived: a pip install without a network (or git missing, a copy that failed)
    ended in a traceback, exit code 1, before the run's own try. It says why and exits 2, as the docstring says."""
    import subprocess

    def offline(work):
        raise subprocess.CalledProcessError(1, ["pip", "install"])

    monkeypatch.setattr(mutate, "ensure_env", offline)
    assert mutate.main(["invoice_calc", "--work", str(tmp_path / "w")]) == 2
    assert "Could not run" in capsys.readouterr().out
    monkeypatch.setattr(mutate, "APP_PYTHON", tmp_path / "no venv" / "python.exe")
    assert mutate.main(["invoice_calc", "--work", str(tmp_path / "w")]) == 2


def test_test_files_with_a_space_are_quoted_for_cosmic_ray(tmp_path):
    """cosmic-ray splits its test command with shlex: a test file's name with a space in it is quoted."""
    import shlex
    import tomllib
    mutate.write_config(tmp_path / "c.toml", "minute_filler/m.py", ["tests/test_a.py", "tests/a b.py"], [1], 60)
    command = tomllib.loads((tmp_path / "c.toml").read_text(encoding="utf-8"))["cosmic-ray"]["test-command"]
    assert shlex.split(command)[-2:] == ["tests/test_a.py", "tests/a b.py"]


def test_the_environment_is_made_again_when_its_python_is_gone(tmp_path):
    """cosmic-ray's environment runs on the Python it was made from (pyvenv.cfg's home): with that one gone,
    every worker stopped at start, as the environment was kept while requirements-mutate.txt was unchanged."""
    venv = tmp_path / "venv"
    venv.mkdir()
    assert not mutate._base_found(venv)  # (no pyvenv.cfg)
    (venv / "pyvenv.cfg").write_text(f"home = {tmp_path / 'Python312'}\nversion = 3.12.1\n", encoding="utf-8")
    assert not mutate._base_found(venv)
    (tmp_path / "Python312").mkdir()
    (tmp_path / "Python312" / "python.exe").write_bytes(b"")
    assert mutate._base_found(venv)


def test_a_run_stopped_part_way_is_not_reported_as_clean(tmp_path, monkeypatch):
    """exec stopped with mutants still to test, none surviving so far: exit code 2 (finish it with --resume),
    not 0, which says nothing survived."""
    from types import SimpleNamespace
    monkeypatch.setattr(mutate, "_dump", lambda *a: [({"job_id": "1"}, None), ({"job_id": "2"}, None)])
    monkeypatch.setattr(mutate.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b""))  # (cr-html)
    assert mutate.report("m", "minute_filler/m.py", tmp_path, tmp_path, {}) == 2
    assert "2 not yet (run again with --resume)" in (tmp_path / "report.txt").read_text(encoding="utf-8")
