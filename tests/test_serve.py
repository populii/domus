"""The server: the viewer, the data, the store, and nothing else."""

import gzip
import http.client
import json
import os
import threading
from pathlib import Path

import pytest

from domus.app import serve


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


def test_inside_keeps_to_its_folder(tmp_path):
    base, outside = tmp_path / "base", tmp_path / "base2"
    (base / "sub").mkdir(parents=True)
    outside.mkdir()
    (base / "sub" / "a.txt").write_text("a", encoding="utf-8")
    (base / "out").symlink_to(outside)
    missing = str(base.resolve() / "__missing__")
    assert serve._inside(base, "") == str(base.resolve())
    assert serve._inside(base, "sub/a.txt") == str((base / "sub" / "a.txt").resolve())
    assert serve._inside(base, "sub%2fa.txt") == str((base / "sub" / "a.txt").resolve())
    for rel in ("..", "../base2/x", "%2e%2e/base2", str(outside), "out/x"):
        assert serve._inside(base, rel) == missing


def test_three_makes_its_ids_from_the_browsers_secure_random_numbers():
    """The vendored three.js is patched (vendor/three/PATCHES.md): an update that drops the patch fails here."""
    lib = (serve.VIEWER / "vendor" / "three" / "three.core.js").read_text(encoding="utf-8")
    body = lib[lib.index("function generateUUID()"):]
    body = body[:body.index("\n}\n")]
    assert "crypto.getRandomValues(" in body and "Math.random" not in body


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


def test_the_viewer_may_compile_its_own_webassembly_which_goes_out_gzipped(server):
    """Manifold (vendor/manifold-3d) is WebAssembly: the page may compile it, and still eval no script."""
    policy = call(server, "GET", "/healthz")[2].getheader("Content-Security-Policy")
    script = next(part for part in policy.split(";") if part.strip().startswith("script-src")).split()
    assert "'wasm-unsafe-eval'" in script and "'unsafe-eval'" not in script
    status, body, r = call(server, "GET", "/vendor/manifold-3d/manifold.wasm", headers={"Accept-Encoding": "gzip"})
    assert status == 200 and r.getheader("Content-Type") == "application/wasm" and r.getheader("Content-Encoding") == "gzip"
    wasm = gzip.decompress(body)
    assert wasm[:4] == b"\0asm" and len(body) < 0.45 * len(wasm)
    assert (serve.VIEWER / "vendor" / "manifold-3d" / "LICENSE").read_text(encoding="utf-8").lstrip().startswith("Apache License")


def test_a_browser_compiles_manifold_under_the_servers_policy(server, tmp_path):
    """The viewer's page, served as domus serve sends it, can load Manifold and work a boolean out with it."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        if not os.path.exists(p.chromium.executable_path):
            pytest.skip("Playwright's Chromium isn't installed: uv run playwright install chromium")
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(f"http://127.0.0.1:{server}/healthz")
            volume = page.evaluate("""async () => {
                const wasm = await (await import("/vendor/manifold-3d/manifold.js")).default();
                wasm.setup();
                const a = wasm.Manifold.cube([2, 2, 2]), b = wasm.Manifold.cube([2, 2, 2]).translate([1, 0, 0]);
                const both = a.intersect(b), v = both.volume();
                [a, b, both].forEach((m) => m.delete());
                return v;
            }""")
        finally:
            browser.close()
    assert volume == pytest.approx(4.0) and not errors


@pytest.fixture
def demo_server(built, tmp_path):
    """domus serve on the made-up demo's built model."""
    httpd = serve.make_server("127.0.0.1", 0, [tmp_path / "unbuilt", built], tmp_path / "db" / "v.db")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], json.loads((built / "model.json").read_text(encoding="utf-8"))
    httpd.shutdown()
    httpd.server_close()


