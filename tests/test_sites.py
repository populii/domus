"""One server, many sites: the list, a new address, and each site kept to its own folder and store."""

import http.client
import json
import threading

import pytest

from domus.app import serve


@pytest.fixture
def server(tmp_path, monkeypatch):
    started, rebuilt = [], []
    monkeypatch.setattr(serve.Creator, "start", lambda self, address: started.append((self.root.name, address)))
    monkeypatch.setattr(serve.BuildJob, "start", lambda self: rebuilt.append(self.root.name))
    demo = tmp_path / "demo"
    demo.mkdir()
    (demo / "model.json").write_text('{"who": "demo"}', encoding="utf-8")
    for name, title in (("one", "First & best"), ("two", "Second")):
        (tmp_path / "sites" / name / "data").mkdir(parents=True)
        (tmp_path / "sites" / name / "viewer" / "data").mkdir(parents=True)
        (tmp_path / "sites" / name / "data" / "site.toml").write_text(f'[site]\ntitle = "{title}"\n', encoding="utf-8")
        (tmp_path / "sites" / name / "viewer" / "data" / "model.json").write_text(json.dumps({"who": name}), encoding="utf-8")
    httpd = serve.make_sites_server("127.0.0.1", 0, tmp_path / "sites", demo)
    httpd.started, httpd.rebuilt = started, rebuilt
    httpd.folder = tmp_path / "sites"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def call(httpd, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1])
    c.request(method, path, body, {"Content-Type": "application/json"} if body is not None else {})
    r = c.getresponse()
    out = (r.status, r.read().decode("utf-8", "replace"), r.getheader("Location"))
    c.close()
    return out


def test_the_list_escapes_titles_and_links_each_site(server):
    status, page, _ = call(server, "GET", "/")
    assert status == 200
    assert 'href="s/one/"' in page and "First &amp; best" in page and 'href="s/two/"' in page
    assert "Build a new one" in page


def test_each_site_serves_its_own_model_viewer_and_store(server):
    assert json.loads(call(server, "GET", "/s/one/data/model.json")[1])["who"] == "one"
    assert json.loads(call(server, "GET", "/s/two/data/model.json")[1])["who"] == "two"
    status, page, _ = call(server, "GET", "/s/one/")
    assert status == 200 and 'id="allSites"' in page and "No model built yet" not in page
    assert call(server, "GET", "/s/one/scan")[0] == 200
    assert call(server, "GET", "/s/one/scan-core.js")[0] == 200
    assert call(server, "PUT", "/s/one/api/db/sketches/a", '{"x": 1}')[0] == 200
    assert call(server, "GET", "/s/one/api/db/sketches/a")[0] == 200
    assert call(server, "GET", "/s/two/api/db/sketches/a")[0] == 404
    assert (server.folder / "one" / ".domus" / "viewer.db").exists()


def test_an_unbuilt_site_links_back_to_the_list(server):
    (server.folder / "three").mkdir()
    page = call(server, "GET", "/s/three/")[1]
    assert 'href="../../">' in page and 'href="../../demo"' in page and "<!--back-->" not in page


def test_the_pages_share_the_viewers_look(server):
    viewer = (serve.VIEWER / "index.html").read_text(encoding="utf-8")
    page = call(server, "GET", "/")[1]
    for token in ("--paper: #eef0ec", "--panel: #f8f9f6", "--ink: #1b2733", "--accent: #0e6f86", "--paper: #0f1518", "--accent: #43b9d6"):
        assert token in viewer and token in page


def test_a_site_without_its_slash_is_sent_to_it_so_relative_links_work(server):
    status, _, where = call(server, "GET", "/s/one")
    assert status == 301 and where == "/s/one/"


def test_only_real_site_names_get_through(server):
    assert call(server, "GET", "/s/nope/")[0] == 404
    assert call(server, "GET", "/s/../secret")[0] in (200, 404)     # not a site: it falls to the viewer's folder, which holds no such file
    assert "who" not in call(server, "GET", "/s/%2e%2e/one/data/model.json")[1]
    assert call(server, "GET", "/s/one/data/../../../two/data/site.toml")[0] == 404
    assert call(server, "POST", "/s/nope/api/create", '{"address": "1 A St"}')[0] == 404


def test_the_top_has_the_demo_but_no_site_jobs(server):
    assert json.loads(call(server, "GET", "/data/model.json")[1])["who"] == "demo"
    assert call(server, "GET", "/demo")[0] == 200
    assert call(server, "GET", "/api/create")[0] == 404
    assert call(server, "GET", "/scan")[0] == 404
    assert call(server, "POST", "/api/scan/x.mp4", "")[0] == 404
    assert call(server, "GET", "/healthz")[0] == 200


def test_a_new_address_gets_its_own_folder_and_starts_there(server):
    status, body, _ = call(server, "POST", "/api/sites", json.dumps({"address": "12 Ēxample Road, Town"}))
    assert status == 202 and json.loads(body)["url"] == "s/12-example-road-town/"
    assert server.started == [("12-example-road-town", "12 Ēxample Road, Town")]
    assert (server.folder / "12-example-road-town").is_dir()
    assert call(server, "POST", "/api/sites", json.dumps({"address": "-x"}))[0] == 400


def test_an_old_model_is_built_again_when_its_site_is_opened_but_never_beside_another_build(server):
    # The fixture's models have no build.json: a Domus from before it said which Domus built them.
    two = server.RequestHandlerClass.func.sites.get("two")["build_job"]
    two.status = lambda: {"state": "running"}
    status, page, _ = call(server, "GET", "/s/one/")
    assert status == 200 and "No model built yet" not in page and server.rebuilt == []
    two.status = lambda: {"state": "done"}
    assert call(server, "GET", "/s/one/")[0] == 200 and server.rebuilt == ["one"]
    assert call(server, "GET", "/s/one/")[0] == 200 and server.rebuilt == ["one"]     # once while this server runs
    assert call(server, "GET", "/demo")[0] == 200 and server.rebuilt == ["one"]


def test_one_build_at_a_time_and_a_repeated_address_gets_a_new_name(tmp_path):
    assert serve.site_name("1 A St", {"1-a-st"}) == "1-a-st-2"
    assert serve.site_name("***", set()) == "site"
    sites = serve.Sites(tmp_path)
    (tmp_path / "busy").mkdir()
    sites.get("busy")["creator"].status = lambda: {"state": "running"}
    assert sites.create("1 A St") is None
    assert sites.listing() == [("busy", "busy", "building")]


def test_a_sites_job_builds_in_its_own_folder_whatever_the_environment_says(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMUS_OUTPUT", "/site/viewer/data")
    monkeypatch.setenv("DOMUS_SITE", "/site")
    env = serve.job_env(tmp_path, True)
    assert env["DOMUS_SITE"] == str(tmp_path) and "DOMUS_OUTPUT" not in env
    assert serve.job_env(tmp_path, False)["DOMUS_OUTPUT"] == "/site/viewer/data"
    c = serve.Creator(tmp_path, own_output=True)
    (tmp_path / "viewer" / "data").mkdir(parents=True)
    (tmp_path / "viewer" / "data" / "model.json").write_text("{}", encoding="utf-8")
    assert c.built()


def test_the_address_box_stops_at_the_limit(server, monkeypatch):
    monkeypatch.setenv("DOMUS_MAX_SITES", "1")
    status = call(server, "POST", "/api/sites", json.dumps({"address": "12 Example Road"}))[0]
    assert status == 409
