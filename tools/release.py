"""Cut a release: work out the version, date the changelog's notes, commit and tag. Pushing is up to you.

    python tools/release.py
    git push origin HEAD v2026.11.0

Versions are calendar versions, YEAR.MONTH.COUNT: 2026.11.0 is the first release of November 2026, 2026.11.1 the next.
The tag sets off .github/workflows/release.yml, which publishes the image and the GitHub release.
Write the notes under `## Unreleased` in CHANGELOG.md first.
"""

import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "src" / "domus" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"


def next_version(tags, today):
    """The next YEAR.MONTH.COUNT, given the tags there are."""
    prefix = f"v{today.year}.{today.month}."
    counts = [int(t[len(prefix):]) for t in tags if t.startswith(prefix) and t[len(prefix):].isdigit()]
    return f"{today.year}.{today.month}.{max(counts) + 1 if counts else 0}"


def main():
    if len(sys.argv) != 1:
        raise SystemExit(__doc__)
    text = CHANGELOG.read_text(encoding="utf-8")
    if not re.search(r"^## Unreleased\n+- ", text, re.MULTILINE):
        raise SystemExit("CHANGELOG.md has no notes under '## Unreleased'")
    status = subprocess.run(["git", "status", "--porcelain", "--", ":!CHANGELOG.md"], cwd=ROOT, capture_output=True,
                            text=True, check=True)
    if status.stdout.strip():
        raise SystemExit("Commit your other changes first:\n" + status.stdout)
    tags = subprocess.run(["git", "tag", "--list", "v*"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    current = re.search(r'__version__ = "(.*)"', INIT.read_text(encoding="utf-8"))[1]
    v = next_version([*tags, f"v{current}"], datetime.datetime.now(datetime.UTC))       # the code's version counts: history starts after it
    CHANGELOG.write_text(text.replace("## Unreleased", f"## {v}", 1), encoding="utf-8", newline="\n")
    INIT.write_text(re.sub(r'__version__ = ".*"', f'__version__ = "{v}"', INIT.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    subprocess.run(["uv", "lock"], cwd=ROOT, check=True)
    subprocess.run(["git", "add", str(INIT), "CHANGELOG.md", "uv.lock"], cwd=ROOT, check=True)
    subprocess.run(["git", "commit", "-m", f"Release {v}"], cwd=ROOT, check=True)
    subprocess.run(["git", "tag", "-a", f"v{v}", "-m", f"domus {v}"], cwd=ROOT, check=True)
    print(f"Tagged v{v}. Now: git push origin HEAD v{v}")


if __name__ == "__main__":
    main()
