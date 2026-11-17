"""The server: the viewer, the data, the store, and nothing else."""

import http.client
import json
import threading

import pytest

from domus import serve


@pytest.fixture
def server(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "model.json").write_text("{}")
    (tmp_path / "secret.txt").write_text("private")
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
    (tmp_path / "data" / "site.toml").write_text("")
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


def test_a_name_it_does_not_know_gets_nothing(server):
    assert call(server, "GET", "/healthz", headers={"Host": "evil.example"})[0] == 403
    assert call(server, "GET", "/healthz", headers={"Host": "localhost:8765"})[0] == 200