def test_a_sketch_is_drawn_as_solids_in_a_worker_while_it_is_drawn(demo_server):
    """The viewer works a sketch out in its preview worker (sketch-worker.js) under the server's policy, and draws it:
    the cut and fill it shows are the preview's, which tests/test_sketch_preview.py holds to the build's."""
    from domus.options import preview

    sync_api = pytest.importorskip("playwright.sync_api")
    port, model = demo_server
    corpus = json.loads((Path(__file__).parent / "sketch_corpus.json").read_text(encoding="utf-8"))
    item = next(sk for sk in corpus["sketches"] if sk["name"] == "a-straight-wall")["items"][0]
    e0, n0 = model["origin"][:2]
    wall = {**item, "pts": [[e - e0, 0.0, -(n - n0)] for e, n in item["en"]]}
    state = json.dumps({"note": "", "items": [wall]})
    with sync_api.sync_playwright() as p:
        if not os.path.exists(p.chromium.executable_path):
            pytest.skip("Playwright's Chromium isn't installed: uv run playwright install chromium")
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            errors = []
            # (an empty store answers its documents' lookups with 404s, which the page takes in its stride)
            page.on("console", lambda m: errors.append(m.text)
                    if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.add_init_script(f"localStorage.setItem({json.dumps('domus-sketch-' + model['title'])}, {json.dumps(state)});")
            page.goto(f"http://127.0.0.1:{port}/demo")
            line = page.locator("#sketchPreview")
            line.wait_for(state="attached", timeout=60000)
            page.wait_for_function("() => !document.getElementById('sketchPreview').hidden", timeout=120000)
            text = line.text_content()
        finally:
            browser.close()
    v = preview.sketch_solids(model, [item])[0].volumes()
    assert text == f"As drawn: cut about {v['cut_m3']:.1f} m³, fill about {v['fill_m3']:.1f} m³" and not errors


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


def test_a_job_runs_one_domus_command_and_keeps_its_log(tmp_path):
    from domus import __version__

    job = serve.Job(tmp_path, "version", tail=1)
    assert job.status() == {"state": "idle", "log": ""}
    job.run("--version")
    job.proc.wait(timeout=120)
    assert job.status() == {"state": "done", "log": __version__}
    assert (tmp_path / ".domus" / "version.log").exists()


def changed_site(root):
    """A site with two proposals and two edits, as domus.project.changes writes them (made up): one proposal open and
    one rejected, and an edit an undo has undone."""
    (root / "data").mkdir(parents=True)
    (root / "data" / "site.toml").write_text("", encoding="utf-8")
    for folder, name, value in [
            ("proposals", "0001.json", {"id": "0001", "made": "2026-11-18T09:00:00Z", "source": "a check", "why": "Measured.",
                                        "intents": [], "summary": ["walls-existing: W1: confidence = 'measured'"], "state": "open"}),
            ("proposals", "0002.json", {"id": "0002", "made": "2026-11-18T09:01:00Z", "source": "a tool", "why": "Guessed.",
                                        "intents": [], "summary": [], "state": "rejected",
                                        "rejected": {"who": "sam", "when": "2026-11-18T09:02:00Z", "why": "No."}}),
            ("edits", "0001-move.json", {"id": "0001", "kind": "move", "who": "sam", "when": "2026-11-18T09:03:00Z",
                                         "why": "Measured.", "summary": ["walls-existing: W1 moved"], "inverse": [], "files": []}),
            ("edits", "0002-undo.json", {"id": "0002", "kind": "undo", "who": "sam", "when": "2026-11-18T09:04:00Z",
                                         "why": "Undoing edit 0001.", "undoes": "0001", "summary": [], "inverse": [], "files": []})]:
        (root / ".domus" / folder).mkdir(parents=True, exist_ok=True)
        (root / ".domus" / folder / name).write_text(json.dumps(value), encoding="utf-8")
    return root


def test_the_viewer_lists_the_changes_and_asks_for_one_at_a_time(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(serve.ChangeJob, "start", lambda self, *args: started.append(args))
    root = changed_site(tmp_path / "site")
    data = tmp_path / "data"
    data.mkdir()
    httpd = serve.make_server("127.0.0.1", 0, [data], root / ".domus" / "v.db", trust_proxy=True, root=root)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port, head = httpd.server_address[1], {"Content-Type": "application/json", "X-Forwarded-User": "sam"}
    try:
        listed = json.loads(call(port, "GET", "/api/changes")[1])
        assert [p["id"] for p in listed["proposals"]] == ["0001", "0002"] and "intents" not in listed["proposals"][0]
        assert [(e["id"], e["in_effect"], e.get("undone_by")) for e in listed["edits"]] == [("0002", True, None),
                                                                                          ("0001", False, "0002")]
        assert listed["job"] == {"state": "idle", "log": ""} and listed["count"] == 2
        assert call(port, "POST", "/api/proposals/1/accept", "{}", head)[0] == 202
        assert call(port, "POST", "/api/proposals/2/accept", "{}", head)[0] == 409          # rejected already
        assert call(port, "POST", "/api/proposals/9/accept", "{}", head)[0] == 404
        assert call(port, "POST", "/api/proposals/1/reject", "{}", head)[0] == 400          # with no reason
        assert call(port, "POST", "/api/proposals/1/reject", json.dumps({"why": "Not yet."}), head)[0] == 202
        assert call(port, "POST", "/api/edits/2/undo", "", head)[0] == 202
        assert call(port, "POST", "/api/edits/7/undo", "{}", head)[0] == 404
        assert call(port, "POST", "/api/edits/2/undo", "{}", {"Content-Type": "text/plain"})[0] == 415
        assert call(port, "POST", "/api/edits/2/redo", "{}", head)[0] == 404
        assert started == [("accept", "1", "--who", "sam", "--build"), ("reject", "1", "--why", "Not yet.", "--who", "sam"),
                           ("undo", "2", "--who", "sam", "--build")]
        monkeypatch.setattr(serve.ChangeJob, "status", lambda self: {"state": "running", "log": ""})
        assert call(port, "POST", "/api/edits/2/undo", "{}", head)[0] == 409               # one change at a time
        assert call(port, "POST", "/api/sketch/option", json.dumps({"name": "A", "items": []}), head)[0] == 409
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_change_job_runs_the_command(site, tmp_path):
    import shutil

    root = tmp_path / "demo"
    shutil.copytree(site, root, ignore=shutil.ignore_patterns("viewer", ".domus"))
    job = serve.ChangeJob(root)
    job.start("proposals")
    job.proc.wait(timeout=120)
    assert job.status()["state"] == "done" and "No proposed changes wait for a person" in job.status()["log"]


def test_each_site_of_many_has_its_own_changes(tmp_path):
    changed_site(tmp_path / "sites" / "one")
    httpd = serve.make_sites_server("127.0.0.1", 0, tmp_path / "sites")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        assert [p["id"] for p in json.loads(call(port, "GET", "/s/one/api/changes")[1])["proposals"]] == ["0001", "0002"]
        assert call(port, "GET", "/api/changes")[0] == 404
        assert call(port, "POST", "/api/edits/1/undo", "{}", {"Content-Type": "application/json"})[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_post_the_many_site_server_turns_away_still_gets_its_answer(tmp_path):
    """The server reads a post's body before it answers 404, so a body that arrives after the headers (as http.client
    sends it, in a second send) isn't left unread when the connection closes, which would reset it and lose the 404."""
    import socket
    import time

    (tmp_path / "sites").mkdir()
    httpd = serve.make_sites_server("127.0.0.1", 0, tmp_path / "sites")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    head = (b"POST %s HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
            b"Content-Length: 2\r\n\r\n")
    try:
        for path in (b"/api/edits/1/undo", b"/s/nowhere/api/edits/1/undo"):
            with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.sendall(head % path)
                time.sleep(0.2)
                s.sendall(b"{}")
                got = b""
                while chunk := s.recv(65536):
                    got += chunk
            assert got.split(b"\r\n", 1)[0].endswith(b"404 Not Found"), (path, got[:80])
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_viewer_keeps_the_ground_a_wall_holds_from_crossing_itself(demo_server):
    """The ground a sketched wall holds, its line and the line behind it, can't cross itself: holding it deeper behind
    a narrow V, where it would, is undone, with the message the server gives (sketch.crossings)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    port, model = demo_server
    e0, n0 = model["origin"][:2]
    en = [[1500004.0, 5500020.0], [1500006.0, 5500026.0], [1500008.0, 5500020.0]]
    wall = {"id": 1, "kind": "wall", "name": "Wall 1", "material": "timber", "mode": "level", "top": 43.0, "tops": [43.0] * 3,
            "height": 0.6, "retains": "right", "back": 2.0, "pts": [[e - e0, 0.0, -(n - n0)] for e, n in en]}
    state = json.dumps({"note": "", "items": [wall]})
    with sync_api.sync_playwright() as p:
        if not os.path.exists(p.chromium.executable_path):
            pytest.skip("Playwright's Chromium isn't installed: uv run playwright install chromium")
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            errors = []
            page.on("console", lambda m: errors.append(m.text)
                    if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.add_init_script(f"localStorage.setItem({json.dumps('domus-sketch-' + model['title'])}, {json.dumps(state)});")
            page.goto(f"http://127.0.0.1:{port}/demo")
            back = page.locator('[data-k="back"]')
            back.wait_for(state="attached", timeout=60000)
            back.evaluate("(el) => { el.value = '3'; el.dispatchEvent(new Event('change')); }")
            said = page.locator("#status").text_content()
            kept = page.locator('[data-k="back"]').input_value()
            back = page.locator('[data-k="back"]')
            back.evaluate("(el) => { el.value = '1.5'; el.dispatchEvent(new Event('change')); }")
            shallower = page.locator('[data-k="back"]').input_value()
        finally:
            browser.close()
    assert said == "The ground behind Wall 1 crosses itself: move the wall's corners, or hold less ground behind it."
    assert kept == "2" and shallower == "1.5" and not errors
