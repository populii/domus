"""Every part of Domus is handed its site. No module reads the current site, or the old names that read it (crs.ROOT,
raster.dem() and the rest, kept for site code), but those names themselves; and only the places a process starts from
read the site the process was started for. Strict mode (tests/conftest.py) catches, as the tests run, a part called
without its site; this looks at the code itself, for parts the tests don't reach."""

import ast
from pathlib import Path

import domus

SRC = Path(domus.__file__).parent
OLD = {"crs": {"ROOT", "OUTPUT", "settings", "site_format", "site_epsg", "label", "site_to_nztm", "nztm_to_site",
               "site_to_lonlat", "datums", "to_model_height", "from_model_height"},
       "raster": {"DEM", "DSM", "AERIAL", "NIR", "shift", "dem", "dsm", "aerial", "nir"}}
CURRENT = {"current", "use"}            # the current site (current(), with no arguments), or making one current
STARTED = {"started", "start"}          # the site a process was started for
ALLOWED = {"geo/crs.py": OLD["crs"] | {"current"}, "geo/raster.py": OLD["raster"] | {"current"},
           "project/site.py": CURRENT | STARTED, "app/cli.py": STARTED, "api.py": {"started"}}


def script(node):
    """Whether a module's statement is its `if __name__ == "__main__":` block, where it starts as a process."""
    return isinstance(node, ast.If) and "__name__" in ast.unparse(node.test) and "__main__" in ast.unparse(node.test)


def reads(tree):
    """What a module's code reads of the site the old way: (line, name)."""
    found = []
    for top in tree.body:
        for node in ast.walk(top):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.attr in OLD.get(node.value.id, ()):
                found.append((node.lineno, node.attr))
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[-1] in OLD:
                found += [(node.lineno, a.name) for a in node.names if a.name in OLD[node.module.split(".")[-1]]]
            elif isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else f.attr if (
                    isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "sites") else None
                if (name == "current" and not node.args) or name == "use" or (name in STARTED and not script(top)):
                    found.append((node.lineno, name))
    return found


def test_no_part_reads_the_current_site_or_the_old_names():
    left = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        allowed = ALLOWED.get(rel, set())
        left += [f"{rel}:{line} {name}" for line, name in reads(ast.parse(path.read_text(encoding="utf-8")))
                 if name not in allowed]
    assert not left, "hand the part its Site (domus.project.site) instead:\n" + "\n".join(left)


def test_the_look_finds_them():
    code = ("from ..geo import crs\nfrom ..geo.raster import dem\nfrom ..project import site as sites\n"
            "def f():\n    return crs.ROOT, crs.settings(), current(), sites.started()\n"
            "if __name__ == '__main__':\n    f(sites.started())\n")
    assert sorted(name for _, name in reads(ast.parse(code))) == ["ROOT", "current", "dem", "settings", "started"]
    other = "from . import options\ndef g(site, cfg):\n    return options.current(site, cfg)\n"     # an option at this format
    assert reads(ast.parse(other)) == []
