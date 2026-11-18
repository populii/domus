"""Add, move or drop the marks in a picks file (data/video/picks/<clip>.json), keeping its layout and a backup.

    domus video picks-edit PICKS.json EDITS.json

EDITS.json: {"FEATURE-ID": {"desc": "what it is (replaces the description; a new feature needs one)",
                            "set": {"t030.000": [600.0, 20.0], ...}, "drop": ["t001.000", ...]}, ...}
Marks are kept in time order. The file is written to a temporary file, read back and compared before it replaces the
old one, and the old one is kept beside it as PICKS.json.bak-<n>.
"""

import json
import os
import shutil
from pathlib import Path

from ..errors import DomusError


def apply(data, edits, report=print):
    """Applies `edits` to the picks `data` in place, reporting each mark set."""
    by_id = {f["id"]: f for f in data["features"]}
    for fid, e in edits.items():
        if fid not in by_id:
            if "desc" not in e:
                raise DomusError(f"{fid}: a new feature needs a desc")
            by_id[fid] = {"id": fid, "desc": e["desc"], "obs": []}
            data["features"].append(by_id[fid])
        f = by_id[fid]
        if "desc" in e:
            f["desc"] = e["desc"]
        obs = {o["frame"]: o for o in f["obs"]}
        for frame in e.get("drop", []):
            if frame not in obs:
                raise DomusError(f"{fid}: no mark on {frame} to drop")
            del obs[frame]
        for frame, px in e.get("set", {}).items():
            was = obs.get(frame, {}).get("px")
            obs[frame] = {"frame": frame, "px": [float(px[0]), float(px[1])]}
            report(f"   {fid} {frame}: {was} -> {obs[frame]['px']}")
        f["obs"] = sorted(obs.values(), key=lambda o: float(o["frame"][1:]))
    return data


def write(path, data, raw):
    """Writes `data` to `path` safely: a backup of `raw` (what was there) that reads back, a temporary file that reads
    back as written, then the swap. Returns the backup's path."""
    path = Path(path)
    n = 1
    while Path(f"{path}.bak-{n}").exists():
        n += 1
    backup = Path(f"{path}.bak-{n}")
    shutil.copy2(path, backup)
    if backup.read_text(encoding="utf-8") != raw:
        raise DomusError("the backup doesn't read back")
    tmp = Path(f"{path}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, indent=1) + "\n")
    if json.loads(tmp.read_text(encoding="utf-8")) != data:
        raise DomusError("the new file doesn't read back as written")
    os.replace(tmp, path)
    return backup


def edit(picks, edits):
    """`domus video picks-edit`: the marks in the picks file `picks` added, moved or dropped as `edits` says (a
    dict, or a JSON file holding one), the old file kept beside it as <picks>.bak-<n>. Gives that backup."""
    raw = Path(picks).read_text(encoding="utf-8")
    edits = edits if isinstance(edits, dict) else json.loads(Path(edits).read_text(encoding="utf-8"))
    data = apply(json.loads(raw), edits)
    backup = write(picks, data, raw)
    print(f"written: {', '.join(f'{f['id']} {len(f['obs'])}' for f in data['features'])}; backup {backup}")
    return backup
