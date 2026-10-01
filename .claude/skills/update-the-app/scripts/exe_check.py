"""Runs the built program headless on made-up inputs and checks what it made.

After PyInstaller (from anywhere; PYTHONPATH is not needed):
    .venv/Scripts/python.exe .claude/skills/update-the-app/scripts/exe_check.py

It uses a temporary APPDATA and home folder, so the user's own settings, records and run sheets are not
touched (the folder is removed again when all is good). Expected: the built version, no errors in the log,
and for the transcript job a minute agreement, a MOFR, an invoice for its 10 transcript pages and a run sheet
with 4 takes. Exit code 1 if something is off.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # .claude/skills/update-the-app/scripts/ -> the repository
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from openpyxl import load_workbook  # noqa: E402

from minute_filler import __version__  # noqa: E402
from test_runsheet import transcript  # noqa: E402

exe = ROOT / "dist" / "DjinnItAgreementForm" / "DjinnItAgreementForm.exe"
if not exe.exists():
    sys.exit(f"PROBLEM: {exe} is not there - build it first (PyInstaller)")
run = Path(tempfile.mkdtemp(prefix="djinnit-exe-check-"))
src = run / "in"
src.mkdir()
transcript(src / "Transcript 6-3-2026 Roe v Poe.pdf")  # two reporters, a witness, a word index at the end
(src / "email.txt").write_text("From: someone@examplefirm.com\nSubject: minutes\n\nPlease send the minutes in "
                               "Smith v Jones, Index No. 712222/2024, 5/22/2026, Judge Lopez. About 40 pages.")
env = dict(os.environ, APPDATA=str(run / "appdata"), USERPROFILE=str(run / "home"), HOME=str(run / "home"))
problems = []
try:
    subprocess.run([str(exe), "--batch", str(run / "out"), "--outputs", "agreement,mofr,invoice,runsheet", str(src)],
                   env=env, timeout=300)  # exit code 1 is expected: the e-mail job has no transcript
except subprocess.TimeoutExpired:
    sys.exit(f"PROBLEM: the program did not finish within 5 minutes (files in {run})")

report_file = run / "out" / "batch.json"
if not report_file.exists():
    sys.exit(f"PROBLEM: the program wrote no batch.json (files in {run})")
report = json.loads(report_file.read_text(encoding="utf-8"))
for j in report["jobs"]:
    print(" ", j["case"], "|", j["forms"], "|", j["error"] or "ok")
roe = next((j for j in report["jobs"] if "Roe" in (j["case"] or "")), None)
if not roe or len(roe["forms"]) != 4 or roe["error"]:
    problems.append("the transcript job should have an agreement, a MOFR, an invoice and a run sheet")
invoices_csv = run / "home" / "Documents" / "DjinnIt Records" / "invoices.csv"
rows = invoices_csv.read_text(encoding="utf-8-sig").splitlines() if invoices_csv.exists() else []
if len(rows) < 2:
    problems.append("no invoice was recorded")
elif ",10,1," not in rows[1]:
    problems.append("the invoice should bill the 10 transcript pages (not the word index)")
sheets = list((run / "home" / "Documents" / "DjinnIt Run Sheets").glob("*.xlsx"))
if sheets:
    ws = load_workbook(sheets[0])["Run Sheet"]
    takes = [(ws.cell(r, 3).value, ws.cell(r, 6).value) for r in range(5, ws.max_row + 1)]
    print("  run sheet:", takes)
    if takes != [("Pat", 2), ("Dana", 3), ("Pat", 3), ("Dana", 2)]:
        problems.append("the run sheet's takes are not as expected")
else:
    problems.append("no run sheet was made")
log_file = run / "appdata" / "DjinnItAgreementForm" / "logs" / "app.log"
log = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
started = [l for l in log.splitlines() if "started: version" in l]
print("  log:", started[-1][20:80] if started else "(no start line)", "| errors:", log.count("ERROR"))
if not started or f"version {__version__} " not in started[-1]:
    problems.append(f"the built program is not version {__version__} (rebuild?)")
if log.count("ERROR"):
    problems.append("the log has errors")
if problems:
    print("\n".join("PROBLEM: " + p for p in problems) + f"\n(files in {run})")
else:
    print("all good")
    shutil.rmtree(run, ignore_errors=True)
sys.exit(1 if problems else 0)
