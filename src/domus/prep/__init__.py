"""Turning a site's documents and photos into its data: `domus prep <step>`. See docs/from-documents.md.

Each step reads what to do from data/prep.json in the site project, so nothing about a site lives in the code.
"""

import importlib
import json
import sys

from ..errors import DomusError

STEPS = {
    "plan-pages": ("plans", "pages", "Pull the page scans out of a PDF"),
    "plan-georef": ("plans", "georef", "Pin scanned sheets to the grid by their grid crosses"),
    "plan-trace": ("plans", "trace", "Trace a vector PDF's linework onto the grid"),
    "ifc": ("ifc", "main", "A designer's IFC model on the grid"),
    "feature-textures": ("textures", "feature_textures", "Cut fence, shed and paving textures from photos"),
    "wall-textures": ("textures", "wall_textures", "Cut wall textures from photos"),
    "house-textures": ("textures", "house_textures", "Rectify house walls from photos and placed video"),
    "boards": ("boards", "main", "The weatherboards' spacing and lap lines' heights on a rectified wall photo"),
    "check": ("domus.checks.model", "main", "Adversarial checks of the model against its data, the LiDAR and placed video"),
    "sections": ("sections", "main", "Cross-sections through the point clouds"),
    "profile": ("profile", "main", "The ground along a line, and either side of it"),
    "cloud": ("cloud", "main", "A placed cloud's ground against the model's or the LiDAR's, cell by cell"),
    "cloud-plan": ("cloud", "plan_main", "A placed cloud from above, in its own colours"),
    "face-fit": ("fitting", "face_main", "Fit vertical faces in a placed cloud, against the model's"),
    "frame-points": ("fitting", "points_main", "Where a placed frame's pixels meet a wall's plane or a level"),
    "overlay": ("overlay", "main", "The model's parts drawn over frames of a placed clip, to check their places"),
    "canopy-plot": ("canopy_plot", "main", "A plan of candidate tree crowns over the aerial"),
}


def config(site, section):
    """The `section` of the site's data/prep.json, which says what a step does."""
    path = site.path / "data" / "prep.json"
    if not path.exists():
        raise DomusError(f"This step reads its settings from {path}. See docs/from-documents.md.")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if section not in cfg:
        raise DomusError(f"{path} has no \"{section}\" section. See docs/from-documents.md.")
    return cfg[section]


def main(site, argv):
    """`domus prep <step> [args]` on the site: the step, handed the site."""
    if not argv or argv[0] not in STEPS:
        print("usage: domus prep <step> [args]\n\nSteps:")
        for name, (_, _, what) in STEPS.items():
            print(f"  {name:18} {what}")
        return 0 if not argv else 2
    module, func, _ = STEPS[argv[0]]
    sys.argv = [f"domus prep {argv[0]}", *argv[1:]]
    return getattr(importlib.import_module(module if module.startswith("domus.") else f".{module}", __name__), func)(site)
