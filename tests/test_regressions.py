"""Bugs found in the sweeps (1.1.1 and later), each with the case that showed it (fictional data)."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from minute_filler import deliver
from minute_filler.batch import Job, fill_jobs, group, read_docs, remerge
from minute_filler.deliver import generate
from minute_filler.invoice import InvoiceOpts
from minute_filler.models import Attorney, to_int
from minute_filler.mofr import build_values
from minute_filler.records import Invoice, Ledger
from minute_filler.settings import Settings

from helpers import ROE, make_case, pat_settings, text_doc, transcript_pdf

APPEARANCE = """SUPREME COURT OF THE STATE OF NEW YORK
COUNTY OF QUEENS
Index No. 712345/2021
Jane Roe
-against-
X.Y. Holding Corporation

A P P E A R A N C E S:

{firm}
Attorneys for the {side}
100 Main Street
Anytown, NY 10000
BY: {name}, ESQ.
"""


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return s


def widgets(path):
    """The first page's form fields {name: value}."""
    with pymupdf.open(path) as doc:
        return {w.field_name: w.field_value for w in doc[0].widgets()}


def test_mofr_without_a_transcript_keeps_the_estimated_pages(s, tmp_path):
    # the window always passes the invoice options; without a transcript their page count is 0
    case = make_case({**ROE, "est_pages": "30"})
    ledger = Ledger(tmp_path / "r.db")
    mofr = generate(case, s, tmp_path / "out", ["mofr"], InvoiceOpts(0), ledger)[0]
    assert widgets(mofr)["Text Field12"] == "30"
    assert ledger.activity()[0].pages == 30


def test_files_made_before_a_failure_are_reported(s, tmp_path, monkeypatch):
    def locked(*a, **kw):
        raise PermissionError("the file is open")

    monkeypatch.setattr(deliver, "fill_mofr", locked)
    case = make_case(ROE)
    with pytest.raises(PermissionError) as err:
        generate(case, s, tmp_path / "out", ["agreement", "mofr"], ledger=Ledger(tmp_path / "r.db"))
    assert [p.name.split(" - ")[0] for p in err.value.made] == ["Minute Agreement"]

    job = Job(case=case, saved=[tmp_path / "from an earlier run.pdf"])
    done = fill_jobs([job], s, outputs=["agreement", "mofr"])
    assert len(job.saved) == 1 and job.saved == done and "PermissionError" in job.error


def test_invoice_numbers_without_seq_still_differ(tmp_path):
    ledger = Ledger(tmp_path / "r.db")
    numbers = []
    for pattern in ("INV", "INV", "{year}", "{seq.nonsense}"):
        got = []
        t = threading.Thread(target=lambda: got.append(ledger.next_invoice_no(pattern)), daemon=True)
        t.start()
        t.join(5)
        assert got, f"numbering with the pattern {pattern!r} never ends"
        no, year, seq = got[0]
        ledger.add_invoice(Invoice(no, f"{year}-01-01"), year, seq)
        numbers.append(no)
    assert len(set(numbers)) == 4 and numbers[0] == "INV-0001" and numbers[1] == "INV-0002"


def test_attorney_ticks_do_not_leak_between_documents(s):
    one = text_doc(s, APPEARANCE.format(firm="DOE & ROE LLP", side="Plaintiff", name="JOHN DOE"), "a.txt")
    two = text_doc(s, APPEARANCE.format(firm="SMITH LAW GROUP", side="Defendant", name="MARY SMITH"), "b.txt")
    job = Job(docs=[one, two])
    remerge(job, s)
    # a cover page lists who appeared, not who ordered: with two of them, neither is ticked
    assert [(a.name, a.checked) for a in job.case.attorneys] == [("John Doe", False), ("Mary Smith", False)]
    job.case.attorneys[0].email = "typed@example.com"
    assert not one.regex.attorneys[0].email  # what was read from the document stays as read

    mail = text_doc(s, "From: John Doe <jdoe@doeroe.example>\nSubject: minutes\n\nRoe v. X.Y. Holding, "
                       "Index No. 712345/2021, 6/3/2026 please.")
    job = Job(docs=[one, mail])
    remerge(job, s)
    assert job.case.attorneys[0].email == "jdoe@doeroe.example" and job.case.attorneys[0].checked
    job.docs.remove(mail)
    remerge(job, s)
    assert job.case.attorneys[0].email == ""  # the e-mail's address left with the e-mail


