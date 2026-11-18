"""domus shots: its spec, cameras in the wall options' frame and on the grid, the PNGs it writes and the server it runs,
and, where Playwright's Chromium is installed, pictures of the made-up demo site."""

import http.client
import io
import json
import struct
import sys
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

from domus import shots
from domus.app import serve
from domus.errors import DomusError

ORIGIN = [1500011.0, 5500015.0, 37.0]
# The demo's frame: s south along the lot's east side from its north-east corner, o in from it, to the west.
FRAME = {"origin": [1500022.0, 5500030.0], "toward": [1500022.0, 5500000.0]}


def model(**summary):
    return {"origin": ORIGIN, "views": {"From the gate": {"pos": [1, 2, 3], "target": [0, 0, 0]}},
            "summary": {"options": [{"id": "a-one-wall"}], "options_site": {"frame": FRAME}, **summary},
            "layers": [{"id": "ground", "group": "Ground"}, {"id": "contours-major", "group": "Ground"},
                       {"id": "veg-trees", "group": "Vegetation"}, {"id": "lot"},
                       {"id": "option-a-one-wall-A1", "group": "Option: A. One wall", "option": "a-one-wall"}]}


def spec(*shots_, **more):
    return shots.check_spec({"shots": list(shots_), **more}, Path("base"))


def chunks(data):
    at, out = 8, []
    while at < len(data):
        length, kind = struct.unpack(">I4s", data[at:at + 8])
        out.append(kind.decode())
        at += 12 + length
    return out


def test_a_spec_takes_its_defaults():
    s = spec({"name": "now"})
    assert s["window"] == [1280, 720] and s["scale"] == 1 and s["hide"] == [] and s["out"] == Path("base")
    assert s["shots"] == [{"name": "now", "option": "", "view": "Overview", "pos": None, "target": None}]
    s = spec({"name": "a", "option": "a-one-wall", "pos_so": [1, 2, 40], "target_en": [1500011, 5500015, 37]},
             window=[640, 400], scale=2, hide=["Vegetation", "Vegetation"], out="pictures")
    assert s["window"] == [640, 400] and s["scale"] == 2 and s["hide"] == ["Vegetation"] and s["out"] == Path("base/pictures")
    assert s["shots"][0]["pos"] == ("so", [1.0, 2.0, 40.0]) and s["shots"][0]["target"] == ("en", [1500011.0, 5500015.0, 37.0])


def test_a_spec_is_read_from_its_file(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"out": "pics", "shots": [{"name": "now"}]}), encoding="utf-8")
    assert shots.read_spec(path)["out"] == tmp_path / "pics"
    with pytest.raises(DomusError, match="There's no"):
        shots.read_spec(tmp_path / "missing.json")
    path.write_text("{", encoding="utf-8")
    with pytest.raises(DomusError, match="isn't JSON"):
        shots.read_spec(path)


@pytest.mark.parametrize(("bad", "says"), [
    ({"shots": []}, "shots is a list"),
    ({"shots": [{"name": "a"}], "size": [1, 2]}, "has no size"),
    ({"shots": [{"name": "a"}], "window": [1280]}, "window is"),
    ({"shots": [{"name": "a"}], "window": [50, 720]}, "window is"),
    ({"shots": [{"name": "a"}], "scale": 0}, "scale is"),
    ({"shots": [{"name": "a"}], "hide": "Vegetation"}, "hide is"),
    ({"shots": [{"name": "../a"}]}, "name is"),
    ({"shots": [{"name": "a\n"}]}, "name is"),
    ({"shots": [{"view": "Plan"}]}, "name is"),
    ({"shots": [{"name": "a"}, {"name": "A"}]}, "Two shots are called A"),
    ({"shots": [{"name": "a", "opt": "x"}]}, "has opt"),
    ({"shots": [{"name": "a", "option": None}]}, "option is"),
    ({"shots": [{"name": "a", "view": "Plan", "pos": [0, 0, 0], "target": [0, 0, 0]}]}, "gives a view and pos"),
    ({"shots": [{"name": "a", "pos": [0, 0, 0]}]}, "needs one of target"),
    ({"shots": [{"name": "a", "pos": [0, 0, 0], "pos_so": [0, 0, 0], "target": [0, 0, 0]}]}, "not pos and pos_so"),
    ({"shots": [{"name": "a", "pos": [0, 0], "target": [0, 0, 0]}]}, "pos is three numbers"),
    ({"shots": [{"name": "a", "pos": [0, 0, "1"], "target": [0, 0, 0]}]}, "pos is three numbers"),
])
def test_a_spec_says_what_is_wrong(bad, says):
    with pytest.raises(DomusError, match=says):
        shots.check_spec(bad, Path("base"))


