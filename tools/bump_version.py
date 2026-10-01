"""Changes the version number (the one place it lives: minute_filler/__init__.py).

    python tools/bump_version.py            show the current version
    python tools/bump_version.py patch      1.2.3 -> 1.2.4   (small fixes; what build_installer.bat does)
    python tools/bump_version.py minor      1.2.3 -> 1.3.0   (new features)
    python tools/bump_version.py major      1.2.3 -> 2.0.0
    python tools/bump_version.py 1.4.0      set it exactly
    python tools/bump_version.py auto       patch bump, but only if an installer for the current
                                      version was already built (so a version that was never
                                      built or released is not skipped)
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "minute_filler" / "__init__.py"
PATTERN = re.compile(r'(__version__\s*=\s*")(\d+)\.(\d+)\.(\d+)(")')


def current() -> tuple[int, int, int]:
    m = PATTERN.search(INIT.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f'No __version__ = "X.Y.Z" found in {INIT}')
    return int(m[2]), int(m[3]), int(m[4])


def write(v: tuple[int, int, int]) -> str:
    text = INIT.read_text(encoding="utf-8")
    new = PATTERN.sub(lambda m: f'{m[1]}{v[0]}.{v[1]}.{v[2]}{m[5]}', text, count=1)
    INIT.write_text(new, encoding="utf-8", newline="")  # keep the file's own line endings
    return ".".join(map(str, v))


def bump(what: str, v: tuple[int, int, int]) -> tuple[int, int, int]:
    major, minor, patch = v
    if what == "major":
        return major + 1, 0, 0
    if what == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def main(argv: list[str]) -> None:
    v = current()
    now = ".".join(map(str, v))
    if not argv:
        print(now)
        return
    arg = argv[0].lower()
    if arg == "auto":
        built = ROOT / "dist" / "installer" / f"DjinnItAgreementForm-Setup-{now}.exe"
        if not built.exists():
            print(f"Version {now} has not been built yet - keeping it.")
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