def test_criminal_mofr_title_without_the(s):
    """Each way of writing "(The) People ... v." is left out of a criminal MOFR's title; "People's Bank" is not."""
    s.mofr_division = "criminal"
    for name in ("People of the State of New York v. John Doe", "The People of the State of New York v. John Doe",
                 "People v. John Doe", "The People, etc. v. John Doe"):
        assert build_values(make_case({**ROE, "case_name": name}), s)["title"] == "John Doe"
    assert build_values(make_case({**ROE, "case_name": "People's Bank v. John Doe"}), s)["title"].startswith("People's")


def test_two_transcripts_in_one_job_are_billed_together(s, tmp_path):
    s.batch_combine_dates = True
    docs, errors = read_docs([str(transcript_pdf(tmp_path / "day1.pdf", 30)),
                              str(transcript_pdf(tmp_path / "day2.pdf", 20))], s)
    job, = group(docs, s)
    assert job.case.get("est_pages") == "50" and job.invoice_pages() == 50
    job.case.set("est_pages", "48")  # a number typed by the user still wins
    remerge(job, s)
    assert job.invoice_pages() == 48


def test_pages_with_a_thousands_comma():
    assert to_int("1,200") == 1200 and to_int("") == 0 and to_int("about 30", 7) == 7


def test_settings_with_entries_of_the_wrong_kind_still_load():
    Settings().path.write_text(json.dumps({"outputs": [["x"], "mofr"], "invoice_speeds": [1, "Daily"],
                                           "invoice_turnaround": {"Regular": 3, "Daily": "tomorrow"},
                                           "invoice_index_threshold": 0}), encoding="utf-8")
    s = Settings.load()
    assert s.outputs == ["mofr"] and s.invoice_speeds == ["Daily"]
    assert s.invoice_turnaround == {"Daily": "tomorrow"} and s.invoice_index_threshold == 1


def test_invoice_text_rows_with_a_list_or_dict_for_where_or_when_are_dropped():
    # a hand-edited file: "where"/"when" that aren't strings used to crash the load (unhashable)
    rows = [{"where": ["footer"], "when": "always", "text": "x"}, {"where": "footer", "when": {"a": 1}, "text": "y"},
            {"where": "footer", "when": "always", "text": "Thank you."}]
    Settings().path.write_text(json.dumps({"invoice_texts": rows}), encoding="utf-8")
    s = Settings.load()
    assert s.invoice_texts == [{"where": "footer", "when": "always", "text": "Thank you."}]


def test_bad_file_name_pattern_falls_back(s, tmp_path):
    from minute_filler.fill import output_name
    case = make_case(ROE)
    for pattern in ("{case.nonsense}", "{case[0]}", "{nonsense}", "{"):
        s.filename_pattern = pattern
        assert output_name(case, Attorney(name="John Doe"), s).endswith(".pdf")


# ------------------------------------------------------------------ found in the run sheet sweep (1.2.0)

def _roe_job(s, tmp_path, name="Transcript 6-3-2026 Roe v Poe.pdf", **kw):
    """The job of one fictional Roe v. Poe transcript (see test_runsheet.transcript for kw)."""
    from test_runsheet import transcript
    docs, errors = read_docs([str(transcript(tmp_path / name, **kw))], s)
    job, = group(docs, s)
    return job


def test_a_locked_run_sheet_makes_nothing_so_trying_again_bills_once(s, tmp_path, monkeypatch):
    import minute_filler.runsheet as rs
    s.runsheet_dir = str(tmp_path / "sheets")
    job = _roe_job(s, tmp_path)
    ledger = Ledger(tmp_path / "r.db")
    real = rs._save

    def locked(wb, path):
        raise PermissionError(f"{path.name} is open in another program - close it in Excel and try again")
    monkeypatch.setattr(rs, "_save", locked)
    with pytest.raises(PermissionError) as err:
        generate(job.case, s, tmp_path / "out", ["invoice", "runsheet"], job.invoice_opts(), ledger,
                 runsheet=job.runsheet_opts(s))
    assert err.value.made == [] and ledger.invoices() == []  # the run sheet is written first
    monkeypatch.setattr(rs, "_save", real)
    made = generate(job.case, s, tmp_path / "out", ["invoice", "runsheet"], job.invoice_opts(), ledger,
                    runsheet=job.runsheet_opts(s))
    assert len(made) == 2 and len(ledger.invoices()) == 1
    # the same takes again: the run sheet is left as it was, and neither listed nor recorded again
    sheet = job.runsheet_opts(s)
    made = generate(job.case, s, tmp_path / "out", ["runsheet"], job.invoice_opts(), ledger, runsheet=sheet)
    assert made == [] and sheet.path and sheet.added == 0
    logged = ledger.activity("runsheet")
    assert len(logged) == 1 and logged[0].pages == 10  # the pages of the takes added


