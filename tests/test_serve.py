"""The server: the viewer, the data, the store, and nothing else."""

import gzip
import http.client
import json
import os
import threading

import pytest

from domus import serve


@pytest.fixture
def server(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "model.json").write_text("{}", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("private", encoding="utf-8")
    httpd = serve.make_server("127.0.0.1", 0, [tmp_path / "unbuilt", data], tmp_path / "db" / "v.db", trust_proxy=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body, headers or {})
    r = c.getresponse()
    return r.status, r.read(), r


def test_health_and_page(server):
    assert call(server, "GET", "/healthz")[0] == 200
    status, body, _ = call(server, "GET", "/demo")
    assert status == 200 and b"<!doctype html>" in body and b'id="view"' in body
    assert call(server, "GET", "/data/model.json")[0] == 200
    assert call(server, "GET", "/vendor/three/three.module.js")[0] == 200


def test_viewer_loads_nothing_from_other_hosts():
    page = (serve.VIEWER / "index.html").read_text(encoding="utf-8")
    for host in ("googleapis", "gstatic", "jsdelivr", "unpkg", "cdnjs"):
        assert host not in page


@pytest.mark.parametrize("path", ["/../secret.txt", "/data/../secret.txt", "/data/..%2fsecret.txt", "/data/%2e%2e/secret.txt"])
def test_paths_stay_inside(server, path):
    assert call(server, "GET", path)[0] == 404


def test_store_round_trip(server):
    head = {"Content-Type": "application/json"}
    assert call(server, "GET", "/api/db/sketches/current")[0] == 404
    assert call(server, "PUT", "/api/db/sketches/current", json.dumps({"items": [1]}), head)[0] == 200
    status, body, _ = call(server, "GET", "/api/db/sketches/current")
    assert status == 200 and json.loads(body) == {"id": "current", "data": {"items": [1]}}
    assert json.loads(call(server, "GET", "/api/db/sketches")[1])[0]["id"] == "current"


def test_store_refuses_bad_writes(server):
    head = {"Content-Type": "application/json"}
    assert call(server, "PUT", "/api/db/sketches/x", "{}", {"Content-Type": "text/plain"})[0] == 415
    assert call(server, "PUT", "/api/db/sketches/x", "not json", head)[0] == 400
    assert call(server, "PUT", "/api/db/a..b/x", "{}", head)[0] == 404
    assert call(server, "PUT", "/api/db/sketches", "{}", head)[0] == 404
    assert call(server, "PUT", "/api/db/sketches/x", None, {**head, "Content-Length": str(serve.MAX_DOC + 1)})[0] == 413


def test_user_comes_from_the_proxy_only_when_trusted(server):
    assert json.loads(call(server, "GET", "/api/user", headers={"X-Forwarded-User": "sam"})[1])["id"] == "sam"


def test_no_model_says_so(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "site.toml").write_text("", encoding="utf-8")
    httpd = serve.make_server("127.0.0.1", 0, [tmp_path], tmp_path / "v.db", root=tmp_path)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert b"No model built yet" in call(httpd.server_address[1], "GET", "/")[1]
        assert json.loads(call(httpd.server_address[1], "GET", "/api/user", headers={"X-Forwarded-User": "sam"})[1])["id"] is None
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_every_response_says_what_may_load(server):
    r = call(server, "GET", "/healthz")[2]
    assert "frame-ancestors 'none'" in r.getheader("Content-Security-Policy")
    assert r.getheader("X-Content-Type-Options") == "nosniff"


def test_big_text_goes_out_gzipped_to_a_browser_that_takes_it(server, tmp_path):
    # The fixture's model is too small to be worth it.
    r = call(server, "GET", "/data/model.json", headers={"Accept-Encoding": "gzip"})[2]
    assert r.getheader("Content-Encoding") is None and r.getheader("Vary") == "Accept-Encoding"
    model = json.dumps({"layers": [{"id": f"layer-{k}", "name": "A layer of the model"} for k in range(200)]}).encode()
    (tmp_path / "data" / "model.json").write_bytes(model)
    status, body, r = call(server, "GET", "/data/model.json", headers={"Accept-Encoding": "gzip, deflate, br"})
    assert status == 200 and r.getheader("Content-Encoding") == "gzip" and r.getheader("Vary") == "Accept-Encoding"
    assert gzip.decompress(body) == model and len(body) < len(model) / 3
    assert int(r.getheader("Content-Length")) == len(body)
    # A browser that doesn't take gzip, or refuses it, gets the file as it is.
    for accept in (None, "identity", "gzip;q=0"):
        status, body, r = call(server, "GET", "/data/model.json", headers={"Accept-Encoding": accept} if accept else {})
        assert status == 200 and body == model and r.getheader("Content-Encoding") is None
        assert r.getheader("Vary") == "Accept-Encoding"
    # Asked again with the time it was given, it hasn't changed.
    since = r.getheader("Last-Modified")
    status, body, r = call(server, "GET", "/data/model.json", headers={"Accept-Encoding": "gzip", "If-Modified-Since": since})
    assert status == 304 and body == b""
    # The viewer's own libraries are compressed too.
    lib = max((serve.VIEWER / "vendor").rglob("*.js"), key=lambda p: p.stat().st_size)
    r = call(server, "GET", "/" + lib.relative_to(serve.VIEWER).as_posix(), headers={"Accept-Encoding": "gzip"})[2]
    assert r.getheader("Content-Encoding") == "gzip"


def test_a_rebuilt_model_is_not_served_from_the_old_one(server, tmp_path):
    path = tmp_path / "data" / "model.json"
    for k in range(2):
        # Two builds the same size: only the file's time tells them apart.
        model = json.dumps({"build": k, "pad": "x" * 4000}).encode()
        path.write_bytes(model)
        os.utime(path, (1_700_000_000 + k, 1_700_000_000 + k))
        assert gzip.decompress(call(server, "GET", "/data/model.json", headers={"Accept-Encoding": "gzip"})[1]) == model


def site_built_by(root, domus):
    """A site whose model the Domus `domus` built: None for one from before build.json said."""
    (root / "data").mkdir(parents=True)
    (root / "data" / "site.toml").write_text("", encoding="utf-8")
    out = root / "viewer" / "data"
    out.mkdir(parents=True)
    (out / "model.json").write_text("{}", encoding="utf-8")
    made = {"title": "A site", "built": "2026-11-01T00:00:00+00:00", "commit": None}
    (out / "build.json").write_text(json.dumps({**made, **({"domus": domus} if domus else {})}), encoding="utf-8")
    return out


@pytest.mark.parametrize("domus, again", [(serve.__version__, False), ("2026.11.1", True), (None, True)])
def test_a_model_another_domus_built_is_built_again_when_its_site_is_opened(tmp_path, monkeypatch, domus, again):
    state = {"now": "idle"}
    started = []
    monkeypatch.setattr(serve.BuildJob, "start", lambda self: (started.append(self.root), state.update(now="running")))
    monkeypatch.setattr(serve.BuildJob, "status", lambda self: {"state": state["now"], "log": ""})
    out = site_built_by(tmp_path / "site", domus)
    httpd = serve.make_server("127.0.0.1", 0, [out], tmp_path / "site" / ".domus" / "v.db", root=tmp_path / "site")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        assert call(port, "GET", "/demo")[0] == 200 and started == []        # the demo is never built again
        status, body, _ = call(port, "GET", "/")
        assert status == 200 and b'id="view"' in body                          # the model it has, meanwhile
        assert started == ([tmp_path / "site"] if again else [])
        call(port, "GET", "/")                                                 # one build at a time
        state["now"] = "failed"
        call(port, "GET", "/")                                                 # and a failed one isn't tried again
        assert len(started) == again
        assert json.loads(call(port, "GET", "/api/version")[1])["model"].get("domus") == domus
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_folder_that_is_not_a_site_is_never_built(tmp_path, monkeypatch):
    monkeypatch.setattr(serve.BuildJob, "start", lambda self: pytest.fail("built"))
    out = site_built_by(tmp_path / "site", None)
    (tmp_path / "site" / "data" / "site.toml").unlink()
    httpd = serve.make_server("127.0.0.1", 0, [out], tmp_path / "v.db", root=tmp_path / "site")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert call(httpd.server_address[1], "GET", "/")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_name_it_does_not_know_gets_nothing(server):
    assert call(server, "GET", "/healthz", headers={"Host": "evil.example"})[0] == 403
    assert call(server, "GET", "/healthz", headers={"Host": "localhost:8765"})[0] == 200
