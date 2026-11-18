"""Turning a site's documents and photos into its data: `domus prep <step>`. See docs/from-documents.md.

Each step reads what to do from data/prep.json in the site project, so nothing about a site lives in the code.
"""

import json

from ..errors import DomusError


def config(site, section):
    """The `section` of the site's data/prep.json, which says what a step does."""
    path = site.path / "data" / "prep.json"
    if not path.exists():
        raise DomusError(f"This step reads its settings from {path}. See docs/from-documents.md.")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if section not in cfg:
        raise DomusError(f"{path} has no \"{section}\" section. See docs/from-documents.md.")
    return cfg[section]