def test_cases_with_one_name_get_their_own_run_sheets(s, tmp_path):
    """Two index numbers under one case name, each told "start a new run sheet": two run sheets."""
    s.runsheet_dir = str(tmp_path / "sheets")
    jobs = [_roe_job(s, tmp_path), _roe_job(s, tmp_path, "Transcript 6-4-2026 Roe v Poe.pdf", index="700999/2022",
                                             day="June 4, 2026")]
    for n, j in enumerate(jobs):
        j.runsheet_to, j.runsheet_group = "", n
    fill_jobs(jobs, s, outputs=["runsheet"])
    assert len(list((tmp_path / "sheets").glob("*.xlsx"))) == 2
    # asked about together (one case): one run sheet
    s.runsheet_dir = str(tmp_path / "together")
    for j in jobs:
        j.runsheet_group = 0
    fill_jobs(jobs, s, outputs=["runsheet"])
    assert len(list((tmp_path / "together").glob("*.xlsx"))) == 1
    # without the window and Settings saying "start a new one": each day its own
    s.runsheet_dir, s.runsheet_existing = str(tmp_path / "new"), "new"
    days = [_roe_job(s, tmp_path), _roe_job(s, tmp_path, "Transcript 6-4-2026 Roe v Poe.pdf", day="June 4, 2026")]
    fill_jobs(days, s, outputs=["runsheet"])
    assert len(list((tmp_path / "new").glob("*.xlsx"))) == 2


def test_two_digit_year_in_an_index_number():
    from minute_filler.batch import ident, same_case
    a, b = make_case({"index_no": "712345/21"}), make_case({"index_no": "712345-2021"})
    assert ident(a).index == ident(b).index == "712345/2021" and same_case(ident(a), ident(b))


def test_reporter_initials_in_the_settings_file_are_tidied():
    s = Settings()
    s.reporters = {"D. S.": "Dana", "KL": "Kim", " . ": "nobody"}
    s.save()
    assert Settings.load().reporter_names() == {"ds": "Dana", "kl": "Kim"}


def test_window_icon_comes_from_the_package():
    import minute_filler
    from minute_filler.main import app_icon
    assert app_icon() == Path(minute_filler.__file__).resolve().parent / "assets" / "app.ico"
    assert app_icon().is_file()


def test_unknown_output_names_are_refused(tmp_path, capsys):
    from minute_filler.main import batch
    assert batch(str(tmp_path / "out"), [str(tmp_path)], ["agreement", "runsheets"]) == 2
    err = capsys.readouterr().err
    assert "runsheets" in err and "agreement, mofr, invoice, runsheet" in err
    assert not (tmp_path / "out" / "batch.json").exists()


def test_run_sheet_names_are_kept_out_of_the_log():
    from minute_filler.log import describe
    text = describe(PermissionError("June 2026 712345-2021 Jane Roe v. Sam Poe - Run Sheet.xlsx is open in another "
                                    "program - close it in Excel and try again"))
    assert "Roe" not in text and "<file.xlsx>" in text and "close it in Excel" in text


@pytest.mark.ollama
def test_the_ollama_check_closes_its_connection_and_skips_the_proxy(monkeypatch):
    import ollama
    from minute_filler.extract_llm import OllamaExtractor
    made = []

    class Client:  # stands in for ollama.Client: nothing is contacted
        def __init__(self, host=None, **kw):
            self.kw, self.closed = kw, False
            made.append(self)

        def list(self):
            return SimpleNamespace(models=[SimpleNamespace(model="gemma3:4b")])

        def close(self):
            self.closed = True
    monkeypatch.setattr(ollama, "Client", Client)
    s = Settings()
    s.ollama_host = "http://localhost:11434"
    assert OllamaExtractor(s).installed_models() == ["gemma3:4b"]
    assert made[0].closed and made[0].kw["trust_env"] is False  # Windows' proxy settings are not used
    s.ollama_host = "http://192.168.1.20:11434"
    OllamaExtractor(s).installed_models()
    assert "trust_env" not in made[1].kw and made[1].closed


