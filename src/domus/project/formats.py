"""Site formats: how each version of a site folder says things, and how an older one is read.

A site says its format in data/site.toml, `[domus] format`, and a site that doesn't say is in format 1. Domus reads
every older format by migrating it in memory, a step at a time, and keeps every step for good, so a site nobody has
upgraded keeps working, in a container as on a laptop. `domus site upgrade` writes the same steps into the site's
files and keeps their comments.

`[domus] requires`, such as ">=2026.12.0", names the oldest Domus that builds a site; an older one says so and stops.

Format 2:
- A compass word in an option means the site grid's north, as it does in sketches and house parts, and a terrace's
  `fall_to` can be a bearing too, in degrees clockwise from north. In format 1 the words ran along the options'
  frame: "north" back along its boundary, "east" towards it.
- `[front_garden]` in options/_site.toml is `[regrade]`.
- `[house] fl_lower` is written down, not worked out from `fl_upper_rel` and `fl_lower_rel`.
- `[services] printed_line` has gone: a drain's levels count by the document they come from (domus.model.drains).

Format 3:
- `[crs] epsg`, `[crs] vertical` and `[site] attribution` have no New Zealand defaults. A site in format 2 or before
  that leaves them out gets what it always had, written down: NZTM2000 (EPSG:2193), NZVD2016 and LINZ's credit line.

Format 4:
- How sure a datum's offset and the upper floor's level are, `[datums.<datum>] uncertainty_m` and `[house]
  fl_upper_uncertainty_m`, aren't Domus's settings: Domus never read them. A site keeps them as its own notes, under
  the same names in `[local]`: `[local.datums.<datum>] uncertainty_m` and `[local.house] fl_upper_uncertainty_m`.

Format 5:
- `[[video.question_marks]]`, a clip's readings of the house's levels shown as labels, and `[[video.compare]]`, two
  placements of a clip side by side, are observations, in data/observations.json (domus.project.observations): a
  question mark is a level with no target yet, its drawn level kept in its note, and a comparison a placement. They
  leave data/site.toml, and a [video] table they leave empty goes with them.
"""

import copy
import math
import re

from .. import __version__
from ..errors import DomusError

FORMAT = 5
COMPASS = {"north": (0.0, 1.0), "east": (1.0, 0.0), "south": (0.0, -1.0), "west": (-1.0, 0.0)}
# What format 2 and before took for a site that didn't say: the LINZ data's own grid, height datum and credit line.
NZTM, NZ_VERTICAL = 2193, "NZVD2016"
LINZ_CREDIT = "Ground model, surface model and aerial photo: LINZ and its data partners (CC BY 4.0)."


def format_of(site):
    """The format data/site.toml (`site`, as read) says it's in: 1 if it doesn't say."""
    return int(site.get("domus", {}).get("format", 1))


def check(site, version=__version__):
    """Raises a DomusError if this Domus can't read the site whose data/site.toml is `site`."""
    fmt = format_of(site)
    if fmt > FORMAT:
        raise DomusError(f"This site is in format {fmt}, and this Domus ({version}) reads formats up to {FORMAT}: "
                         "update Domus to open it.")
    requires = site.get("domus", {}).get("requires")
    if requires and not satisfies(version, str(requires)):
        raise DomusError(f"This site needs Domus {requires}, and this is {version}: update Domus to open it.")


def satisfies(version, spec):
    """Whether a version, YEAR.MONTH.COUNT, meets `spec`, such as ">=2026.12.0" or ">=2026.11.27,<2027.1.0"."""
    key = lambda v: tuple(int(x) for x in v.split("."))
    for clause in spec.split(","):
        m = re.fullmatch(r"\s*(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)\s*", clause)
        if not m:
            raise DomusError(f'[domus] requires = "{spec}" isn\'t a version this Domus understands: write it as ">=2026.12.0"')
        a, b = key(version), key(m[2])
        if not {">=": a >= b, "<=": a <= b, "==": a == b, "!=": a != b, ">": a > b, "<": a < b}[m[1]]:
            return False
    return True


# The steps. Each turns, in place, what one format's files say into what the next format's say, and returns notes on
# what it changed. A released step never changes: sites in that format read through it for good.

FORMAT_1_FALLS = {"north": (-1.0, 0.0), "south": (1.0, 0.0), "east": (0.0, -1.0), "west": (0.0, 1.0)}   # (s, o)


def _fall(terrace):
    """Whether a format 1 terrace falls at all ("1:50", 0.02, "2%"; nothing or 0 is level)."""
    f = terrace.get("fall", 0)
    if isinstance(f, str):
        f = f.strip()
        if f.endswith("%"):
            return float(f[:-1]) != 0
        if ":" in f:
            a, b = f.split(":")
            return float(a) / float(b) != 0
    return float(f) != 0


def _corner_levels(terrace):
    """Whether a terrace gives a level at each corner, rather than a sloping plane."""
    pts = terrace.get("polygon_so", terrace.get("polygon"))
    return bool(pts) and len(pts[0]) == 3


def _direction(v):
    """A direction on the site's grid as format 2 says it: a compass word if it is one, else a bearing in degrees."""
    for word, w in COMPASS.items():
        if abs(v[0] - w[0]) < 1e-9 and abs(v[1] - w[1]) < 1e-9:
            return word
    return math.degrees(math.atan2(v[0], v[1])) % 360.0


