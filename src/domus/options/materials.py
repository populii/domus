"""What a wall option takes to build, to order from: its walls' units and what they stand in, the soil it moves, its
steps and its paving, worked out from the option as scheme.evaluate works it out. Net quantities, and beside each the
quantity to order with the site's allowance (`[materials] allowance_pct` in options/_site.toml, none unless it says).
No costs.

- A wall of named blocks (`unit_m`): its straight, corner and capping units, whole and cut, counted course by course
  from its set-out (walls.unit_count), with, as a check, the units its face takes by area. Where its option says how
  it's built (docs/options.md): the base course and the bedding along its trench, `trench_m` wide; the drainage
  aggregate behind its units, from its body's back to its trench's and from its first course up to the soil over
  it (`seal_m`, low-permeability soil); the aggregate in its units' cores, where it gives their volume
  (`core_void_m3`); its pins, two (`pins_per_unit`) for each unit under another course, and the boxes of them
  (`pins_per_box`); a perforated drainage pipe along its foot (`pipe_m`), with an outlet for each `outlet_spacing_m`
  of it; and filter fabric over its face's length and height (`geotextile`). Adhesive for its capping units, corner
  units and cut units, as needed.
- Any other wall: its length and face area, by material, and its posts where it gives their spacing
  (`post_spacing_m`): one at each end and each point of its line, and between them no further apart than that.
- Steps: a concrete flight's concrete as it's drawn, each tread down to 0.3 m under the one before; a flight of
  sleepers' two sleepers across each tread, and their count where `[materials] sleeper_m` gives a sleeper's length.
- Paving: each paved terrace's area, and its bedding sand and basecourse where it gives their depths
  (`paving_bedding_m`, `paving_base_m`).
- Soil: the cut, fill and net as the option's volumes count them, over the regrade area.

A volume shows its weight too where `[materials] density_t_m3` gives one for it (base, bedding, drainage,
paving_bedding, paving_base).

A pure part of the model: it reads no site.
"""

import math

import numpy as np

from ..model import walls as walls_mod

SLEEPERS_ON_A_TREAD = 2     # as Stair._sleepers draws a flight of sleepers
GROUPS = ("Blocks", "Walls", "Aggregate", "Drainage", "Steps", "Paving", "Soil")      # the list's order


def wall_numbers(w):
    """A new wall's length and its levels along it, for its materials (levelled by Design.wall_levels): (run, chain,
    top, retained, first): its top, the ground it holds, and where its first course stands (walls.foot)."""
    chain, toe = np.asarray(w["toe_chain"], float), np.asarray(w["toe"], float)
    top = np.interp(chain, w["top_chain"], w["top"])
    first = walls_mod.foot(w, chain, toe, clear=0.0)
    return float(chain[-1]) if len(chain) else 0.0, chain, top, np.asarray(w["retained"], float), first


def posts(line, spacing):
    """The posts a wall along `line` takes: one at each end and at each point of its line, as the drawing has them,
    and between them no further apart than `spacing`."""
    seg = np.hypot(*np.diff(np.asarray(line, float)[:, :2], axis=0).T)
    return sum(max(1, math.ceil(s / spacing - 1e-9)) for s in seg[seg > 1e-9]) + 1


def _area(chain, height):
    """The area under `height` (metres, none below 0) along `chain`."""
    return float(np.trapezoid(np.maximum(height, 0.0), chain)) if len(chain) > 1 else 0.0