def test_tests_never_ask_a_real_ollama():
    from minute_filler.extract_llm import OllamaExtractor
    ok, msg = OllamaExtractor(Settings()).status()
    assert not ok and "not running" in msg


def test_auto_version_bump_asks_github(tmp_path, monkeypatch):
    """A version counts as released when GitHub has its release, or (without the gh tool) when its installer
    was built here."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("bump_version", Path(__file__).parent.parent / "tools" /
                                                  "bump_version.py")
    bv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bv)
    monkeypatch.setattr(bv, "ROOT", tmp_path)
    answers = {"v1.2.0": (0, ""), "v1.2.1": (1, "release not found")}
    monkeypatch.setattr(bv.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=answers[cmd[3]][0], stderr=answers[cmd[3]][1]))
    assert bv.released("1.2.0") and not bv.released("1.2.1")

    def no_gh(cmd, **kw):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(bv.subprocess, "run", no_gh)  # without gh: was its installer built here?
    assert not bv.released("1.2.1")
    (tmp_path / "dist" / "installer").mkdir(parents=True)
    (tmp_path / "dist" / "installer" / "YinItAgreementForm-Setup-1.2.1.exe").write_text("")
    assert bv.released("1.2.1")


def test_settings_ok_keeps_speeds_named_otherwise_on_a_rate_sheet(qt):
    """A speed of the user's own rate sheet, ticked under Speeds offered in the Order card, has no box in
    Settings → Invoice: saving the Settings must not untick it."""
    from minute_filler.gui.dialogs import SettingsDialog
    s = pat_settings()
    s.invoice_speeds = ["Regular", "2-Day Rush Plus"]
    SettingsDialog(s, None).accept()
    assert s.invoice_speeds == ["2-Day Rush Plus", "Regular"]


def test_the_invoice_spreadsheet_prices_as_the_app_does():
    """The shipped template charges one index (and judge's index) split between the parties, as the app does by
    default (a Setup option charges an index to each), and rounds each party's share up to the cent."""
    from openpyxl import load_workbook
    from minute_filler.invoice import TEMPLATE
    wb = load_workbook(TEMPLATE)
    calc, setup = wb["Calculation"], wb["Setup"]
    index, each = calc["I2"].value, calc["L2"].value
    assert "IF(IndexEach,Parties,1)" in index and each.startswith("=ROUNDUP(") and "ROUND(K2/Parties" in each
    names = {n: wb.defined_names[n].attr_text for n in ("IndexEach", "Turnaround")}
    row = int(names["IndexEach"].rsplit("$", 1)[1])
    assert setup[f"B{row}"].value is False  # one index, split, unless the user says otherwise
    first, last = (int(x.rsplit("$", 1)[1]) for x in names["Turnaround"].split(":"))
    assert setup[f"A{first - 1}"].value == "TURNAROUND WORDING" and last - first == 3


def test_generate_asks_about_the_case_as_whose_pages_left_it(make_window, monkeypatch, tmp_path):
    # Whose pages... (asked first) may merge the job again: the questions are about the case as it is now
    from minute_filler.gui import main_window
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir = str(tmp_path / "out")
    s.outputs = ["agreement"]
    win = make_window(s)
    win.case = make_case(ROE)
    win._show_case()
    merged = make_case({**ROE, "case_name": ""})  # (the case name blank after the merge)

    def whose_pages(job, outputs):
        job.case = merged
        return outputs
    monkeypatch.setattr(win, "_without_unchecked_invoice", whose_pages)
    asked, made = [], []

    class Ask:
        Accepted = 1

        def __init__(self, questions, attorneys, parent):
            asked.append([key for key, _, _ in questions])

        def exec(self):
            return 0  # (Cancel)
    monkeypatch.setattr(main_window, "ClarifyDialog", Ask)
    monkeypatch.setattr(main_window, "generate", lambda *a, **k: made.append(a) or [])
    monkeypatch.setattr(win, "_saved_box", lambda *a, **k: None)
    win.fill()
    assert asked == [["case_name"]] and not made


def test_selftest_checks_the_fuzzy_search(tmp_path):
    """--selftest says whether the Records' Fuzzy and Regex searches, HEIC photos, the look for a newer version,
    printing and The math's Save as PDF work (the release check runs it on the build)."""
    from minute_filler.main import selftest
    assert selftest(str(tmp_path / "st"), []) == 0
    report = json.loads((tmp_path / "st" / "selftest.json").read_text(encoding="utf-8"))
    assert report["fuzzy_search"] is True and report["regex_search"] is True and report["heic_photos"] is True
    assert report["update_check"] is True and report["printing"] is True and report["math_pdf"] is True


def test_an_iphone_heic_photo_is_read(tmp_path, monkeypatch):
    """A .heic photo opens (pillow-heif), upright, and is handed to the text recognition like a JPG.
    1.6.0 said "this doesn't look like a valid .heic file" because pillow-heif wasn't installed."""
    from PIL import Image
    from minute_filler import ingest
    seen = []
    monkeypatch.setattr(ingest, "ocr_image", lambda img: seen.append(img.size) or "SUPREME COURT")
    path = tmp_path / "IMG_0001.heic"
    Image.new("RGB", (120, 80), "white").save(path, format="HEIF")
    ing = ingest.ingest_file(path)
    assert seen == [(120, 80)] and ing.text == "SUPREME COURT" and ing.images


def test_a_heic_photo_without_pillow_heif_says_what_is_missing(tmp_path, monkeypatch):
    """Without pillow-heif, a HEIC photo gets a message saying so and what to do, not "not a valid file"."""
    import pytest
    from minute_filler import ingest
    monkeypatch.setattr(ingest, "HEIF_OK", False)
    path = tmp_path / "IMG_0002.HEIC"
    path.write_bytes(b"\x00\x00\x00\x18ftypheic")
    with pytest.raises(ValueError, match="can't read HEIC photos.*save it as JPG"):
        ingest.ingest_file(path)


def test_browse_offers_every_kind_of_file_a_folder_takes(qt):
    from minute_filler.batch import BATCH_EXT
    from minute_filler.gui.main_window import FILE_FILTER
    documents, everything = FILE_FILTER.split(";;")
    assert all(f"*{e}" in documents.split("(")[1] for e in BATCH_EXT) and everything == "All files (*.*)"
    assert {"*.htm", "*.html", "*.gif", "*.heif"} <= set(documents.strip(")").split("(")[1].split())


# ------------------------------------------------------------------ 2.0.0 sweep
def test_the_rate_follows_a_speed_the_invoice_no_longer_offers():
    """A document says Regular and its rate; the invoice offers Expedited and Daily, so the agreement names
    Expedite: the rate stayed Regular's $4.30. It follows the speed named ($5.40)."""
    from minute_filler.merge import merge
    from minute_filler.models import Extraction, SRC_REGEX
    s = pat_settings()
    s.invoice_speeds = ["Expedited", "Daily"]
    ex = Extraction()
    ex.add("delivery", "Regular", SRC_REGEX, 0.7)
    ex.add("rate", s.rate_for("Regular"), SRC_REGEX, 0.8)
    case = merge([ex], s)
    assert case.get("delivery") == "Expedite" and case.get("rate") == s.rate_for("Expedite") == "5.40"


def test_copies_read_from_a_document_stay_with_nobody_ticked():
    """"3 copies" read from a document was replaced by the default (1) while no attorney was ticked. Now it
    stays until someone is ticked; then the firms ticked decide."""
    from minute_filler.merge import merge, refresh_copies
    from minute_filler.models import Extraction, SRC_REGEX
    s = pat_settings()
    ex = Extraction()
    ex.add("copies", "3", SRC_REGEX, 0.8)
    case = merge([ex], s)
    assert case.get("copies") == "3"
    case.attorneys = [Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=True)]
    refresh_copies(case, s)
    assert case.get("copies") == "1"  # the firms ticked decide once there are some
    case.attorneys[0].checked = False
    refresh_copies(case, s)
    assert case.get("copies") == (s.default_copies or "")  # (a number of the firms ticked isn't kept)


