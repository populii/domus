"""Observations: readings kept as data apart from the model, each checking an element of the model by its id
(docs/observations.md). A site keeps them in data/observations.json:

    {"observations": [
      {"id": "w1-top-east", "kind": "level", "name": "W1's top at its east end",
       "source": {"picks": "walk", "mark": "w1-top-east"},
       "target": {"layer": "walls-existing", "id": "W1", "what": "top", "vertex": 1},
       "uncertainty_m": 0.03, "note": "Read on two frames five seconds apart."},
      {"id": "walk-up", "kind": "placement", "name": "The walk, 0.3 m higher",
       "source": {"clouds": ["walk-colmap-site"], "pivot": [1500011.0, 5500022.0], "dz_m": 0.3}}]}

Each says what was read (`source`), what it checks (`target`) and how sure it is (`uncertainty_m`, metres). Nothing in
it is a copy: at every build Domus reads the value from the source as it is then (a clip's latest solved picks,
data/video/picks/<picks>-site.csv, by the mark's id) and the value it checks from the model as it is then, by the
element's id, so a clip solved again or a model changed carries its observations with it.

Kinds and their sources:
- level: a height. Source {"picks", "mark"}: the mark's height.
- point: a position. Source {"picks", "mark"}: the mark's east, north and height.
- placement: a reconstruction placed another way, shown as a cloud beside the model: source {"clouds": [...], "pivot":
  [E, N], "dz_m", "turn_deg"}, its clouds moved up by dz_m and turned anticlockwise by turn_deg about pivot.

Targets (none for a reading to show by itself, and for a placement):
- {"setting": "house.fl_upper"}: a level the settings give, such as a floor's.
- {"layer": "walls-existing", "id": "W1", "what": "top", "vertex": 1}: a vertex's height ("top" for a wall's top,
  "level" for any other's; vertex 0 unless given).
- {"layer": "walls-existing", "id": "W1", "what": "toe", "vertex": 1}: an existing wall's toe at a vertex, the level of
  the ground at its foot as the model takes it: its `toe_m` there, else the ground in front of its face.
- {"layer": "house", "id": "house-walls", "what": "plinth", "face": "north"}: the plinth's top on a face of the house,
  as the model draws it.
- {"what": "ground"}: the ground as the build draws it, under the reading.
- {"layer": "house", "id": "house-walls", "what": "position", "vertex": 0}: a vertex's place, for a point.

`name` labels it, `note` says anything else, and `lift_m` is how far above the reading its label stands (0.15 unless
given).

A site in format 4 or before says two kinds in data/site.toml: [[video.question_marks]], a clip's readings of the house's
levels, and [[video.compare]], two placements of a clip side by side. Domus reads them as observations (format 5's
step, in memory), and `domus site upgrade` writes them into data/observations.json.
"""

import json
import math
import re
import tomllib
from collections import Counter

from ..errors import DomusError

FILE = ("data", "observations.json")
KINDS = ("level", "point", "placement")
WHATS = {"level": ("top", "level", "toe", "plinth", "ground"), "point": ("position",)}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
LIFT_M = 0.15


def path(site):
    """The site's observations' file, data/observations.json."""
    return site.path.joinpath(*FILE)


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def from_settings(settings):
    """The observations a site in format 4 or before says in data/site.toml (`settings`, as read): its
    [[video.question_marks]] as levels with no target yet, then its [[video.compare]] as placements, each with an id
    of its own. A question mark's mark keeps its id; one used by more than one question mark gives each observation
    the picks' name after it."""
    video = settings.get("video") if isinstance(settings.get("video"), dict) else {}
    marks = [m for m in video.get("question_marks", []) if isinstance(m, dict)]
    placements = [c for c in video.get("compare", []) if isinstance(c, dict)]
    times = Counter(str(m.get("id")) for m in marks)
    taken, out = set(), []

    def unique(base):
        base = re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-.")[:56] or "observation"
        name, n = base, 1
        while name in taken:
            n += 1
            name = f"{base}-{n}"
        taken.add(name)
        return name

    for m in marks:
        mark, picks = str(m.get("id")), str(m.get("picks", ""))
        stem = picks.removesuffix("-site.csv")
        obs = {"id": unique(mark if times[mark] == 1 else f"{mark}-{stem.removesuffix('.csv')}"), "kind": "level",
               "name": str(m.get("text", mark)), "source": {"picks": stem, "mark": mark}, "target": None}
        if m.get("drawn"):
            obs["note"] = f"drawn: {m['drawn']}"
        if "lift_m" in m:
            obs["lift_m"] = m["lift_m"]
        out.append(obs)
    for c in placements:
        source = {"clouds": list(c.get("clouds", []))}
        source.update({k: c[k] for k in ("pivot", "dz_m", "turn_deg") if k in c})
        out.append({"id": unique(str(c.get("id"))), "kind": "placement", "name": str(c.get("name", c.get("id"))),
                    "source": source})
    return out


