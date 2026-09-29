import pytest


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    """Keep tests away from the real %APPDATA% settings and rate sheets."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
