"""One route for every change to a site's data (docs/changes.md).

The viewer, agents, site tools and checks say what to change as intents (domus.project.intents, and
domus.project.ifc_intents for the house kept as an IFC file), such as "move wall W1 0.5 m east" or "set this level to
42.10". Applying them, as one edit:

- checks each against the site: its layer and feature are there, its setting is one Domus knows;
- writes each file it changes the safe way, all of them or none: a copy of the file as it was, read back; the new file
  written to a temporary file in the old one's own layout, read back, and checked to say what was asked and nothing
  else; then put in place. The house's IFC is written whole by IfcOpenShell, so its inverse is the copy;
- records it in .domus/edits/<sequence>-<kind>.json: who, when (in UTC), why, the intents, each file's SHA-256 before
  and after and where its copy is, and the inverse, the intents that put back exactly what it changed;
- and says what to build again to see it.

`undo` applies an edit's inverse, but only while each file it changed is as the edit left it (the same SHA-256), so it
never undoes more than the edit did; it gives back each file byte for byte, and is recorded as an edit too, which can
be undone in turn. A check or a tool that finds something to change proposes it (.domus/proposals/<id>.json: its
source, why, and its intents). Nothing applies itself: a person accepts a proposal, which applies its intents through
this route with the proposal as their basis, or rejects it, saying why.

One change at a time: each holds the site's .domus/edits/.lock while it reads and writes.
"""

import copy
import datetime
import getpass
import hashlib
import json
import os
import re
import shutil
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path

from ..errors import DomusError
from . import ifc_intents
from . import intents as kinds

RECORD = re.compile(r"(\d+)-([a-z][a-z-]*)\.json")
PROPOSAL = re.compile(r"(\d+)\.json")
LOCK_WAIT_S = 30.0          # how long a change waits for another to finish
LOCK_STALE_S = 300.0        # a lock older than this was left by a change that stopped part way
NEW_LAYER = (1, (",", ": "), True, True)        # how Domus writes a layer: one-space indent and a final newline
# What the build draws from each file, to say what building again changes.
DRAWS = {"data/site.toml": "the whole model", "options/_site.toml": "the wall options",
         "boundary": "the lot and its boundary, and the wall options",
         "house": "the house, and the wall options' checks against it",
         "walls-existing": "the existing walls, the ground their faces and tops are cut into, and the wall options",
         "ground-fixes": "the ground, and the wall options", "services": "the drains, and the wall options' checks of them",
         "trees": "the plants", "title": "the title and its easements", "paved-under-canopy": "the aerial photo's paving",
         "features-survey-marks": "the survey marks",
         "data/house.ifc": "the house, where [house] source = \"ifc\" draws it from this file"}


def now():
    """The time now, in UTC, to the second: 2026-11-18T09:12:03Z."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_who():
    """Who's making a change when nobody says: the name this computer knows them by."""
    try:
        return getpass.getuser()
    except (OSError, KeyError, ImportError):    # a container's user may have no name
        return "someone"


