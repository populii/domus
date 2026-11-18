"""Wall options: one TOML file per scheme in options/ (files starting with "_" are skipped;
options/_site.toml holds the settings they share).

Each option lists its terraces, the walls that hold them and any steps; domus.options.design turns one into
designed ground and walls with levels, and compares it with the others. An option can also be another
one restyled: `based_on` names it, and `restyle` gives its walls other materials. See docs/options.md
for the format.
"""

import tomllib
from pathlib import Path

# How a wall is built, below and behind its face (docs/options.md), which goes with its block when a restyle gives it
# another material.
CONSTRUCTION = ("depth_m", "cap_depth_m", "cap_overhang_m", "embed_m", "embed_per_m", "embed_courses", "bedding_m",
                "base_m", "trench_m", "seal_m", "cut_min_m", "pins_per_unit", "pins_per_box", "core_void_m3", "pipe_m",
                "outlet_spacing_m", "geotextile", "post_spacing_m")


def options_dir(site):
    """The site's wall options, options/."""
    return site.path / "options"


def load_all(site):
    """The site's wall options, each as load() gives it."""
    out = []
    for path in sorted(options_dir(site).glob("*.toml")):
        if path.name.startswith("_"):
            continue
        out.append(load(path, site))
    return out


def restyled(cfg, path):
    """`cfg` as the option it is based on (a file beside it), with its own name, description and anything else
    it sets, and that option's walls changed as each `restyle` entry says: every wall, or those in its `walls`.
    Its steps change the same way with `restyle_stairs`: every flight, or those in an entry's `stairs`."""
    base = tomllib.loads(Path(path).with_name(f"{cfg['based_on']}.toml").read_text(encoding="utf-8"))
    if "based_on" in base:
        raise ValueError(f"{Path(path).name}: {cfg['based_on']} is itself based on another option; base it on that one")
    walls = [dict(w) for w in base.get("walls", [])]
    for rule in cfg.get("restyle", []):
        for w in walls:
            if "walls" not in rule or w["id"] in rule["walls"]:
                if "material" in rule:          # another material: the block the old one named goes, and how it's built
                    for k in ("unit_m", "cap_m", "colour", *CONSTRUCTION):
                        w.pop(k, None)
                w.update({k: v for k, v in rule.items() if k != "walls"})
    stairs = [dict(s) for s in base.get("stairs", [])]
    for rule in cfg.get("restyle_stairs", []):
        for s in stairs:
            if "stairs" not in rule or s["id"] in rule["stairs"]:
                s.update({k: v for k, v in rule.items() if k != "stairs"})
    own = {k: v for k, v in cfg.items() if k not in ("based_on", "restyle", "restyle_stairs")}
    return {**base, "stairs": stairs, **own, "walls": walls}


def current(site, cfg):
    """An option of the site's as loaded, at this Domus's format: an older site's are migrated in memory
    (domus.project.formats)."""
    from ..project import formats

    fmt = site.format()
    if fmt >= formats.FORMAT:
        return cfg
    from . import design

    return formats.option(cfg, fmt, design.settings(site).get("frame"))


def load(path, site):
    """The option in the file at `path`, one of the site's."""
    cfg = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    if "based_on" in cfg:
        cfg = restyled(cfg, path)
    cfg = current(site, cfg)
    return {
        **cfg,
        "id": Path(path).stem,
        "name": cfg.get("name", Path(path).stem),
        "short": cfg.get("short", cfg.get("name", Path(path).stem)),
        "description": cfg.get("description", ""),
        "walls": cfg.get("walls", []),
        "terraces": cfg.get("terraces", []),
        "stairs": cfg.get("stairs", []),
        "path": Path(path).relative_to(site.path).as_posix(),
    }