def test_cameras_come_into_viewer_metres():
    from domus.options.scheme import SiteFrame

    frame = SiteFrame(FRAME["origin"], FRAME["toward"])
    # 5 m along the east side from the north-east corner and 2 m in: E = E0 + 22 - 2, N = N0 + 30 - 5
    assert shots.to_viewer("so", [5.0, 2.0, 41.0], ORIGIN, frame) == pytest.approx([9.0, 4.0, -10.0])
    left = SiteFrame(FRAME["origin"], FRAME["toward"], "left")
    assert shots.to_viewer("so", [5.0, 2.0, 41.0], ORIGIN, left) == pytest.approx([13.0, 4.0, -10.0])
    assert shots.to_viewer("en", [1500014.0, 5500011.0, 39.5], ORIGIN) == pytest.approx([3.0, 2.5, 4.0])
    assert shots.to_viewer("viewer", [1.0, 2.0, 3.0], ORIGIN) == [1.0, 2.0, 3.0]


def test_shots_are_planned_against_the_model():
    planned = shots.plan(spec({"name": "a", "option": "a-one-wall", "pos_so": [5, 2, 41], "target": [0, 1, 0]},
                              {"name": "gate", "view": "From the gate"}, hide=["Vegetation", "Other"]), model())
    assert planned[0]["option"] == "a-one-wall" and planned[0]["pos"] == pytest.approx([9.0, 4.0, -10.0])
    assert planned[0]["target"] == [0.0, 1.0, 0.0] and planned[0]["view"] is None
    assert planned[1] == {"name": "gate", "option": "", "view": "From the gate"}
    assert shots.groups(model()) == ["Ground", "Other", "Vegetation"]


@pytest.mark.parametrize(("bad", "summary", "says"), [
    ({"name": "a", "option": "c-terraces"}, {}, "no wall option c-terraces: its options are a-one-wall"),
    ({"name": "a", "view": "Street"}, {}, "no view Street: its views are Overview, Plan, From the gate"),
    ({"name": "a", "pos_so": [0, 0, 0], "target": [0, 0, 0]}, {"options_site": {}}, "has no wall options"),
])
def test_a_plan_says_what_the_model_lacks(bad, summary, says):
    with pytest.raises(DomusError, match=says):
        shots.plan(spec(bad), model(**summary))


def test_a_shot_can_colour_its_option_by_cut_and_fill():
    s = spec({"name": "a", "option": "a-one-wall", "cutfill": True, "view": "Plan"}, {"name": "now", "cutfill": False})
    assert s["shots"][0]["cutfill"] is True and "cutfill" not in s["shots"][1]
    assert shots.plan(s, model()) == [{"name": "a", "option": "a-one-wall", "view": "Plan", "cutfill": True},
                                      {"name": "now", "option": "", "view": "Overview"}]
    with pytest.raises(DomusError, match="Shot a: cutfill is true or false"):
        spec({"name": "a", "option": "a-one-wall", "cutfill": "yes"})
    with pytest.raises(DomusError, match="Shot now: cutfill colours a wall option's ground .* so it needs an option"):
        spec({"name": "now", "cutfill": True})


def test_only_the_sites_own_groups_hide():
    with pytest.raises(DomusError, match="no group Option: A. One wall to hide: the groups are Ground, Other, Vegetation"):
        shots.plan(spec({"name": "a"}, hide=["Option: A. One wall"]), model())


