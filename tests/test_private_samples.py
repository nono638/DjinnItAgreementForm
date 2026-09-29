"""Runs every document in samples_internal/ (git-ignored, for real documents).

Every PDF, image, .eml, .txt or .docx dropped in that folder is automatically checked: it must be read,
extracted, merged and filled into both forms without errors.

To also check the extracted values, add an entry to samples_internal/expected.json:

    {
      "_profile": {"name": "Your Name", "email": "you@example.com", "phone": "(555) 010-0000"},
      "Some transcript.pdf": {
        "fields":            {"index_no": "700001/2026", "judge": "Maria T. Alvarez"},
        "fields_startswith": {"case_name": "Jane Roe v."},
        "proc_types":        ["Trial"],
        "attorneys":         ["Alex B. Counsel", "Sam Advocate"],
        "attorney_fields":   {"Alex B. Counsel": {"firm": "Counsel & Counsel"}}
      }
    }

- "fields": exact best value per field (see models.FIELD_KEYS)
- "proc_types": proceeding types that must be detected
- "attorneys": the exact set of attorneys found (name, or firm when there is no name),
  leaving out "Unrepresented" / "No one appeared" entries
- "_profile": your own details, so they are never mistaken for an attorney
"""
import json
from pathlib import Path

import pytest

from minute_filler.extract_regex import RegexExtractor
from minute_filler.fill import fill_all
from minute_filler.ingest import ingest_file, ocr_available
from minute_filler.merge import merge
from minute_filler.settings import Profile, Settings

PRIVATE = Path(__file__).resolve().parent.parent / "samples_internal"
SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff", ".bmp", ".webp", ".eml", ".txt", ".docx"}
IMAGES = {".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff", ".bmp", ".webp"}


def _files():
    if not PRIVATE.is_dir():
        return []
    return sorted(p for p in PRIVATE.iterdir()
                  if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.lower().startswith("readme"))


def _expected() -> dict:
    f = PRIVATE / "expected.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _best(ex, key):
    cands = sorted(ex.fields.get(key, []), key=lambda c: -c.confidence)
    return cands[0].value if cands else ""


FILES = _files()


@pytest.mark.skipif(not FILES, reason="no documents in samples_internal/")
@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_private_sample(path, tmp_path):
    if path.suffix.lower() in IMAGES and not ocr_available():
        pytest.skip("Windows OCR not available")
    expected = _expected()
    profile = Profile(**expected.get("_profile", {}))
    s = Settings()
    s.profile = profile

    ing = ingest_file(path)
    ex = RegexExtractor(profile).extract(ing)
    case = merge([ex], s)
    for choice in ("clean", "original"):  # both forms must fill without errors
        s.form_choice = choice
        for pdf in fill_all(case, s, tmp_path / choice):
            assert pdf.exists() and pdf.stat().st_size > 10_000

    exp = expected.get(path.name)
    if not exp:
        return
    for key, want in exp.get("fields", {}).items():
        assert _best(ex, key) == want, key
    for key, want in exp.get("fields_startswith", {}).items():
        assert _best(ex, key).startswith(want), key
    for ptype in exp.get("proc_types", []):
        assert ex.proc_types.get(ptype, 0) >= 0.6, ptype
    real = [a for a in ex.attorneys if not a.is_placeholder()]
    if "attorneys" in exp:
        assert {a.name or a.firm for a in real} == set(exp["attorneys"])
    for who, attrs in exp.get("attorney_fields", {}).items():
        a = next(a for a in real if (a.name or a.firm) == who)
        for k, v in attrs.items():
            assert getattr(a, k) == v, f"{who}.{k}"
