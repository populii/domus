"""tools/readme_pictures.py: the README's pictures of the made-up sites, and the list of what each shows."""

import io
import re
import struct
import sys
from pathlib import Path

from PIL import Image, PngImagePlugin

from domus import shots

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import made_up_sites
import readme_pictures


def chunks(data):
    at, out = 8, []
    while at < len(data):
        length, kind = struct.unpack(">I4s", data[at:at + 8])
        out.append(kind.decode())
        at += 12 + length
    return out


def test_every_picture_the_readme_shows_is_listed_and_of_a_made_up_site():
    pics = readme_pictures.pictures()
    shown = set(re.findall(r"docs/images/([\w-]+)\.png", (ROOT / "README.md").read_text(encoding="utf-8")))
    assert shown == {p["name"] for p in pics}
    assert {p["site"] for p in pics} <= set(readme_pictures.MAKE) <= set(made_up_sites.SITES)
    for p in pics:
        data = (ROOT / "docs" / "images" / f"{p['name']}.png").read_bytes()
        assert shots.png_size(data) == tuple(p["window"]), p["name"]
        assert set(chunks(data)) <= {"IHDR", "PLTE", "IDAT", "IEND"}, p["name"]


def test_its_specs_are_ones_domus_shots_takes(built):
    specs = readme_pictures.specs(readme_pictures.pictures())
    assert {site for site, _ in specs} == {"demo", "richer"}
    for (site, window), spec in specs.items():
        checked = shots.check_spec(spec, Path("base"))
        assert checked["window"] == list(window)
        if site == "demo":
            # its options and cameras fit the demo as it's built
            assert [s["name"] for s in shots.plan(checked, shots.read_model(built))] == [s["name"] for s in spec["shots"]]


def test_a_picture_goes_to_256_colours_with_no_metadata():
    image = Image.new("RGB", (64, 40))
    image.putdata([(x * 4, y * 6, (x * y) % 256) for y in range(40) for x in range(64)])
    info = PngImagePlugin.PngInfo()
    info.add_text("Software", "a browser")
    raw = io.BytesIO()
    image.save(raw, "PNG", pnginfo=info, dpi=(96, 96))
    small = readme_pictures.small_png(raw.getvalue())
    assert set(chunks(small)) == {"IHDR", "PLTE", "IDAT", "IEND"}
    again = Image.open(io.BytesIO(small))
    assert again.mode == "P" and again.size == (64, 40) and len(again.getcolors()) <= 256
    assert readme_pictures.small_png(raw.getvalue()) == small      # the same picture gives the same bytes
