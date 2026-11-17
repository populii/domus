"""The prep steps on made-up documents: a PDF, a photo, the demo site."""

import json
import sys

import numpy as np
import pytest
from PIL import Image

from domus.errors import DomusError

pytest.importorskip("pdfplumber")


def write_pdf(path, width, height, content):
    """A one-page PDF with the given content stream (y up, as PDF draws) and Helvetica as /F1."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>".encode(),
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))


@pytest.fixture
def prep_json(site):
    path = site / "data" / "prep.json"
    yield path
    path.unlink(missing_ok=True)


def run(step, *args):
    from domus import prep

    argv = sys.argv
    try:
        return prep.main([step, *args])
    finally:
        sys.argv = argv


def test_trace_puts_a_vector_sheet_on_the_grid(site, prep_json):
    raw = site / "sources" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    # A red line 100 pt down the page, a black one 200 pt down and a label between them (PDF's y runs up from 400).
    write_pdf(raw / "plan.pdf", 600, 400, b"1 0 0 RG 1 w 100 300 m 300 300 l S 0 0 0 RG 2 w 100 200 m 300 200 l S "
                                          b"BT /F1 12 Tf 150 250 Td (1.05) Tj ET")
    ring = json.loads((site / "data" / "site" / "boundary.geojson").read_text(encoding="utf-8"))["features"][0]["geometry"]["coordinates"][0]
    a, b = ring[0], ring[1]
    prep_json.write_text(json.dumps({"plans": {"trace": [{
        "name": "plan", "pdf": "sources/raw/plan.pdf", "out": "data/sources/plan.geojson", "window": [50, 50, 500, 350],
        "text": "words", "fill_colour": True, "colours": {"1.0,0.0,0.0": "drain", "0.0,0.0,0.0": "line"},
        "fit": {"kind": "similarity", "paper": {"A": [100, 100], "B": [300, 100]}, "boundary_corners": ["A", "B"]}}]}}), encoding="utf-8")
    run("plan-trace")
    feats = json.loads((site / "data" / "sources" / "plan.geojson").read_text(encoding="utf-8"))["features"]
    drain = next(f for f in feats if f["properties"]["layer"] == "drain")
    assert np.allclose(drain["geometry"]["coordinates"], [a, b], atol=1e-6)
    assert any(f["properties"].get("text") == "1.05" for f in feats)
    assert any(f["properties"]["layer"] == "line" for f in feats)


def test_feature_textures_rectify_and_tile(site, prep_json):
    raw = site / "sources" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    a = np.zeros((200, 200, 3), np.uint8)
    a[50:150, 50:150] = (200, 100, 50)
    Image.fromarray(a).save(raw / "photo.png")
    prep_json.write_text(json.dumps({"feature_textures": {
        "textures": {"plate.jpg": {"source": "photo.png", "quad": [[60, 60], [140, 60], [140, 140], [60, 140]], "size": [64, 64], "seams": "xy"}},
        "turned": {"plate-up.jpg": "plate.jpg"}}}), encoding="utf-8")
    run("feature-textures")
    out = np.asarray(Image.open(site / "data" / "features" / "plate.jpg"))
    assert out.shape == (64, 64, 3)
    assert np.abs(out.mean(axis=(0, 1)) - (200, 100, 50)).max() < 8
    assert (site / "data" / "features" / "plate-up.jpg").exists()
    for name in ("plate.jpg", "plate-up.jpg"):
        (site / "data" / "features" / name).unlink()   # the demo draws plain; leave it so


def test_check_finds_a_duplicate_id_on_the_demo(site, built):
    from domus.prep import check

    m = check.Model()
    findings = []
    check.check_sanity(m, findings)
    assert not [f for f in findings if f[1] == "duplicate"]
    m.site_features.append(m.site_features[0])
    findings = []
    assert check.check_sanity(m, findings) >= 1
    assert [f for f in findings if f[1] == "duplicate"]


def test_a_missing_settings_file_says_so(site, prep_json):
    prep_json.unlink(missing_ok=True)
    with pytest.raises(DomusError, match="from-documents"):
        run("plan-pages")


def test_each_command_keeps_its_own_help(capsys):
    from domus import cli

    with pytest.raises(SystemExit):
        cli.main(["scan", "--help"])
    assert "phone LiDAR" in capsys.readouterr().out
