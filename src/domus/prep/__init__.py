"""Turning a site's documents and photos into its data: `domus prep <step>`. See docs/from-documents.md.

Each step reads what to do from data/prep.json in the site project, so nothing about a site lives in the code.
"""

import importlib
import json
import sys

from ..crs import ROOT
from ..errors import DomusError

STEPS = {
    "plan-pages": ("plans", "pages", "Pull the page scans out of a PDF"),
    "plan-georef": ("plans", "georef", "Pin scanned sheets to the grid by their grid crosses"),
    "plan-trace": ("plans", "trace", "Trace a vector PDF's linework onto the grid"),
    "ifc": ("ifc", "main", "A designer's IFC model on the grid"),
    "feature-textures": ("textures", "feature_textures", "Cut fence, shed and paving textures from photos"),
    "wall-textures": ("textures", "wall_textures", "Cut wall textures from photos"),
    "house-textures": ("textures", "house_textures", "Rectify house walls from photos and placed video"),
    "check": ("check", "main", "Adversarial checks of the model against its data, the LiDAR and placed video"),
    "sections": ("sections", "main", "Cross-sections through the point clouds"),
    "canopy-plot": ("canopy_plot", "main", "A plan of candidate tree crowns over the aerial"),
}


def config(section):
    path = ROOT / "data" / "prep.json"
    if not path.exists():
        raise DomusError(f"This step reads its settings from {path}. See docs/from-documents.md.")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if section not in cfg:
        raise DomusError(f"{path} has no \"{section}\" section. See docs/from-documents.md.")
    return cfg[section]


def main(argv):
    if not argv or argv[0] not in STEPS:
        print("usage: domus prep <step> [args]\n\nSteps:")
        for name, (_, _, what) in STEPS.items():
            print(f"  {name:18} {what}")
        return 0 if not argv else 2
    module, func, _ = STEPS[argv[0]]
    sys.argv = [f"domus prep {argv[0]}", *argv[1:]]
    return getattr(importlib.import_module(f".{module}", __name__), func)()