def _sha(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def edits_dir(site):
    return site.path / ".domus" / "edits"


def proposals_dir(site):
    return site.path / ".domus" / "proposals"


@contextmanager
def _lock(site):
    """The site's change lock, held while a change reads and writes its files."""
    folder = edits_dir(site)
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / ".lock"
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > LOCK_STALE_S
            except FileNotFoundError:
                continue
            if stale:
                lock.unlink(missing_ok=True)
            elif time.monotonic() > deadline:
                raise DomusError(f"Another change to this site is being made: try again when it's done ({lock})") from None
            else:
                time.sleep(0.1)
    try:
        os.write(fd, f"{os.getpid()}\n".encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _write_json(path, value):
    """`value` as JSON in `path`, through a temporary file that's read back first."""
    tmp = Path(f"{path}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(value, indent=1) + "\n")
    if json.loads(tmp.read_text(encoding="utf-8")) != value:
        raise DomusError(f"{path} doesn't read back as written")
    os.replace(tmp, path)


def _next(folder, pattern):
    numbers = [int(m[1]) for p in folder.glob("*.json") if (m := pattern.fullmatch(p.name))] if folder.is_dir() else []
    return max(numbers, default=0) + 1


# --------------------------------------------------------------------------- a file's layout

def layout(raw):
    """How a JSON file is written, from its text: (indent, separators, ASCII only, final newline), so a file written
    again with json.dumps in that layout comes back as it was."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    body = text.strip()
    newline, ascii_ = text.endswith("\n"), text.isascii()
    if "\n" not in body:
        return None, ((", ", ": ") if '": ' in body else (",", ":")), ascii_, newline
    second = next((line for line in body.split("\n")[1:] if line.strip()), "")
    indent = "\t" if second.startswith("\t") else (len(second) - len(second.lstrip(" "))) or 1
    return indent, (",", ": "), ascii_, newline


def dump(data, lay):
    indent, separators, ascii_, newline = lay
    return json.dumps(data, indent=indent, separators=separators, ensure_ascii=ascii_) + ("\n" if newline else "")


# --------------------------------------------------------------------------- applying intents

class _File:
    """What an edit makes of one file: its text as it was (`raw`, None if there was none) and as it will be (`text`,
    None to take the file away), and what to check in what's written."""

    def __init__(self, rel, path, raw):
        self.rel, self.path, self.raw = rel, path, raw
        self.text = self.data = self.before = self.restore_sha = None
        self.restore = self.whole = False         # the house's IFC put back as it was, or written whole
        self.named = set()
        self.intents = []

    @property
    def name(self):
        return Path(self.rel).name

    @property
    def layer(self):
        return self.rel.startswith("data/site/")

    @property
    def ifc(self):
        return self.rel.endswith(".ifc")


def frame_of(site):
    """The site's wall options' frame, read when it's first needed: a function that gives it."""
    from ..options import design

    held = {}
    return lambda: held.setdefault("frame", design.frame(site))


def _setting_format(site):
    from . import formats

    fmt = site.format()
    if fmt < formats.FORMAT:
        raise DomusError(f"This site is in format {fmt}, and its settings are changed in this Domus's, {formats.FORMAT}: "
                         "domus site upgrade writes it up to date first")


def _plan(site, intents, say):
    """The files `intents` change and what each becomes: ([_File], the inverse). Nothing is written."""
    files, inverse, frame = {}, [], frame_of(site)
    for i in intents:
        rel = kinds.file_of(i)
        if rel not in files:
            path = site.path / rel
            f = files[rel] = _File(rel, path, path.read_bytes() if path.exists() else None)
            if f.ifc:
                if f.raw is None and i["kind"] not in ("ifc-restore", "ifc-write"):
                    raise DomusError(f"There's no {path}: domus house ifc writes it")
                if i["kind"] not in ("ifc-restore", "ifc-write"):
                    f.data = ifc_intents.parse(f.raw)
                # IfcOpenShell writes the file whole, so what puts it back is the file as it is now, the edit's copy,
                # or, where there's none yet, taking away the one the edit starts.
                inverse.insert(0, {"kind": "ifc-restore", "sha": hashlib.sha256(f.raw).hexdigest() if f.raw is not None
                                   else None})
            elif not f.layer:
                if f.raw is None:
                    raise DomusError(f"There's no {path}")
                _setting_format(site)
                import tomlkit

                f.data = tomlkit.parse(f.raw.decode("utf-8"))
            elif f.raw is None:
                if i["kind"] != "add":
                    raise DomusError(f"There's no {path}")
                from ..geo import geojson

                f.data = geojson.collection([], i["layer"], epsg=site.grid.epsg)
            else:
                try:
                    f.data = json.loads(f.raw)
                except ValueError as e:
                    raise DomusError(f"{path} isn't JSON: {e}") from None
                if not isinstance(f.data, dict) or not isinstance(f.data.get("features"), list):
                    raise DomusError(f"{path} isn't a GeoJSON FeatureCollection")
            if not f.ifc:
                f.before = copy.deepcopy(f.data) if f.layer else tomllib.loads(f.raw.decode("utf-8"))
        f = files[rel]
        f.intents.append(i)
        if f.ifc:
            if len(f.intents) > 1 and (f.restore or f.whole or i["kind"] in ("ifc-restore", "ifc-write")):
                raise DomusError(f"{rel}: putting it back as it was, or writing it whole, goes alone")
            if i["kind"] == "ifc-restore":
                f.restore, f.restore_sha = True, i["sha"]
            elif i["kind"] == "ifc-write":
                from ..model import house_ifc

                f.whole, f.text = True, house_ifc.draft(site, say)
            else:
                f.named |= ifc_intents.apply(f.data, i, site, say)
            continue
        if f.layer:
            back = kinds.apply(f.data, i, f.name, frame, say)
            f.named |= {x["id"] for x in back if x["kind"] in ("put", "delete")}      # each feature it changed, as named now
            f.named |= {x["feature"]["properties"]["id"] for x in back if x["kind"] == "add"}
            f.named |= {i["id"]} if "id" in i else set()
        else:
            back = kinds.apply_setting(f.data, i, say)
        last, then = (back[-1], inverse[0]) if back and inverse else (None, None)
        if last and last["kind"] == then["kind"] == "put" and last["layer"] == then["layer"] and \
                last["feature"]["properties"]["id"] == then["id"]:
            back[-1] = {**last, "feature": inverse.pop(0)["feature"]}       # one feature put back once, as it first was
        inverse = back + inverse
    for f in files.values():
        if f.ifc:
            f.text = ifc_intents.text(f.data) if f.data is not None else f.text
        elif f.layer:
            f.text = dump(f.data, layout(f.raw) if f.raw is not None else NEW_LAYER)
        else:
            import tomlkit

            f.text = tomlkit.dumps(f.data)
    gone = [f for f in files.values() if f.raw is None and f.restore and f.restore_sha is None]    # not there to take away
    changed = [f for f in files.values() if f not in gone and
               (f.raw is None or (f.text != f.raw if f.ifc else f.data != f.before if f.layer else f.text.encode("utf-8") != f.raw))]
    return changed, inverse


def _unset(data, key):
    """`data` (a settings dict) without the setting `key` (dotted), a copy."""
    out = copy.deepcopy(data)
    *tables, last = key.split(".")
    here = out
    for t in tables:
        here = here.get(t, {})
    here.pop(last, None)
    return out


def _str(text):
    return text.decode("utf-8") if isinstance(text, bytes) else text


def _by_id(fc):
    """A layer's features by their ids, each a list: features with no id, or one they share, are told apart by order."""
    out = {}
    for x in fc["features"]:
        out.setdefault(kinds._id(x), []).append(x)
    return out


def _check(f, written):
    """Raises a DomusError unless the file as written (`written`, its text read back; the house's IFC's bytes) says
    what was asked of it, and nothing else changed."""
    if f.ifc:
        if f.restore:
            if hashlib.sha256(written).hexdigest() != f.restore_sha:
                raise DomusError(f"{f.rel}: doesn't read back as it was: not written")
        elif f.whole:
            if written != f.text:
                raise DomusError(f"{f.rel}: doesn't read back as written: not written")
            ifc_intents.parse(written)
        else:
            ifc_intents.check(f.raw, written, f.named, f.intents)
        return
    if not f.layer:
        after = tomllib.loads(written)
        before = f.before
        for i in f.intents:
            before, after = _unset(before, i["key"]), _unset(after, i["key"])
        if before != after or tomllib.loads(_str(f.text)) != tomllib.loads(written):
            raise DomusError(f"{f.rel}: more changed than its settings named: not written")
        return
    after = json.loads(written)
    if after != f.data:
        raise DomusError(f"{f.name}: doesn't read back as written")
    groups, was = _by_id(after), _by_id(f.before)
    for fid in {*groups, *was} - f.named:
        if groups.get(fid) != was.get(fid):
            raise DomusError(f"{f.name}: {fid} changed though it was not named: not written")
    by_id = {fid: xs[0] for fid, xs in groups.items()}
    for i in f.intents:
        if i["kind"] == "move":
            _check_move(f, i, by_id)
        elif i["kind"] == "level":
            was = next(x for x in f.before["features"] if kinds._id(x) == i["id"])
            a, b = kinds._vertices(by_id[i["id"]]["geometry"]), kinds._vertices(was["geometry"])
            if len(a) != len(b) or any(p[:2] != q[:2] for p, q in zip(a, b, strict=True)):
                raise DomusError(f"{f.name}: {i['id']}: its points moved in plan: not written")


def _check_move(f, i, by_id):
    """The named features moved by exactly the vector, as `domus site shift` has always checked."""
    de, dn, *rest = i["by"]
    dz = rest[0] if rest else 0.0
    if len(f.data["features"]) != len(f.before["features"]):
        raise DomusError(f"{f.name}: feature count changed")
    named = None if i["ids"] == "*" else set(i["ids"])
    for fb in f.before["features"]:
        pid = kinds._id(fb)
        if named is not None and pid not in named:
            continue
        ca, cb = kinds._flat(by_id[pid]["geometry"]["coordinates"], []), kinds._flat(fb["geometry"]["coordinates"], [])
        if len(ca) != len(cb):
            raise DomusError(f"{f.name}: {pid} changed its number of points")
        for pa, pb in zip(ca, cb, strict=True):
            if abs(pa[0] - pb[0] - de) > 0.0011 or abs(pa[1] - pb[1] - dn) > 0.0011 or \
                    (len(pa) > 2 and abs(pa[2] - pb[2] - dz) > 0.0011) or pa[3:] != pb[3:]:
                raise DomusError(f"{f.name}: {pid} did not move as meant")


def _same_as(f, old):
    """Whether the bytes `old` say what `f` will: the same features, or the same settings; for the house's IFC, the
    file it puts back, by its SHA-256."""
    if f.ifc:
        return f.restore and f.restore_sha is not None and hashlib.sha256(old).hexdigest() == f.restore_sha
    try:
        if f.layer:
            return json.loads(old) == json.loads(f.text)
        return tomllib.loads(old.decode("utf-8")) == tomllib.loads(_str(f.text))
    except ValueError:
        return False


def _write(site, plans, copies, given_backup, say, written):
    """Writes each file the safe way, all of them or none, and gives each one's copy."""
    kept, said = {}, {}
    for f in plans:
        if f.raw is None:
            continue
        copy_path = Path(given_backup) / f.name if given_backup is not None else copies / f.rel
        shown = copy_path if given_backup is not None else _rel(site, copy_path)
        if copy_path.exists():
            raise DomusError(f"{copy_path} is already there: use a fresh backup folder")
        copy_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f.path, copy_path)
        if copy_path.read_bytes() != f.raw:
            raise DomusError(f"backup of {f.name} does not read back the same")
        kept[f.rel] = copy_path
        said[f.rel] = shown
    temps = []
    try:
        for f in plans:
            if f.text is None:
                continue
            tmp = Path(f"{f.path}.tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "wb") as fh:
                fh.write(f.text if isinstance(f.text, bytes) else f.text.encode("utf-8"))
            temps.append(tmp)
            _check(f, tmp.read_bytes() if f.ifc else tmp.read_text(encoding="utf-8"))
        for f in plans:
            if _sha(f.path) != (hashlib.sha256(f.raw).hexdigest() if f.raw is not None else None):
                raise DomusError(f"{f.rel} changed while this change was being made: nothing written, try again")
    except BaseException:
        for tmp in temps:
            tmp.unlink(missing_ok=True)
        raise
    done = []
    try:
        for f in plans:
            if f.text is None:
                f.path.unlink()
            else:
                os.replace(Path(f"{f.path}.tmp"), f.path)
            done.append(f)
    except BaseException:
        for f in done:                  # put back what was put in place, from its copy
            if f.raw is None:
                f.path.unlink(missing_ok=True)
            else:
                f.path.write_bytes(f.raw)
        for tmp in temps:
            tmp.unlink(missing_ok=True)
        raise
    for f in plans:
        if written is not None:
            say(written(f.name, said.get(f.rel), json.loads(f.text) if f.layer and f.text is not None else None))
        elif f.text is None:
            say(f"{f.rel}: taken away, as it wasn't there before")
        else:
            say(f"{f.rel}: written" + (f"; the file as it was is in {said[f.rel]}" if f.rel in said else ""))
    return kept


def draws(rel):
    """What in the model the build draws from a site's file."""
    if rel in DRAWS:
        return DRAWS[rel]
    layer = Path(rel).stem
    return DRAWS.get(layer) or ("the site features" if layer.startswith("features-") else f"what the build draws from {layer}")


def _apply(site, intents, why, who, *, kind=None, basis=None, undoes=None, backup=None, dry_run=False, say=print,
           lines=True, written=None, restore=None):
    """Applies `intents` as one edit, the lock held. `restore` is {file: the bytes it had before the edit an undo
    undoes}, so the undo gives it back byte for byte. Gives the record, or None if nothing changed."""
    plans, inverse = _plan(site, intents, say if lines else (lambda line: None))
    rebuild = list(dict.fromkeys(draws(f.rel) for f in plans))
    if dry_run:
        say("dry run: nothing written")
        return {"intents": intents, "summary": [kinds.describe(i) for i in intents], "rebuild": rebuild,
                "files": [{"path": f.rel, "before": _sha(f.path)} for f in plans]}
    if not plans:
        say("Nothing to change: the site says that already.")
        return None
    for f in plans:
        old = (restore or {}).get(f.rel, False)
        if old is False:
            continue
        if old is None:                 # the edit undone made the file: an undo leaves it as empty as it found it
            if f.layer and f.data["features"]:
                raise DomusError(f"Undoing it would leave {f.rel} with features the edit didn't add: not written")
            f.text = None
        elif not _same_as(f, old):
            raise DomusError(f"Undoing it doesn't give back {f.rel} as it was: not written. This is a fault in Domus: "
                             "please report it.")
        else:
            f.text = old                # byte for byte as it was
    for f in plans:
        if f.ifc and f.restore and f.restore_sha is not None and f.text is None:
            raise DomusError(f"The copy of {f.rel} from before the edit isn't there as it was, so it can't be put back")
    folder = edits_dir(site)
    seq = _next(folder, RECORD)
    kind = kind or (intents[0]["kind"] if len({i["kind"] for i in intents}) == 1 else "edit")
    stem = f"{seq:04d}-{kind}"
    for orphan in folder.glob(f"{seq:04d}-*"):         # copies an edit that stopped part way left, with no record
        if orphan.is_dir():
            shutil.rmtree(orphan, ignore_errors=True)
    try:
        kept = _write(site, plans, folder / stem, backup, say, written)
    except BaseException:
        if backup is None:
            shutil.rmtree(folder / stem, ignore_errors=True)
        raise
    record = {"id": f"{seq:04d}", "kind": kind, "who": who or default_who(), "when": now(), "why": why,
              **({"basis": basis} if basis else {}), **({"undoes": undoes} if undoes else {}),
              "intents": intents, "summary": [kinds.describe(i) for i in intents],
              "files": [{"path": f.rel, "before": hashlib.sha256(f.raw).hexdigest() if f.raw is not None else None,
                         "after": _sha(f.path),
                         "copy": _rel(site, kept[f.rel]) if f.rel in kept else None} for f in plans],
              "inverse": inverse, "rebuild": rebuild}
    _write_json(folder / f"{stem}.json", record)
    say(f"Recorded as edit {record['id']} (.domus/edits/{stem}.json): domus site undo {seq} undoes it.")
    say(f"Build the model again to see it (domus build): this changes {'; '.join(rebuild)}.")
    return record


def _rel(site, path):
    path = Path(path).resolve()
    return path.relative_to(site.path).as_posix() if path.is_relative_to(site.path) else str(path)


def apply(site, intents, why, who=None, *, basis=None, backup=None, dry_run=False, say=print, lines=True, written=None):
    """Applies `intents` (as domus.project.intents.read gives them) to the site's files as one edit, and records it.
    `why` says why, `who` who (the computer's user unless given), `basis` what it rests on (such as the proposal it
    came from). The copies of the files as they were go into `backup` if it's given, a folder of their own, else beside
    the record. `dry_run` only says what would change. `lines` False keeps each intent's own lines to itself, and
    `written(name, copy, data)` gives the line said for each file written. Gives the record (with dry_run, what it
    would hold), or None when there's nothing to change."""
    if not isinstance(why, str) or not why.strip():
        raise DomusError("Say why: every change is recorded with its reason")
    with _lock(site):
        return _apply(site, intents, why.strip(), who, basis=basis, backup=backup, dry_run=dry_run, say=say, lines=lines,
                      written=written)


# --------------------------------------------------------------------------- the record

def _number(value, what):
    m = re.fullmatch(r"(\d+)(?:-[a-z][a-z-]*)?(?:\.json)?", str(value).strip())
    if not m:
        raise DomusError(f"{value!r} isn't {what}'s number")
    return int(m[1])


def records(site):
    """Every edit's record, oldest first, each with `in_effect` (False once an undo that's itself in effect has undone
    it) and `undone_by`."""
    folder = edits_dir(site)
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        if RECORD.fullmatch(path.name):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except ValueError as e:
                raise DomusError(f"{path} isn't JSON: {e}") from None
    out.sort(key=lambda r: int(r["id"]))
    undone = {}
    for r in reversed(out):             # an undo comes after what it undoes, so its own state is known first
        r["in_effect"] = r["id"] not in undone
        if r["in_effect"] and r.get("undoes"):
            undone[r["undoes"]] = r["id"]
        if r["id"] in undone:
            r["undone_by"] = undone[r["id"]]
    return out


def undo(site, edit_id, who=None, why=None, say=print):
    """Undoes edit `edit_id` (its number) by applying its inverse, while every file it changed is still as it left it,
    and records the undo as an edit of its own. Gives that record."""
    n = _number(edit_id, "an edit")
    with _lock(site):
        found = [r for r in records(site) if int(r["id"]) == n]
        if not found:
            raise DomusError(f"This site has no edit {n} (domus site edits lists them)")
        r = found[0]
        if not r["in_effect"]:
            raise DomusError(f"Edit {r['id']} is undone already, by edit {r['undone_by']}")
        later = [x["id"] for x in records(site) if int(x["id"]) > n and x["in_effect"]
                 and {f["path"] for f in x["files"]} & {f["path"] for f in r["files"]}]
        for f in r["files"]:
            if _sha(site.path / f["path"]) != f["after"]:
                raise DomusError(f"{f['path']} has changed since edit {r['id']}, so undoing it now could undo more than "
                                 "it did: " + (f"undo the edits after it first ({', '.join(later)})" if later else
                                              "it was changed outside this route, so change it back by hand"))
        restore = {}
        for f in r["files"]:
            if f["before"] is None:
                restore[f["path"]] = None
            elif f.get("copy") and _sha(site.path / f["copy"]) == f["before"]:
                restore[f["path"]] = (site.path / f["copy"]).read_bytes()
        why = why.strip() if isinstance(why, str) and why.strip() else f"Undoing edit {r['id']}: {r['why']}"
        return _apply(site, kinds.read(r["inverse"]), why, who, kind="undo", undoes=r["id"], say=say, restore=restore)


# --------------------------------------------------------------------------- proposals

def proposals(site, state=None):
    """Every proposal, oldest first: those in `state` (open, accepted or rejected) if it's given."""
    folder = proposals_dir(site)
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        if PROPOSAL.fullmatch(path.name):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except ValueError as e:
                raise DomusError(f"{path} isn't JSON: {e}") from None
    out.sort(key=lambda p: int(p["id"]))
    return [p for p in out if state is None or p["state"] == state]


def _against(site, intents):
    """What a proposal's intents rest on as the site is now: each feature they name, as a SHA-256 of it (None where it
    isn't there), each setting's value, and each element or storey of the house's IFC they name, as a SHA-256 of what
    it is. It's accepted only while these still hold."""
    out, read = {}, {}
    for i in intents:
        rel = kinds.file_of(i)
        if i["kind"] == "ifc-write":              # the house's IFC written whole rests on the file as it is
            out.setdefault((rel, "whole"), {"file": rel, "whole": True, "sha": _sha(site.path / rel)})
            continue
        if i["kind"].startswith("ifc-"):          # each element or storey of the house's IFC it names, as a SHA-256
            if rel not in read:
                path = site.path / rel
                read[rel] = ifc_intents.parse(path.read_bytes()) if path.exists() else None
            for found in ifc_intents.against(read[rel], [i]):
                what = "storey" if "storey" in found else "element"
                out.setdefault((rel, what, found[what]), found)
            continue
        if rel not in read:
            path = site.path / rel
            text = path.read_text(encoding="utf-8") if path.exists() else None
            if i["kind"] == "setting":
                read[rel] = tomllib.loads(text) if text is not None else {}
            else:
                read[rel] = json.loads(text) if text is not None else {"features": []}
        if i["kind"] == "setting":
            here = read[rel]
            for part in i["key"].split("."):
                here = here.get(part) if isinstance(here, dict) else None
            out.setdefault((rel, i["key"]), {"file": rel, "key": i["key"], "value": here})
            continue
        if i["kind"] == "move":
            ids = kinds._ids(read[rel]) if i["ids"] == "*" else i["ids"]
        else:
            ids = [i["feature"]["properties"]["id"] if i["kind"] == "add" else i["id"]]
        for fid in ids:
            found = [f for f in read[rel]["features"] if kinds._id(f) == fid]
            sha = hashlib.sha256(json.dumps(found[0], sort_keys=True).encode()).hexdigest() if found else None
            out.setdefault((rel, fid), {"file": rel, "id": fid, "sha": sha})
    return list(out.values())


def propose(site, intents, why, source, say=print):
    """Proposes `intents` for a person to accept or reject: `why` says why, `source` what proposed them (a check, an
    observation, a tool). They're checked against the site now, and nothing is changed. Gives the proposal."""
    for value, what in ((why, "why"), (source, "source")):
        if not isinstance(value, str) or not value.strip():
            raise DomusError(f"A proposal says its {what}")
    with _lock(site):
        _plan(site, intents, lambda line: None)          # one that can't be applied is refused now
        folder = proposals_dir(site)
        folder.mkdir(parents=True, exist_ok=True)
        seq = _next(folder, PROPOSAL)
        proposal = {"id": f"{seq:04d}", "made": now(), "source": source.strip(), "why": why.strip(), "intents": intents,
                    "summary": [kinds.describe(i) for i in intents], "against": _against(site, intents), "state": "open"}
        _write_json(folder / f"{seq:04d}.json", proposal)
    say(f"Proposed as {proposal['id']} (.domus/proposals/{proposal['id']}.json): {'; '.join(proposal['summary'])}. "
        f"domus site accept {seq} applies it; domus site reject {seq} --why ... turns it down.")
    return proposal


def _proposal(site, proposal_id):
    n = _number(proposal_id, "a proposal")
    path = proposals_dir(site) / f"{n:04d}.json"
    if not path.exists():
        raise DomusError(f"This site has no proposal {n} (domus site proposals lists them)")
    p = json.loads(path.read_text(encoding="utf-8"))
    if p["state"] != "open":
        done = p.get(p["state"], {})
        raise DomusError(f"Proposal {p['id']} was {p['state']} already, by {done.get('who')} on {done.get('when')}")
    return path, p


def accept(site, proposal_id, who=None, say=print):
    """Accepts a proposal: its intents applied through the route, as an edit whose basis is the proposal. Gives the
    edit's record (None if the site said it all already)."""
    with _lock(site):
        path, p = _proposal(site, proposal_id)
        intents = kinds.read(p["intents"])
        for was, now_ in zip(p.get("against", []), _against(site, intents), strict=False):
            if was != now_:
                what = f"{was['file']}: {was.get('id') or was.get('key') or was.get('element') or was.get('storey')}"
                raise DomusError(f"{what} has changed since proposal {p['id']} was made, so it may no longer say what was "
                                 "meant: reject it, and propose it again if it still holds")
        record = _apply(site, intents, p["why"], who, basis={"proposal": p["id"], "source": p["source"]}, say=say)
        p["state"] = "accepted"
        p["accepted"] = {"who": record["who"] if record else who or default_who(), "when": now(),
                         "edit": record["id"] if record else None}
        _write_json(path, p)
    say(f"Proposal {p['id']} accepted.")
    return record


def reject(site, proposal_id, why, who=None, say=print):
    """Rejects a proposal, recording who and why; nothing in the site changes. Gives the proposal."""
    if not isinstance(why, str) or not why.strip():
        raise DomusError("Say why the proposal is rejected")
    with _lock(site):
        path, p = _proposal(site, proposal_id)
        p["state"] = "rejected"
        p["rejected"] = {"who": who or default_who(), "when": now(), "why": why.strip()}
        _write_json(path, p)
    say(f"Proposal {p['id']} rejected: {why.strip()}")
    return p