def test_a_run_sheet_one_reporters_case_doesnt_need_isnt_reported_missing(tmp_path):
    """Generate all with the run sheet ticked for another case: a day one reporter wrote said "no run sheet:
    the Pages field says 0"."""
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    path = transcript_pdf(tmp_path / "one.pdf", pages=6, initials=["pr"] * 6)
    docs, _ = read_docs([str(path)], s)
    job, = group(docs, s)
    for a in job.case.attorneys:
        a.checked = True
    job.runsheet_unneeded = True  # (as MainWindow._auto_runsheet sets it)
    assert job.makeable(["agreement", "runsheet"]) == ["agreement"] and job.unneeded("runsheet")
    fill_jobs([job], s, batch=[job], outputs=["agreement", "runsheet"])
    assert job.saved and job.error == ""


# ------------------------------------------------------------------ the 2.1 sweep

def test_a_firm_ticked_again_without_a_run_gets_no_agreement(s, tmp_path):
    """Excerpts... gave Sam's firm no run, and the user ticked it again in the attorney table: Generate made it a
    minute agreement saying 0 pages (Generate all, for the days of a case together, made none)."""
    path = transcript_pdf(tmp_path / "Roe.pdf", 30)
    docs, _ = read_docs([str(path)], s)
    job, = group(docs, s)
    alex = Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=True)
    sam = Attorney(name="Sam Advocate", firm="Advocate LLP", checked=True)
    job.case.attorneys, job.att_touched = [alex, sam], True
    job.portions = [(10, [alex.key()]), (30, ["-"])]
    assert job.portions_problem() == "" and job.form_count() == 1
    saved = fill_jobs([job], s, outputs=["agreement"])
    assert len(saved) == 1 and "Advocate" not in saved[0].name