def listed(walls, design, volumes, settings, seal_m3=None):
    """The option's materials: {"allowance_pct": ..., "rows": [...]}, each row {"group", "item", "net", "order",
    "unit"} with "tonnes" where a density is given and "note" where there's more to say. `walls` are the option's walls
    as scheme.evaluate gives them (their `def` levelled and set out), `design` the scheme.Design, `volumes` its cut
    and fill, and `settings` the site's options/_site.toml. `seal_m3`, where the cut and fill were measured as solids,
    is the soil over the walls' drainage they measured, part of the fill; else it's worked out wall by wall."""
    cfg = settings.get("materials", {}) or {}
    pct = float(cfg.get("allowance_pct", 0.0))
    density = dict(cfg.get("density_t_m3", {}) or {})
    rows = []

    def add(group, item, net, unit, note=None, density_key=None, ordered=True):
        if net is None or (isinstance(net, (int, float)) and net <= 0 and unit != "m3 soil"):
            return
        count = unit in ("units", "pins", "boxes", "outlets", "posts", "sleepers")
        order = net
        if ordered and pct:
            order = net * (1 + pct / 100)
        if count:
            net, order = int(math.ceil(net - 1e-9)), int(math.ceil(order - 1e-9))
        else:
            net, order = round(float(net), 2), round(float(order), 2)
        row = {"group": group, "item": item, "net": net, "order": order, "unit": unit.replace(" soil", "")}
        if density_key and density.get(density_key):
            row["tonnes"] = round(float(order) * float(density[density_key]), 2)
        if note:
            row["note"] = note
        rows.append(row)

    # Walls of named blocks, by block: their units counted from the set-out, and what they stand in.
    blocks = {}
    for w in walls:
        d = w["def"]
        unit = walls_mod.units(d)
        counted = walls_mod.unit_count(d) if unit else None
        if counted is None:
            continue
        b = blocks.setdefault(unit[:3], {"straight": [0, 0], "corner": [0, 0], "caps": [0, 0], "top": 0, "face": 0.0,
                                         "walls": [], "pins": 0.0, "boxes": 0.0, "cores": 0.0})
        for key in ("straight", "corner", "caps"):
            b[key][0] += counted[key][0]
            b[key][1] += counted[key][1]
        b["top"] += counted["top"]
        b["narrow"] = b.get("narrow", 0) + sum(1 for x in counted["cut_m"] if x < walls_mod.cut_min(d) - 1e-9)
        b["walls"].append(w["id"])
        _, chain, top, _, first = wall_numbers(d)
        b["face"] += _area(chain, top - unit[2] - first)               # from its first course to under its cap
        units_here = sum(counted["straight"]) + sum(counted["corner"])
        if d.get("pins_per_unit"):
            pins = d["pins_per_unit"] * max(units_here - counted["top"], 0)
            b["pins"] += pins
            if d.get("pins_per_box"):
                b["boxes"] += pins / d["pins_per_box"]                  # whole boxes of the lot, to order
        if d.get("core_void_m3"):
            b["cores"] += d["core_void_m3"] * units_here
    for (length, height, cap), b in blocks.items():
        size = f"{length * 1000:.0f} x {height * 1000:.0f} mm"
        which = ", ".join(b["walls"])
        by_area = b["face"] / (length * height)
        add("Blocks", f"Straight units, {size}, whole", b["straight"][0], "units", f"walls {which}")
        narrow = (f"{b['narrow']} of the pieces cut to fit are under the shortest a unit is cut to (`cut_min_m`), with "
                  "no whole unit beside them to share the cut: move a corner or an end (domus prep check blocks)") if b["narrow"] else None
        add("Blocks", f"Straight units, {size}, cut to fit", b["straight"][1], "units", narrow)
        add("Blocks", f"Corner units, {size}, whole", b["corner"][0], "units",
            "at each outside corner, each end, and each course's end where the top steps down")
        add("Blocks", f"Corner units, {size}, cut to fit", b["corner"][1], "units", None if b["straight"][1] else narrow)
        total = sum(b["straight"]) + sum(b["corner"])
        add("Blocks", "Units in all", total, "units",
            f"as a check, the face from the first course up to the capping units is {by_area:.0f} units' faces")
        add("Blocks", f"Capping units, {length * 1000:.0f} mm long, whole", b["caps"][0], "units")
        add("Blocks", f"Capping units, {length * 1000:.0f} mm long, cut to fit", b["caps"][1], "units")
        add("Blocks", "Pins", b["pins"], "pins", "for each unit under another course")
        add("Blocks", "Boxes of pins", b["boxes"], "boxes")
        add("Aggregate", "Drainage aggregate in the units' cores", b["cores"], "m3", density_key="drainage")
        if b["caps"][0] + b["caps"][1] or b["corner"][1] or b["straight"][1]:
            rows.append({"group": "Blocks", "item": "Concrete adhesive, exterior grade", "net": None, "order": None,
                         "unit": "as needed", "note": "for the capping units, the corner units and the cut units"})

    # What every new wall stands in, where its option says how it's built.
    built = {"base": 0.0, "bedding": 0.0, "drainage": 0.0, "seal": 0.0, "pipe": 0.0, "outlets": 0, "fabric": 0.0}
    for w in walls:
        d = w["def"]
        run, chain, top, retained, first = wall_numbers(d)
        trench, depth, seal = d["trench"], d["depth"], d["seal"]
        built["base"] += trench * d["base"] * run
        built["bedding"] += trench * d["bedding"] * run
        behind = max(trench - depth, 0.0)
        if behind > 0:
            built["drainage"] += behind * _area(chain, retained - seal - first)
            built["seal"] += behind * _area(chain, np.minimum(seal, retained - first))
        if d.get("pipe_m"):
            built["pipe"] += run
            built["outlets"] += max(1, math.ceil(run / float(d.get("outlet_spacing_m") or 15.0) - 1e-9))
        if d.get("geotextile"):
            built["fabric"] += _area(chain, top - first)
    add("Aggregate", "Base course, compacted", built["base"], "m3", "along each wall's trench", density_key="base")
    add("Aggregate", "Bedding", built["bedding"], "m3", "along each wall's trench", density_key="bedding")
    add("Aggregate", "Drainage aggregate behind the units", built["drainage"], "m3",
        "from each wall's back to its trench's, up to the soil over it", density_key="drainage")
    add("Soil", "Low-permeability soil over the drainage aggregate", built["seal"] if seal_m3 is None else seal_m3, "m3",
        "soil: part of the fill")
    pipes = sorted({float(w["def"]["pipe_m"]) for w in walls if w["def"].get("pipe_m")})
    add("Drainage", f"Perforated drainage pipe, {'/'.join(f'{p * 1000:.0f}' for p in pipes)} mm, in a filter sock",
        built["pipe"], "m", "along each wall's foot")
    add("Drainage", "Pipe outlets", built["outlets"], "outlets",
        "one for each outlet spacing of pipe or part of it, at least one a wall: at the low points")
    add("Drainage", "Filter fabric", built["fabric"], "m2", "each wall's length by its height from its first course")

    # Other walls: length and face, by material, and posts where a spacing is given.
    other = {}
    counted = {i for b in blocks.values() for i in b["walls"]}
    for w in walls:
        d = w["def"]
        if w["id"] in counted:
            continue
        o = other.setdefault(d["material"], {"length": 0.0, "face": 0.0, "posts": 0})
        o["length"] += w["length_m"]
        o["face"] += w["face_area_m2"]
        if d.get("post_spacing_m"):
            o["posts"] += posts(d["line"], float(d["post_spacing_m"]))
    for material, o in sorted(other.items()):
        add("Walls", f"{material.capitalize()} walls, length", o["length"], "m")
        add("Walls", f"{material.capitalize()} walls, face", o["face"], "m2")
        add("Walls", f"{material.capitalize()} walls, posts", o["posts"], "posts",
            "at each end and each point of its line, and no further apart than the spacing between")

    # Steps.
    concrete, sleepers, sleeper_m = 0.0, 0.0, float(cfg.get("sleeper_m") or 0.0)
    count = 0
    for st in design.stairs:
        going = st.length / st.n
        if st.material == "sleepers":
            sleepers += SLEEPERS_ON_A_TREAD * st.n * st.width
            if sleeper_m:
                count += SLEEPERS_ON_A_TREAD * st.n * math.ceil(st.width / sleeper_m - 1e-9)
        elif st.material == "concrete":
            concrete += sum(going * st.width * (top - (st.foot + st.rise / st.n * i - 0.3)) for i, top in enumerate(st.tops()))
    add("Steps", "Concrete for the steps", concrete, "m3", "as drawn: each tread down to 0.3 m under the one before")
    add("Steps", "Sleepers for the steps", sleepers, "m", "two across each tread")
    add("Steps", "Sleepers", count, "sleepers")

    # Paving.
    paved = {"area": 0.0, "bedding": 0.0, "base": 0.0}
    defaults = settings.get("defaults", {})
    for t in design.terraces:
        if t.kind != "paving":
            continue
        a = float(t.poly.area)
        paved["area"] += a
        paved["bedding"] += a * float(t.properties.get("paving_bedding_m", defaults.get("paving_bedding_m", 0.0)) or 0.0)
        paved["base"] += a * float(t.properties.get("paving_base_m", defaults.get("paving_base_m", 0.0)) or 0.0)
    add("Paving", "Pavers", paved["area"], "m2", "the paved terraces' area")
    add("Paving", "Bedding sand under the pavers", paved["bedding"], "m3", density_key="paving_bedding")
    add("Paving", "Basecourse under the paving", paved["base"], "m3", density_key="paving_base")

    # Soil, as the option counts it over the regrade area.
    add("Soil", "Cut", volumes["cut_m3"], "m3 soil", "over the regrade area", ordered=False)
    add("Soil", "Fill", volumes["fill_m3"], "m3 soil", "over the regrade area", ordered=False)
    add("Soil", "Fill less cut", volumes["net_m3"], "m3 soil", "to bring in, or to take away where it's less than nothing",
        ordered=False)
    rows.sort(key=lambda r: GROUPS.index(r["group"]))
    return {"allowance_pct": pct, "rows": rows}