def site_1(site):
    """data/site.toml, format 1 to 2."""
    notes = []
    house = site.get("house")
    if house is not None and ("fl_upper_rel" in house or "fl_lower_rel" in house):
        fl_lower = round(float(house["fl_upper"]) - (float(house.get("fl_upper_rel", 0)) - float(house.get("fl_lower_rel", 0))), 6)
        house.pop("fl_upper_rel", None)
        house.pop("fl_lower_rel", None)
        house["fl_lower"] = fl_lower
        notes.append(f"[house] fl_lower = {fl_lower}, worked out from fl_upper_rel and fl_lower_rel")
    services = site.get("services")
    if services is not None and "printed_line" in services:
        services.pop("printed_line")
        notes.append("[services] printed_line dropped: a drain's levels count by the document they come from")
    return notes


def options_site_1(cfg):
    """options/_site.toml, format 1 to 2."""
    if "front_garden" not in cfg:
        return []
    garden = cfg.pop("front_garden")
    if "regrade" in cfg:
        return ["[front_garden] dropped: [regrade] was there already, and was the one read"]
    cfg["regrade"] = garden
    return ["[front_garden] is now [regrade]"]


def option_1(option, frame):
    """An option, format 1 to 2, given options/_site.toml's [frame]: each sloping terrace's fall_to becomes the
    compass word or bearing on the site's grid that it pointed along the frame."""
    if not frame:
        return []
    o, t = [float(x) for x in frame["origin"][:2]], [float(x) for x in frame["toward"][:2]]
    length = math.hypot(t[0] - o[0], t[1] - o[1])
    along = ((t[0] - o[0]) / length, (t[1] - o[1]) / length)
    side = 1.0 if frame.get("side", "right") == "left" else -1.0
    inward = (-along[1] * side, along[0] * side)
    notes = []
    for terrace in option.get("terraces", []):
        word = terrace.get("fall_to", "north")
        if _corner_levels(terrace) or not _fall(terrace) or not isinstance(word, str):
            continue
        ds, do = FORMAT_1_FALLS[str(word)]
        new = _direction((ds * along[0] + do * inward[0], ds * along[1] + do * inward[1]))
        terrace["fall_to"] = new
        if new != word:
            notes.append(f"terrace {terrace.get('id')}: fall_to {word!r} along the frame is {new!r} on the site's grid")
    return notes


def site_2(site):
    """data/site.toml, format 2 to 3: what format 2 took from New Zealand for a site that didn't say, written down."""
    notes = []
    for table, key, value in (("crs", "epsg", NZTM), ("crs", "vertical", NZ_VERTICAL), ("site", "attribution", LINZ_CREDIT)):
        if table not in site:
            site[table] = {}
        if key not in site[table]:
            site[table][key] = value
            notes.append(f"[{table}] {key} = {value!r}, which format 2 took for a site that didn't say")
    return notes


def _local(site, path):
    """The table [local.<path>] of `site`, made where it isn't there; None if something there already isn't a table."""
    table = site
    for key in ("local", *path):
        if key not in table:
            table[key] = {}
        table = table[key]
        if not isinstance(table, dict):
            return None
    return table


def site_3(site):
    """data/site.toml, format 3 to 4: how sure a datum's offset and the upper floor's level are, which Domus never
    read, become the site's own notes under [local], by the same names. One already there is kept, and one with
    something other than a table where it would go stays where it was."""
    datums = site.get("datums")
    moves = [(("datums", name), "uncertainty_m") for name, datum in (datums.items() if isinstance(datums, dict) else ())
             if isinstance(datum, dict) and "uncertainty_m" in datum]
    if isinstance(site.get("house"), dict) and "fl_upper_uncertainty_m" in site["house"]:
        moves.append((("house",), "fl_upper_uncertainty_m"))
    notes = []
    for path, key in moves:
        old, new = f"[{'.'.join(path)}] {key}", f"[local.{'.'.join(path)}] {key}"
        there = _local(site, path)
        if there is None:
            notes.append(f"{old} left where it was: [local] has something other than a table where it would go")
            continue
        here = site
        for p in path:
            here = here[p]
        value = here.pop(key)
        if key in there:
            notes.append(f"{old} dropped: {new} was there already, and is kept")
        else:
            there[key] = value
            notes.append(f"{old} is now {new}: the site's own note, which Domus never read")
    return notes


def site_4(site):
    """data/site.toml, format 4 to 5: [[video.question_marks]] and [[video.compare]] leave the settings, as
    observations (domus.project.observations.from_settings reads them, before this step), and a [video] table they
    leave empty goes with them."""
    video = site.get("video")
    if not isinstance(video, dict):
        return []
    notes = []
    for key, what in (("question_marks", "levels"), ("compare", "placements")):
        if key in video:
            n = len(video[key]) if isinstance(video[key], list) else 0
            del video[key]
            notes.append(f"[[video.{key}]]: {n} moved to data/observations.json, as observations ({what})")
    if notes and not len(video):
        del site["video"]
    return notes


# Reading in memory: each file at this Domus's format, from the format the site is in.

def site(raw):
    """data/site.toml as read (`raw`), checked and at this Domus's format."""
    check(raw)
    out = copy.deepcopy(raw)
    if format_of(raw) < 2:
        site_1(out)
    if format_of(raw) < 3:
        site_2(out)
    if format_of(raw) < 4:
        site_3(out)
    if format_of(raw) < 5:
        site_4(out)
    out.setdefault("domus", {})["format"] = FORMAT
    return out


def options_site(raw, fmt):
    """options/_site.toml as read (`raw`), from a site in format `fmt`, at this Domus's format."""
    out = copy.deepcopy(raw)
    if fmt < 2:
        options_site_1(out)
    return out


def option(raw, fmt, frame):
    """An option as loaded (`raw`), from a site in format `fmt`, at this Domus's format, given the [frame] of
    options/_site.toml."""
    out = copy.deepcopy(raw)
    if fmt < 2:
        option_1(out, frame)
    return out
