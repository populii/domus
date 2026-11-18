"""A built model for other software: `domus export PATH [--option ID] [--format glb|ifc]`, and `site.export` in the
public API (domus.api). See docs/export.md.

    domus export site.glb                     glTF, binary: Blender and most 3D viewers, phones among them
    domus export site.ifc --option a-one-wall IFC4: architects' and builders' software, with that option's walls

Both read the model as the site's last build wrote it (model.json and its pictures in the site's output folder) and
hold what the viewer shows (domus.export.scene): the site as it is, or with one wall option's layers in place of what
they replace. Neither builds the site: build it after changing it.
"""

import json
import os
from pathlib import Path

from .. import __version__
from ..errors import DomusError
from . import scene

FORMATS = {".glb": "glb", ".ifc": "ifc"}


def read_model(folder):
    """The built model in `folder` (model.json), or a DomusError if there isn't one this Domus can read."""
    path = folder / "model.json"
    if not path.exists():
        raise DomusError(f"{folder} has no model yet: build the site first (domus build), then export it.")
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise DomusError(f"{path} isn't JSON ({e}): build the site again.") from e
    if model.get("format") != scene.MODEL_FORMAT:
        raise DomusError(f"{path} is in the model's format {model.get('format')!r}, and this Domus exports format "
                         f"{scene.MODEL_FORMAT}: build the site again with this Domus.")
    return model


def format_of(path, format=None):
    """The format to write: `format` ("glb" or "ifc") if given, else the one the path's suffix says."""
    if format is not None:
        if format not in FORMATS.values():
            raise DomusError(f"Domus exports {' or '.join(FORMATS.values())}, not {format!r}.")
        return format
    found = FORMATS.get(Path(path).suffix.lower())
    if found is None:
        raise DomusError(f"{path}: say which format to write, by the file's suffix (.glb for glTF, .ifc for IFC) or "
                         "with --format glb or --format ifc.")
    return found


def georeference(site, model, option):
    """Where the model is: the site's grid and height datum, the model's origin on them, and what the file shows. It
    goes in a glTF's asset extras and an IFC's map conversion, so either can be placed on the grid again."""
    picked = scene.option_of(model, option)
    return {"title": model.get("title"), "built": model.get("built"), "domus": __version__,
            "crs": {"epsg": site.grid.epsg, "name": model.get("crs"), "vertical": model.get("vertical") or None},
            "origin": [float(v) for v in model["origin"]],
            "axes": "metres: x east, y up and z south of the origin; on the grid E = E0 + x, N = N0 - z, height = H0 + y",
            "option": {k: picked[k] for k in ("id", "name", "short")} if picked else None}


def write(site, path, format=None, option=None):
    """Writes the site's built model to `path` in `format` ("glb" or "ifc"; by default, the one its suffix
    says), with the wall option `option` (its id) shown, or the site as it is. Returns the file's path."""
    path = Path(path).resolve()
    kind = format_of(path, format)
    folder = site.output
    model = read_model(folder)
    georef = georeference(site, model, option)
    parts = scene.parts(model, option)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.part")
    try:
        if kind == "glb":
            from . import gltf

            gltf.write(parts, model, folder, temp, georef)
        else:
            from . import ifc

            e0, n0 = georef["origin"][:2]
            ifc.write(parts, model, temp, georef, site.grid.to_lonlat(e0, n0), name=path.name)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path
