"""Compare two builds of a site, to prove a code change left the model alone.

    python tools/compare_builds.py OLD_DIR NEW_DIR

Compares model.json and summary.json value by value (model keys that only describe the build are
skipped), and the other files by hash. Exits 1 and lists the differences if they don't match.
"""

import hashlib
import json
import sys
from pathlib import Path

# What describes the build or how the viewer labels datums, not the site's geometry.
SKIP_KEYS = {"built", "commit", "datums", "heading", "subtitle", "about", "views", "enrich"}
# python tools/compare_builds.py OLD NEW --skip KEY also skips a top-level model key an old build wrote.
SKIP_FILES = {"model.json", "summary.json", "build.json"}


def diffs(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if path == "" and k in SKIP_KEYS:
                continue
            if k not in a or k not in b:
                yield f"{path}/{k}: only in {'new' if k not in a else 'old'}"
            else:
                yield from diffs(a[k], b[k], f"{path}/{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            yield f"{path}: {len(a)} items, now {len(b)}"
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                yield from diffs(x, y, f"{path}[{i}]")
    elif a != b:
        yield f"{path}: {str(a)[:80]!r} -> {str(b)[:80]!r}"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    old, new = Path(sys.argv[1]), Path(sys.argv[2])
    if "--skip" in sys.argv:
        SKIP_KEYS.add(sys.argv[sys.argv.index("--skip") + 1])
    out = []
    for name in ("model.json", "summary.json"):
        a, b = (json.loads((d / name).read_text(encoding="utf-8")) for d in (old, new))
        if name == "model.json":
            a["layers"] = {x.get("id", x.get("name")): x for x in a["layers"]}
            b["layers"] = {x.get("id", x.get("name")): x for x in b["layers"]}
        out += [f"{name}{d}" for d in diffs(a, b)]
    names_a = {p.name for p in old.iterdir()} - SKIP_FILES
    names_b = {p.name for p in new.iterdir()} - SKIP_FILES
    out += [f"{n}: only in old" for n in sorted(names_a - names_b)]
    out += [f"{n}: only in new" for n in sorted(names_b - names_a)]
    same = 0
    for n in sorted(names_a & names_b):
        if digest(old / n) == digest(new / n):
            same += 1
        else:
            out.append(f"{n}: file differs")
    print(f"{same} of {len(names_a | names_b)} files identical; {len(out)} differences")
    for line in out[:60]:
        print(" ", line)
    sys.exit(1 if out else 0)


if __name__ == "__main__":
    main()