def test_two_rows_of_the_users_table_merged_into_one_firm_keep_the_tick(s):
    """A document said Dana Smith is of Counsel & Counsel: her row (unticked) and the firm's (ticked) became one,
    unticked, so the firm got no agreement or invoice; and a new document's ticked sender stayed ticked."""
    from minute_filler.batch import Doc
    from minute_filler.ingest import ingest_text
    from minute_filler.models import Extraction

    def after(rows, found):
        job = Job()
        job.docs = [Doc(ingest_text("hello", "x.txt"), Extraction(attorneys=found, doc_kind="email"))]
        job.case.attorneys, job.att_touched = rows, True
        remerge(job, s)
        return {a.firm: a.checked for a in job.case.attorneys}

    firm = "Counsel & Counsel, LLP"
    got = after([Attorney(name="Dana Smith", checked=False), Attorney(name="Alex B. Counsel", firm=firm)],
                [Attorney(name="Dana Smith", firm=firm, checked=False)])
    assert got == {firm: True}
    got = after([Attorney(name="Dana Smith", checked=True), Attorney(name="Alex B. Counsel", firm=firm, checked=False)],
                [Attorney(name="Dana Smith", firm=firm, checked=False),
                 Attorney(name="Sam Poe", firm="Poe Law PLLC", checked=True)])
    assert got == {firm: True, "Poe Law PLLC": False}


def test_an_old_record_naming_attorneys_with_esq_opens_with_its_excerpts_on_the_firm():
    """A 2.0 record keyed its Excerpts... rows by each attorney's name ('dana smith esq'): merged into one firm row,
    the name lost its "Esq." and the rows kept the old keys, so the reopened day's invoice was held."""
    from minute_filler.batch import one_row_per_firm
    job = Job()
    job.case.attorneys = [Attorney(name="Dana Smith, Esq.", firm="Counsel & Counsel, LLP"),
                          Attorney(name="Alex B. Counsel, Esq.", firm="Counsel & Counsel, LLP"),
                          Attorney(name="Mr. Advocate")]
    job.portions = [(10, ["dana smith esq"]), (30, ["alex b counsel esq", "mr advocate"])]
    one_row_per_firm(job)
    assert job.portions == [(10, ["counsel and counsel"]), (30, ["counsel and counsel", "mr advocate"])]
    assert job.portions_problem() in ("", "it is for one day, and this job no longer is")  # (no pages here)


def test_accented_firm_names_are_one_firm_and_other_scripts_have_a_key():
    from minute_filler.models import firm_key
    assert firm_key("Ñandú & Pingüino, LLP") == firm_key("NANDU AND PINGUINO LLP") == "nandu and pinguino"
    assert Attorney(firm="法律事务所").key() == "法律事务所"  # (it was "": no invoice, not counted as a party)


def test_a_firm_renamed_after_a_stopped_run_is_not_billed_twice():
    job = Job()
    job.invoiced_keys = ["smith law", "smith law@ds", "other"]
    job.rename_in_portions("smith law", "smith law group")
    assert job.invoiced_keys == ["smith law group", "smith law group@ds", "other"]


