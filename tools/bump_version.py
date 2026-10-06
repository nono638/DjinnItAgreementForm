"""Changes the version number (the one place it lives: minute_filler/__init__.py).

    python tools/bump_version.py            show the current version
    python tools/bump_version.py patch      1.2.3 -> 1.2.4   (small fixes)
    python tools/bump_version.py minor      1.2.3 -> 1.3.0   (new features)
    python tools/bump_version.py major      1.2.3 -> 2.0.0
    python tools/bump_version.py 1.4.0      set it exactly
    python tools/bump_version.py auto       patch bump, but only if the current version was already
                                      released on GitHub (gh release view v<version>); without
                                      gh, if an installer for it was already built. A version
                                      built but never released is built again, not skipped.
                                      (What build_installer.bat runs.)
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "minute_filler" / "__init__.py"
PATTERN = re.compile(r'(__version__\s*=\s*")(\d+)\.(\d+)\.(\d+)(")')


def current() -> tuple[int, int, int]:
    """The version in minute_filler/__init__.py as (major, minor, patch); exits when there is none."""
    m = PATTERN.search(INIT.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f'No __version__ = "X.Y.Z" found in {INIT}')
    return int(m[2]), int(m[3]), int(m[4])


def write(v: tuple[int, int, int]) -> str:
    """Writes v into minute_filler/__init__.py; returns it as text ("1.2.4")."""
    text = INIT.read_text(encoding="utf-8")
    new = PATTERN.sub(lambda m: f'{m[1]}{v[0]}.{v[1]}.{v[2]}{m[5]}', text, count=1)
    INIT.write_text(new, encoding="utf-8", newline="")  # keep the file's own line endings
    return ".".join(map(str, v))


def bump(what: str, v: tuple[int, int, int]) -> tuple[int, int, int]:
    """v raised by one step: "major", "minor", else a patch."""
    major, minor, patch = v
    if what == "major":
        return major + 1, 0, 0
    if what == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def released(version: str) -> bool:
    """Whether version is out: GitHub has the release v<version>. When gh can't tell (not installed, not
    logged in, offline): whether its installer was built here."""
    try:
        r = subprocess.run(["gh", "release", "view", f"v{version}", "--json", "tagName"], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return True
        if "release not found" in (r.stderr or "").lower():
            return False
    except (OSError, subprocess.TimeoutExpired):
        pass
    return any((ROOT / "dist" / "installer" / f"{name}-Setup-{version}.exe").exists()
               for name in ("YinItAgreementForm", "DjinnItAgreementForm"))  # (the app's name before 2.0)


def main(argv: list[str]) -> None:
    """Runs the command line (see the module docstring); anything else prints the usage and exits."""
    v = current()
    now = ".".join(map(str, v))
    if not argv:
        print(now)
        return
    arg = argv[0].lower()
    if arg == "auto":
        if not released(now):
            print(f"Version {now} has not been released yet - keeping it.")
            return
        arg = "patch"
    if arg in ("major", "minor", "patch"):
        new = write(bump(arg, v))
    elif re.fullmatch(r"\d+\.\d+\.\d+", arg):
        new = write(tuple(int(p) for p in arg.split(".")))
    else:
        sys.exit(__doc__)
    print(f"Version {now} -> {new}")


if __name__ == "__main__":
    main(sys.argv[1:])
