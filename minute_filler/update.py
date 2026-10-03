"""Is there a newer version? The one time the app goes online.

Once a day (Settings.check_updates, on by default; Settings -> Options turns it off) the window asks GitHub for
the latest release of the app and, when its number is higher than this copy's, shows a line with a link to
download it. Nothing is installed by itself. The request names only the app and its version: nothing about
the user, the computer or any case is sent.
"""
from __future__ import annotations

import json
import re
import urllib.request

from . import __version__

RELEASES_PAGE = "https://github.com/nono638/DjinnItAgreementForm/releases"
LATEST_API = "https://api.github.com/repos/nono638/DjinnItAgreementForm/releases/latest"


def version_key(version: str) -> tuple[int, ...]:
    """'v1.10.2' -> (1, 10, 2), so that 1.10 counts as newer than 1.9; () when it holds no number."""
    return tuple(int(n) for n in re.findall(r"\d+", version)[:4])


def latest(timeout: float = 6) -> tuple[str, str]:
    """(version, page) of the latest release on GitHub: ("1.7.0", its release page). OSError when GitHub
    can't be reached; ValueError when the answer isn't what a release looks like."""
    req = urllib.request.Request(LATEST_API, headers={"User-Agent": f"DjinnItAgreementForm/{__version__}",
                                                      "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as answer:
        data = json.loads(answer.read(200_000).decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str) or not version_key(data["tag_name"]):
        raise ValueError("GitHub's answer names no version")
    page = data.get("html_url")
    # only ever a page of this project: the link is opened in the user's browser
    page = page if isinstance(page, str) and page.startswith(RELEASES_PAGE) else RELEASES_PAGE + "/latest"
    return data["tag_name"].lstrip("vV"), page


def newer(current: str = __version__, timeout: float = 6) -> tuple[str, str] | None:
    """(version, page) when the latest release is newer than `current`, else None (see latest for errors)."""
    version, page = latest(timeout)
    return (version, page) if version_key(version) > version_key(current) else None
