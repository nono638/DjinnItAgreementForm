---
name: update-the-app
description: >
  Full release cycle for YinItAgreementForm, in order: bug sweep and fixes, a
  comments and docstrings review, commit and push, rebuild the installer, publish
  a GitHub release with the new version, update the website (GitHub Pages) and
  update the README and internal notes. Use when the user says "update the app",
  "/update-the-app", "ship it", "release a new version" or "do the release cycle".
disable-model-invocation: true
---

# Update the app

Run the steps below in order, in this repository (the folder with `YinItAgreementForm.spec`).
Invoking this skill is the user's go-ahead to commit, push and publish **for this run**. Still stop and
ask if a step fails in a way you can't fix, or if a decision belongs to the user (a change in what is
billed, a feature removed, anything about their money or clients).

Tell the user in a line or two what you're on as you go, e.g. "Step 2 of 7: docstrings".

## Ground rules (read before starting)

- **Privacy: the repo is public (AGPL).** Real documents live only in `samples_internal/`, which is
  git-ignored and must stay that way. Never commit the user's name, phone, e-mail or payment details,
  colleagues' or clients' names, case names, index numbers or anything else from a real document. Tests,
  docstrings, examples, placeholders, screenshots and release notes use made-up data (Pat Reporter, Dana
  Smith, Jane Roe v. Sam Poe, 712345/2021, phone numbers starting 555). Real names creep into
  docstrings and placeholder text when you work from real samples. Placeholder text is compiled into the
  .exe, so scan **before building** (step 3).
- **Python:** `.venv/Scripts/python.exe` (never a bare `python`; never pipe into `python -`, because the
  Windows Store stub waits for input forever). Tests: `.venv/Scripts/python.exe -m pytest -q`. They include
  `tests/test_private_samples.py`, which reads every file in `samples_internal/`.
- **Editing:** use the Edit tool, or patch scripts written with the Write tool. Bash heredocs mangle
  backslashes (`\b` became a backspace character, `\\n` became a real newline). After a scripted patch,
  check the changed `.py` files for control characters. The working copy is CRLF.
- **Long commands** (PyInstaller, about 3 minutes): `run_in_background`. Wait with an `until` loop, not
  `sleep`. Don't combine `rm -rf` with other commands. Give commands no stray `cat` or `python -` that would
  wait for input.
- **Subagents** (the bug sweep runs several): tell each one to edit only its own files, to keep repro
  scripts in the scratchpad, and never to end processes by name (`taskkill /IM python*` once closed the
  user's own Python programs). Only processes it started itself. Every script that imports minute_filler
  must first (at the very top, before that import) set `APPDATA`, `USERPROFILE` and `HOME` to a
  `tempfile.mkdtemp()` folder: setting records_dir/output_dir is not enough, as `Ledger()` otherwise writes
  the user's real `%APPDATA%\YinItAgreementForm\records.db` (made-up invoices once had to be removed from
  it by hand). pytest is safe (conftest isolates them).
- Read the project memory (`MEMORY.md` and the files it points to) for the current state and known
  limits before starting.

## Step 0: Where things stand

1. `git status`, `git log --oneline -5`, the version in `minute_filler/__init__.py`, and the latest
   release: `gh release list --limit 3`.
2. Work may be waiting from an earlier session (built but not released, uncommitted changes). Look at the
   diff and include it. If you can't tell whether it was meant to ship, ask.
3. Run the tests once so you know the starting point.
4. Scan the whole repository, not just the changes, against the private words: older commits can hold a
   real name that was added before the word was on the list. Run privacy_scan.py with `--all`. A hit in
   the author's own credit lines (README contact, About box, installer publisher) is intended.

## Step 1: Bug sweep and fixes

Run the user's `bug-sweep` skill over this repository (Skill tool, `bug-sweep`). These project rules
apply on top of it:

- Look for logic errors **and race conditions**, in both the window (`minute_filler/gui/`) and the
  logic behind it. Hotspots: background reads and batches (`MainWindow.work`, `.filling`, `.gen`,
  `_loading`), "Generate all" running on copies of the jobs and settings, the records database and its
  CSV copies (`records.NUMBER_LOCK`, `_MIRROR_LOCK`), the run sheet (`runsheet._LOCK`, files open in
  Excel), and dialogs that wait for the user while other work arrives.
- Confirm each suspected bug with a small repro script in the scratchpad before fixing it.
- Every fix gets a regression test with made-up data that fails on the old code (in
  `tests/test_regressions.py`, or next to related tests).
- Questions of judgement (what is billed, how a form is laid out) are not bugs. List them for the
  user at the end instead of changing them.
- Finish with the full test suite passing.

## Step 2: Comments and docstrings review

Go through every module in `minute_filler/` (including `gui/` and `forms/`), `tools/` and `tests/`:

- Each module, class and public function has a docstring saying what it is for, and what goes in and
  comes out when that isn't obvious. Private helpers get one when the reason isn't obvious from the name.