def test_a_picture_keeps_the_image_and_drops_its_metadata():
    image = Image.new("RGB", (40, 30), (20, 120, 200))
    image.putpixel((3, 4), (250, 10, 10))
    info = PngImagePlugin.PngInfo()
    info.add_text("Software", "a browser")
    info.add_itxt("Comment", "taken somewhere", zip=True)
    exif = Image.Exif()
    exif[0x0110] = "a camera"
    raw = io.BytesIO()
    image.save(raw, "PNG", pnginfo=info, exif=exif, dpi=(96, 96))
    assert {"tEXt", "iTXt", "eXIf", "pHYs"} <= set(chunks(raw.getvalue()))
    bare = shots.bare_png(raw.getvalue())
    assert chunks(bare) == ["IHDR", "IDAT", "IEND"]
    again = Image.open(io.BytesIO(bare))
    assert again.tobytes() == image.tobytes() and not again.info and shots.png_size(bare) == (40, 30)
    # a palette and its transparency are part of the image
    raw = io.BytesIO()
    image.convert("P").save(raw, "PNG", transparency=0)
    assert set(chunks(shots.bare_png(raw.getvalue()))) == {"IHDR", "PLTE", "tRNS", "IDAT", "IEND"}
    with pytest.raises(DomusError, match="isn't a PNG"):
        shots.bare_png(b"GIF89a")
    with pytest.raises(DomusError, match="part way"):
        shots.bare_png(raw.getvalue()[:40])


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, r.read(), r.getheader("Content-Type")


def test_the_server_gives_the_viewer_and_the_model_and_nothing_else(tmp_path):
    built = tmp_path / "out"
    built.mkdir()
    (built / "model.json").write_text('{"title": "made up"}', encoding="utf-8")
    (tmp_path / "secret.txt").write_text("private", encoding="utf-8")
    with shots.serving(built) as url:
        port = int(url.rsplit(":", 1)[1].strip("/"))
        assert url == f"http://127.0.0.1:{port}/"
        status, body, kind = get(port, "/")
        assert status == 200 and body.startswith(serve.HEAD.encode()) and b'id="view"' in body and kind.startswith("text/html")
        assert get(port, "/data/model.json")[:2] == (200, b'{"title": "made up"}')
        status, _, kind = get(port, "/vendor/three/three.module.js")
        assert status == 200 and kind == "text/javascript"
        status, body, kind = get(port, "/vendor/manifold-3d/manifold.wasm")
        assert status == 200 and kind == "application/wasm" and body[:4] == b"\0asm"
        for path in ("/healthz", "/api/db/sketches/current", "/vendor/", "/data/../secret.txt", "/data/..%2fsecret.txt",
                     "/../secret.txt"):
            assert get(port, path)[0] == 404, path
    with pytest.raises(OSError):
        get(port, "/")


def test_the_page_is_the_one_domus_serve_sends():
    assert shots.HEAD == serve.HEAD


def test_without_playwright_it_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(DomusError, match=r"uv sync --extra shots, then uv run playwright install chromium"):
        shots.playwright()


def test_a_missing_model_says_to_build(tmp_path):
    with pytest.raises(DomusError, match="build the site first"):
        shots.take(spec({"name": "now"}), tmp_path)


def test_errors_read_plainly():
    page = Exception("Page.wait_for_function: Error: This model has no view Street.\n    at eval (eval at predicate)")
    assert shots.first_line(page) == "This model has no view Street."
    assert shots.Deadline(300).over("shot a") == "The pictures ran over their time limit, 300 s, at shot a. --timeout gives them longer."
    with pytest.raises(DomusError, match="ran over their time limit"):
        shots.Deadline(-1).ms()