def _target(o, where):
    t = o.get("target")
    if t is None:
        return
    if not isinstance(t, dict):
        raise DomusError(f"{where}: its target is an element of the model, such as {{\"layer\": \"walls-existing\", "
                         "\"id\": \"W1\", \"what\": \"top\", \"vertex\": 1}, or null")
    if o["kind"] == "placement":
        raise DomusError(f"{where}: a placement is shown beside the model, and has no target")
    if "setting" in t:
        if o["kind"] != "level" or not isinstance(t["setting"], str):
            raise DomusError(f"{where}: a setting's target, such as house.fl_upper, is a level's")
        from . import intents

        intents.setting_node({"file": "data/site.toml", "key": t["setting"]})
        return
    what = t.get("what")
    if what not in WHATS[o["kind"]]:
        raise DomusError(f"{where}: what it checks of its target is one of {', '.join(WHATS[o['kind']])} for a {o['kind']}")
    if what == "ground":
        return
    if not isinstance(t.get("layer"), str) or not isinstance(t.get("id"), str):
        raise DomusError(f"{where}: its target names a layer and an element's id in it")
    if what == "plinth" and not isinstance(t.get("face"), str):
        raise DomusError(f"{where}: a plinth's target names the face, such as north")
    if "vertex" in t and not (isinstance(t["vertex"], int) and not isinstance(t["vertex"], bool) and t["vertex"] >= 0):
        raise DomusError(f"{where}: vertex is the vertex's number, counted from 0")


def checked(o, where):
    """Raises a DomusError, saying what, unless `o` is an observation as docs/observations.md describes."""
    if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not ID.fullmatch(o["id"]):
        raise DomusError(f"{where}: an observation has an id: letters, digits, '.', '-' and '_'")
    where = f"{where}: {o['id']}"
    if o.get("kind") not in KINDS:
        raise DomusError(f"{where}: its kind is one of {', '.join(KINDS)}")
    s = o.get("source")
    if o["kind"] == "placement":
        if not (isinstance(s, dict) and isinstance(s.get("clouds"), list) and s["clouds"]
                and all(isinstance(c, str) for c in s["clouds"])):
            raise DomusError(f"{where}: a placement's source names its clouds in data/video/")
        if any(k in s and not _number(s[k]) for k in ("dz_m", "turn_deg")) or \
                ("pivot" in s and not (isinstance(s["pivot"], list) and len(s["pivot"]) == 2 and all(map(_number, s["pivot"])))):
            raise DomusError(f"{where}: a placement moves its clouds by numbers: dz_m, turn_deg about pivot [E, N]")
        if s.get("turn_deg") and "pivot" not in s:
            raise DomusError(f"{where}: a placement that turns gives the pivot it turns about")
    elif not (isinstance(s, dict) and isinstance(s.get("picks"), str) and s["picks"] and isinstance(s.get("mark"), str)):
        raise DomusError(f"{where}: its source is a mark in a clip's picks: {{\"picks\": \"walk\", \"mark\": \"...\"}}")
    _target(o, where)
    u = o.get("uncertainty_m")
    if u is not None and not (_number(u) and u >= 0):
        raise DomusError(f"{where}: uncertainty_m is how sure the reading is, in metres")
    for key in ("name", "note"):
        if key in o and not isinstance(o[key], str):
            raise DomusError(f"{where}: its {key} is text")
    if "lift_m" in o and not _number(o["lift_m"]):
        raise DomusError(f"{where}: lift_m is a number of metres")
    return o


def read(site):
    """The site's observations, checked: data/observations.json's, and for a site in format 4 or before, those its
    data/site.toml says (format 5's step). Each id is the site's only one."""
    found, where = [], []
    file = path(site)
    if file.exists():
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except ValueError as e:
            raise DomusError(f"{file} isn't JSON: {e}") from None
        if not isinstance(data, dict) or not isinstance(data.get("observations"), list):
            raise DomusError(f"{file} holds {{\"observations\": [...]}} (docs/observations.md)")
        found += data["observations"]
        where += ["data/observations.json"] * len(data["observations"])
    if site.format() < 5:
        moved = from_settings(tomllib.loads(site.settings_file.read_text(encoding="utf-8")))
        found += moved
        where += ["data/site.toml"] * len(moved)
    seen = Counter(o.get("id") if isinstance(o, dict) else None for o in found)
    for o, w in zip(found, where, strict=True):
        checked(o, w)
        if seen[o["id"]] > 1:
            raise DomusError(f"{w}: two observations have the id {o['id']}")
    return found
