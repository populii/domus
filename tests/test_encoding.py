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


def test_a_layer_is_written_with_lf_line_endings_on_every_platform(tmp_path):
    """geojson.write, which writes the layers canopy, prep ifc and prep plans make, ends its lines with LF on Windows
    too, as the change route and domus new write theirs, so a site's files don't change their line endings by where
    they were written."""
    from domus.geo import geojson

    layer = geojson.collection([{"type": "Feature", "properties": {"id": "peg", "name": "A peg (made up)"},
                                 "geometry": {"type": "Point", "coordinates": [1500001.23456, 5500002.0]}}],
                               "features-pegs", "Pegs, made up", epsg=2193)
    path = tmp_path / "data" / "site" / "features-pegs.geojson"
    geojson.write(path, layer)
    raw = path.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"}\n") and raw.count(b"\n") > 5
    assert geojson.read(path)["features"][0]["geometry"]["coordinates"] == [1500001.235, 5500002.0]