def test_another_reporters_invoice_works_on_a_case_of_its_own(s, tmp_path):
    """billed_by's copy shared the job's attorney rows: same_entries filled a firm into them while renaming only
    the copy's Excerpts... rows, and the day itself was then held."""
    path = transcript_pdf(tmp_path / "Roe.pdf", 30, initials=["pr"] * 10 + ["ds"] * 20)
    docs, _ = read_docs([str(path)], s)
    job, = group(docs, s)
    job.case.attorneys = [Attorney(name="Dana Smith", firm="Smith Law", checked=True)]
    job.page_basis = {job.transcripts()[0].key(): ["me", "ds"]}
    copy = job.billed_by("ds")
    assert copy.case is not job.case and copy.case.attorneys[0] is not job.case.attorneys[0]


def test_the_same_firm_on_two_days_is_one_column_of_excerpts():
    from minute_filler.excerpts import firms_of
    day1, day2 = Job(), Job()
    day1.case.attorneys = [Attorney(name="Dana Smith", firm="Smith Law", checked=True)]
    day2.case.attorneys = [Attorney(name="Dana Smith", checked=True)]
    assert [a.key() for a in firms_of([day1, day2])] == ["smith law"]


def test_an_invoice_the_records_cant_take_is_not_left_on_disk(s, tmp_path, monkeypatch):
    """The PDF was saved, then the records refused it (database locked): its number was given again next time,
    so two PDFs had one number."""
    import sqlite3
    from minute_filler.invoice import make_invoice
    atty = Attorney(name="Alex B. Counsel", firm="Counsel & Counsel")
    case = make_case({**ROE, "est_pages": "30"}, attorneys=[atty])
    ledger = Ledger(tmp_path / "r.db")

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(ledger, "add_invoice", locked)
    with pytest.raises(sqlite3.OperationalError):
        make_invoice(case, atty, s, tmp_path / "out", InvoiceOpts(30), ledger)
    assert not list((tmp_path / "out").glob("*.pdf"))


def test_one_csv_copy_that_cant_be_written_doesnt_stop_the_other(tmp_path):
    ledger = Ledger(tmp_path / "r.db")
    (tmp_path / "copies" / "invoices.csv").mkdir(parents=True)  # (it can't be replaced, as when open in Excel)
    with pytest.raises(OSError):
        ledger.export_csv(tmp_path / "copies")
    assert (tmp_path / "copies" / "activity.csv").is_file()
    assert not list((tmp_path / "copies").glob(".*.tmp"))


def test_a_blank_payment_is_refused(tmp_path):
    """It was recorded as $0.00 paid."""
    with pytest.raises(ValueError):
        Ledger(tmp_path / "r.db").mark_paid("2026-0001", "Regular", "")


def test_batch_without_files_says_how_and_opens_no_window(monkeypatch, tmp_path):
    """--batch OUTDIR with nothing after it opened the window, which read OUTDIR as a folder of documents."""
    import sys
    from minute_filler import main
    monkeypatch.setattr(sys, "argv", ["YinItAgreementForm", "--batch", str(tmp_path)])
    assert main.main() == 2
    monkeypatch.setattr(sys, "argv", ["YinItAgreementForm", "--batch", str(tmp_path), "--outputs"])
    assert main.main() == 2


def test_the_swirl_follows_the_layout_while_it_lingers(qt):
    """After a read the swirl goes on a while; a smaller layout (or another zoom) meanwhile was left for later, so
    the window measured the big one, and a zoom cut a click's swirl short."""
    from minute_filler.gui.main_window import DropZone
    from minute_filler.gui.zoom import z
    d = DropZone(lambda *a: None, lambda *a: None, lambda *a: None, lambda: None)
    d.show()
    d.linger_ms = 5000
    d.set_mood("working")
    d.set_mood("done")
    assert d._settle.isActive()
    d.set_compact(True)
    assert not d.sub.isVisible() and d.minimumHeight() == z(170) and d.mood == ("working", z(150))
    d.set_compact(False)
    d._settle.stop()
    d.set_mood("done")
    d.encore()
    d.rezoom()
    assert d._settle.isActive() and d.mood[0] == "working" and d.wanted == "done"
    d.deleteLater()