@pytest.fixture(scope="session")
def chromium():
    """Skips a test unless Playwright and the Chromium it drives are installed here."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        if not Path(p.chromium.executable_path).exists():
            pytest.skip("Playwright's Chromium isn't installed: uv run playwright install chromium")


def test_the_docs_example_fits_the_demo(built):
    doc = (Path(__file__).resolve().parents[1] / "docs" / "shots.md").read_text(encoding="utf-8")
    example = json.loads(doc.split("```json\n", 1)[1].split("```", 1)[0])
    planned = shots.plan(shots.check_spec(example, Path("base")), shots.read_model(built))
    assert [s["option"] for s in planned] == ["", "a-one-wall", "b-two-tiers", "a-one-wall", "b-two-tiers", "b-two-tiers"]


def test_pictures_of_the_demo(chromium, built, tmp_path):
    import numpy as np

    said = []
    s = shots.check_spec({"window": [480, 300], "scale": 2, "out": "pictures", "shots": [
        {"name": "now"},
        {"name": "a", "option": "a-one-wall", "pos_so": [-6, 11, 46], "target_so": [12, 11, 41]},
        {"name": "b-plan", "option": "b-two-tiers", "view": "Plan"},
        {"name": "now-again", "option": "", "pos_en": [1500037, 5499990, 55], "target_en": [1500011, 5500015, 41]},
    ]}, tmp_path)
    taken = shots.take(s, built, timeout_s=240, say=said.append)
    assert [(p.name, showing) for p, showing in taken] == [
        ("now.png", "Existing site"), ("a.png", "A. One wall"), ("b-plan.png", "B. Two tiers"), ("now-again.png", "Existing site")]
    assert len(said) == 4 and sorted(p.name for p in (tmp_path / "pictures").iterdir()) == ["a.png", "b-plan.png", "now-again.png", "now.png"]
    pictures = {}
    for path, _ in taken:
        assert set(chunks(path.read_bytes())) == {"IHDR", "IDAT", "IEND"}
        image = Image.open(path).convert("RGB")
        assert image.size == (960, 600)
        colours = image.getcolors(maxcolors=960 * 600)
        assert len(colours) > 500, f"{path.name} has hardly anything in it"
        assert max(colours)[0] < 0.9 * 960 * 600, f"{path.name} is mostly one colour"
        pictures[path.stem] = np.asarray(image, dtype=int)
    # each shot moved the camera or showed another option, so no two pictures are the same
    assert len({p.tobytes() for p in pictures.values()}) == 4
    # the same view with the trees and lawns switched off: a good part of the picture changes
    s = shots.check_spec({"window": [480, 300], "scale": 2, "hide": ["Vegetation"], "out": "bare", "shots": [{"name": "now"}]},
                         tmp_path)
    (path, _), = shots.take(s, built, timeout_s=240, say=said.append)
    bare = np.asarray(Image.open(path).convert("RGB"), dtype=int)
    assert (np.abs(bare - pictures["now"]).max(axis=2) > 40).mean() > 0.02


def test_a_picture_of_an_option_coloured_by_its_cut_and_fill(chromium, built, tmp_path):
    """cutfill colours the option's ground as the viewer's switch does, blue where it's filled, and each shot sets the
    switch, so the colours don't carry into the next."""
    import numpy as np

    s = shots.check_spec({"window": [480, 300], "scale": 2, "shots": [
        {"name": "b", "option": "b-two-tiers", "view": "Plan"},
        {"name": "b-cut-fill", "option": "b-two-tiers", "cutfill": True, "view": "Plan"},
        {"name": "b-again", "option": "b-two-tiers", "view": "Plan"},
    ]}, tmp_path)
    taken = shots.take(s, built, timeout_s=240, say=lambda line: None)
    assert [(p.name, showing) for p, showing in taken] == [
        ("b.png", "B. Two tiers"), ("b-cut-fill.png", "B. Two tiers"), ("b-again.png", "B. Two tiers")]
    plain, coloured, again = (np.asarray(Image.open(p).convert("RGB"), dtype=int) for p, _ in taken)
    assert (np.abs(coloured - plain).max(axis=2) > 40).mean() > 0.02, "the option's ground isn't coloured"
    assert (np.abs(again - plain).max(axis=2) > 40).mean() < 0.002, "the colours carried into the next shot"
    # The demo's option B fills far more than it cuts, so its ground turns blue where it's filled: on the demo about 2%
    # of the picture, where the picture without the colours has under half a per cent of blue.
    bluish = lambda image: ((image[..., 2] - image[..., 0]) > 30).mean()
    assert bluish(coloured) > bluish(plain) + 0.01
