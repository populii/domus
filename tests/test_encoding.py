"""Every text file is read and written as UTF-8, whatever the platform's default: a layer saved from QGIS with a
macron in a name has to load on Windows too, where Python's default is the system's code page."""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def unnamed_encodings(path):
    """Calls in a Python file that read or write text without naming an encoding: the built-in open() in a text
    mode, and Path.read_text() and write_text(). Other libraries' open() (rasterio, PIL, pycolmap, urllib's
    openers) aren't text files, and binary modes and read_bytes() need no encoding."""
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(n, ast.Call) or any(k.arg == "encoding" for k in n.keywords):
            continue
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            yield n.lineno
        elif isinstance(f, ast.Name) and f.id == "open":
            mode = n.args[1].value if len(n.args) > 1 and isinstance(n.args[1], ast.Constant) else None
            mode = next((k.value.value for k in n.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)), mode)
            if mode is None or "b" not in str(mode):
                yield n.lineno


def test_every_text_read_and_write_names_utf8():
    found = [f"{p.relative_to(REPO).as_posix()}:{line}"
             for folder in ("src", "tools", "tests", "site")
             for p in sorted((REPO / folder).rglob("*.py")) if "vendor" not in p.parts
             for line in unnamed_encodings(p)]
    assert not found, "name the encoding (encoding=\"utf-8\") at: " + ", ".join(found)