- Docstrings and comments match the code as it is now: renamed parameters, changed behaviour, removed
  features and stale examples. Wrong comments are worse than none.
- Comments explain *why* (a court form's quirk, a Windows gotcha, a race), not what the next line does.
  Remove comments that only repeat the code.
- Keep the house style: plain, short English sentences, concrete examples in quotes ("'712345-2021' ->
  '712345/2021'"), and made-up names only.
- Don't change behaviour in this step. Run the tests afterwards.

## Step 3: Commit and push

1. **Version.** If the current version already has a GitHub release (`gh release view v<version>`),
   bump it: `tools/bump_version.py minor` when users get something new (a feature, a new setting, a
   changed result), else `tools/bump_version.py patch`. If that version has no release yet, keep it.
   (`tools/bump_version.py auto` does this check for a patch bump.)
2. Tests pass.
3. `git add -A`, then the privacy scan of what is staged:
   `.venv/Scripts/python.exe .claude/skills/update-the-app/scripts/privacy_scan.py`.
   It checks the staged diff against the real names, numbers and contact details listed in
   `samples_internal/` (`expected.json` and `private_words.txt`), and the names of the files and the text
   of Excel workbooks and PDFs being added. Fix every hit with made-up data and scan again. It can't read
   pictures: it lists them, so look at each new or changed image yourself.
   When work from real samples brought in new real names (witnesses, colleagues, firms), add them to
   `samples_internal/private_words.txt` (one per line).
4. Commit with a message that says what changed for the user, ending with the attribution line the
   session asks for. Then `git push origin main`.

## Step 4: Rebuild the installer

1. `.venv/Scripts/python.exe -m PyInstaller --noconfirm --clean YinItAgreementForm.spec` (in the
   background; it prints "Build complete!" at the end of its output).
2. Check the built program headless on made-up inputs:
   `.venv/Scripts/python.exe .claude/skills/update-the-app/scripts/exe_check.py`.
   It must say the right version, report no errors in the log, and make all four outputs.
3. Installer: `MSYS_NO_PATHCONV=1 "C:/Program Files (x86)/Inno Setup 6/ISCC.exe" /Qp
   /DMyAppVersion=X.Y.Z installer/YinItAgreementForm.iss`. The result is
   `dist/installer/YinItAgreementForm-Setup-X.Y.Z.exe`. Never change the `AppId` in the `.iss`.

## Step 5: Publish the release

1. Write release notes to a file in the scratchpad, following the earlier releases (`gh release view
   v<previous>`): a `## X.Y.Z: <headline>` title, then what changed **for the user** in plain words.
   Bold lead-ins, one short paragraph or a few bullets per change, made-up examples only, and the same
   "### Install" paragraph as before (with the new file name).
2. `gh release create vX.Y.Z dist/installer/YinItAgreementForm-Setup-X.Y.Z.exe --title
   "YinItAgreementForm X.Y.Z" --notes-file <notes>`.
3. The website's download button points at `releases/latest`, so it serves the new installer at once.
   Check with `gh release view vX.Y.Z` that the installer is attached.

## Step 6: Update the website

The site is `docs/index.html` (plus its pictures), served by GitHub Pages from `docs/` on `main`.

- If the release adds or changes something a user would notice, describe it in the site's own voice
  (short, friendly, second person) and in the right section. A new feature gets a `<section>` like the
  others. Remove anything that is no longer true.
- New screenshots use made-up data only: build them from the test helpers (`tests/helpers.py`,
  `tests/test_runsheet.py`). Render windows off screen (`QT_QPA_PLATFORM=offscreen`, and set
  `QT_QPA_FONTDIR=C:\Windows\Fonts` inside the script or the text comes out as boxes). Never call
  `MainWindow.fill()` from a script: it opens a dialog on the user's screen. Excel sheets can be
  printed to PDF through Excel's COM interface (PowerShell) and cropped with PyMuPDF.
- If nothing visible changed, leave the site alone and say so.
- Commit and push (after the privacy scan). Pages redeploys in a minute or two.

## Step 7: Update the README and internal notes

1. `README.md`: the feature list, the usage steps, each feature's section, the `--batch --outputs`
   line, the "How it works" module table and the build instructions should match the code.
2. `samples_internal/README.txt` (private): only if how the private samples are used has changed.
3. Docstrings at the top of the test files, when what they cover has changed.
4. Commit and push any of the above (privacy scan first).
5. Project memory: update the project state memory (version released, commit, what shipped, known
   limits, test count), the release-process memory (new gotchas found during this run) and their lines
   in `MEMORY.md`.

## Finish

Report to the user in a few lines:
- the version and release link: https://github.com/nono638/YinItAgreementForm/releases/tag/vX.Y.Z
- the bugs fixed, in plain words
- what changed in the docs and on the site: https://nono638.github.io/YinItAgreementForm/
- anything left for their decision
