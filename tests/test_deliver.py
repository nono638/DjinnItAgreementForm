"""Generating the chosen outputs: agreements, MOFR and invoices (transcripts only), and their records; also
settings files from versions 3 and 4 brought up to date (the invoice speeds, and the old "offer every speed"
box, invoice_choice, now decided by the speeds ticked alone)."""
from pathlib import Path

import pytest

from minute_filler.batch import expand_paths, fill_jobs, group, read_docs
from minute_filler.deliver import NO_INVOICE, generate, ledger_for
from minute_filler.settings import Settings

from helpers import pat_settings, transcript_pdf


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return s


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    transcript_pdf(d / "Roe transcript.pdf")
    (d / "email.txt").write_text("From: someone@examplefirm.com\nSubject: minutes\n\nPlease send the minutes in "
                                 "Smith v Jones, Index No. 712222/2024, 5/22/2026, Judge Lopez.")
    return d


def jobs_of(folder, s):
    docs, errors = read_docs(expand_paths([str(folder)]), s)
    assert not errors
    return sorted(group(docs, s), key=lambda j: j.title())


def test_only_a_transcript_can_be_invoiced(folder, s):
    roe, smith = jobs_of(folder, s)
    assert roe.docs[0].regex.doc_kind == "transcript" and roe.transcript_pages() == 30
    assert roe.invoice_pages() == 30  # the transcript's own count fills the Pages field
    assert smith.docs[0].regex.doc_kind == "text" and smith.transcript_pages() == 0
    assert smith.invoice_pages() == 0
    assert smith.output_problems(["invoice"]) == [NO_INVOICE]
    roe.case.set("est_pages", "28")  # an edited page count is what gets billed
    assert roe.invoice_pages() == 28


def test_batch_makes_every_output_and_records_it(folder, s, tmp_path):
    roe, smith = jobs_of(folder, s)
    for a in roe.case.attorneys:
        a.checked = "Counsel" in (a.firm or "")
    fill_jobs([roe, smith], s, outputs=["agreement", "mofr", "invoice"])
    made = [p.name.split(" - ")[0] for p in roe.saved]
    assert made[:2] == ["Minute Agreement", "MOFR"] and made[2].startswith("Invoice ") and len(made) == 3
    assert not roe.error
    # the e-mail job gets its forms but no invoice, and says why
    assert [p.name.split(" - ")[0] for p in smith.saved] == ["Minute Agreement", "MOFR"]
    assert "no invoice" in smith.error

    ledger = ledger_for(s)
    assert sorted(a.kind for a in ledger.activity()) == ["agreement", "agreement", "invoice", "mofr", "mofr"]
    inv = ledger.invoices()[0]
    assert inv.pages == 30 and inv.firm.upper() == "COUNSEL & COUNSEL" and inv.case_name.startswith("Jane Roe")
    assert ledger.activity(kind="invoice")[0].invoice_no == inv.invoice_no
    assert (tmp_path / "records" / "invoices.csv").exists()

    # what the app made is not read back in as input, but an attorney's own invoice would be
    (tmp_path / "out" / "Invoice from a firm.txt").write_text("Invoice\nTo: Example")
    again = [Path(p).name for p in expand_paths([str(tmp_path / "out")])]
    assert again == ["Invoice from a firm.txt"]


def test_generate_without_pages_refuses_an_invoice(s, tmp_path):
    from minute_filler.models import CaseInfo
    with pytest.raises(ValueError):
        generate(CaseInfo(), s, tmp_path, ["invoice"])


def test_settings_v3_upgrade(tmp_path):
    """A version 3 settings file loads as the current version: unknown outputs are dropped and the invoice
    defaults kick in."""
    import json
    s = Settings()
    s.path.write_text(json.dumps({"settings_version": 3, "outputs": ["agreement", "bogus", "mofr"],
                                  "invoice_speeds": "Regular"}), encoding="utf-8")
    loaded = Settings.load()
    assert loaded.outputs == ["agreement", "mofr"] and loaded.settings_version == Settings.settings_version
    assert loaded.invoice_speeds == ["Regular", "Expedited"]  # wrong type keeps the default
    assert loaded.turnaround("Expedite") == "1 week from receipt of payment."


def test_settings_v4_offer_every_speed_off(tmp_path):
    """Version 4 had an "offer every speed" box; off, invoices billed the speed chosen under Order. That is now
    no speed ticked (not the default speed, which would bill an Expedited job at the Regular price)."""
    import json
    s = Settings()
    s.path.write_text(json.dumps({"settings_version": 4, "invoice_choice": False, "default_delivery": "Regular",
                                  "invoice_speeds": ["Regular", "Expedited", "Daily"]}), encoding="utf-8")
    loaded = Settings.load()
    assert loaded.invoice_speeds == []
    from minute_filler.invoice_calc import quotes_for
    assert [q.speed for q in quotes_for(10, 1, loaded.sheet(), loaded, "Expedited")] == ["Expedite"]
    s.path.write_text(json.dumps({"settings_version": 4, "invoice_choice": True, "invoice_speeds": []}),
                      encoding="utf-8")
    assert Settings.load().invoice_speeds == []  # none ticked: the job's own speed (see invoice_calc.offered)
