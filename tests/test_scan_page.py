"""The guided scan page: its upload route, and its sums (with node, if there is one)."""

import http.client
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from domus.app import serve


@pytest.fixture
def app(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "model.json").write_text("{}", encoding="utf-8")
    httpd = serve.make_server("127.0.0.1", 0, [out], tmp_path / "v.db", root=tmp_path)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], tmp_path
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body, headers or {})
    r = c.getresponse()
    return r.status, r.read()


def test_page_is_served(app):
    port, _ = app
    status, body = call(port, "GET", "/scan")
    assert status == 200 and b"Scan this site" in body and b"<!doctype html>" in body
    assert call(port, "GET", "/scan-core.js")[0] == 200


def test_upload_goes_to_sources_raw(app):
    port, root = app
    head = {"Content-Type": "application/octet-stream"}
    status, body = call(port, "POST", "/api/scan/20261102T010203.webm", b"video bytes", head)
    assert status == 201
    name = json.loads(body)["file"]
    assert name == "sources/raw/scan-20261102T010203.webm"
    assert (root / name).read_bytes() == b"video bytes"
    assert call(port, "POST", "/api/scan/20261102T010203-poses.json", b"[]", head)[0] == 201


@pytest.mark.parametrize("name", ["../evil.webm", "x.exe", "..%2Fevil.webm", "a.b.webm", "20261102T010203.html"])
def test_upload_names_are_checked(app, name):
    port, root = app
    status, _ = call(port, "POST", f"/api/scan/{name}", b"x", {"Content-Type": "application/octet-stream"})
    assert status == 400
    assert not (root / "evil.webm").exists()


def test_upload_wont_overwrite_or_take_a_web_form(app):
    port, _ = app
    head = {"Content-Type": "application/octet-stream"}
    assert call(port, "POST", "/api/scan/1.mp4", b"a", head)[0] == 201
    assert call(port, "POST", "/api/scan/1.mp4", b"b", head)[0] == 409
    assert call(port, "POST", "/api/scan/2.mp4", b"a", {"Content-Type": "text/plain"})[0] == 415


def test_upload_size_is_capped(app):
    port, _ = app
    head = {"Content-Type": "application/octet-stream", "Content-Length": str(serve.MAX_SCAN + 1)}
    assert call(port, "POST", "/api/scan/3.mp4", None, head)[0] == 413


def test_a_scan_counts_on_the_checklist(built, site, project):
    from domus.checks import enrich

    raw = site / "sources" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "scan-1.webm").write_bytes(b"x")
    try:
        assert next(s for s in enrich.checklist(project) if s["id"] == "video")["found"]
    finally:
        (raw / "scan-1.webm").unlink()


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_the_sums():
    here = Path(__file__).parent
    core = here.parent / "src" / "domus" / "app" / "viewer" / "scan-core.js"
    out = subprocess.run(["node", str(here / "scan_core_check.mjs"), str(core)], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
