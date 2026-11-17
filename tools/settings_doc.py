"""Writes the settings tables in docs/site-format.md from the settings schema (domus.project.schema).

    uv run python tools/settings_doc.py           # rewrites them
    uv run python tools/settings_doc.py --check   # exits 1 if the doc says something the schema doesn't

The tables sit between the doc's `<!-- settings: ... -->` markers; everything else in it is written by hand.
"""

import json
import re
import sys
from pathlib import Path

from domus.project import schema

DOC = Path(__file__).resolve().parents[1] / "docs" / "site-format.md"
START, END = "<!-- settings: start (tools/settings_doc.py writes this part) -->", "<!-- settings: end -->"


def default(key):
    if key.default == schema.REQUIRED:
        return "required"
    if key.default == schema.COMPUTED:
        return "worked out"
    if key.default is None:
        return ""
    return f"`{json.dumps(key.default)}`"


def rows(node, prefix):
    """Table rows for the keys of `node`, a Table, with their dotted names under `prefix`."""
    out = []
    for name, child in node.keys.items():
        full = f"{prefix}{name}"
        if isinstance(child, schema.Key):
            out.append(f"| `{full}` | {default(child)} | {child.help} |")
        elif isinstance(child, schema.Table):
            out.append(f"| `[{full}]` | | {child.help} |")
            out += rows(child, f"{full}.")
        elif isinstance(child, schema.Map):
            known = "".join(f"; `{k}`: {v.help}" + (f" ({default(v)})" if default(v) else "") for k, v in child.known.items())
            if isinstance(child.value, schema.Table):
                out.append(f"| `[{full}.<{child.name}>]` | | {child.help}{known} |")
                out += rows(child.value, f"{full}.<{child.name}>.")
            else:
                out.append(f"| `{full}.<{child.name}>` | {default(child.value)} | {child.help}{known} |")
        elif isinstance(child, schema.Tables):
            out.append(f"| `[[{full}]]` | | {child.help} |")
            out += rows(child.item, f"{full}[].")
    return out


def section(title, node):
    return [f"### {title}", "", "| Key | Default | What it is |", "|---|---|---|", *rows(node, ""), ""]


def tables():
    return "\n".join([*section("data/site.toml", schema.SITE), *section("options/_site.toml", schema.OPTIONS_SITE)])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    text = DOC.read_text(encoding="utf-8")
    m = re.search(re.escape(START) + r"\n(.*?)" + re.escape(END), text, flags=re.DOTALL)
    if not m:
        raise SystemExit(f"{DOC} has no {START} ... {END} markers")
    want = tables() + "\n"
    if "--check" in argv:
        if m[1] != want:
            print(f"{DOC.name}'s settings tables differ from the schema: run uv run python tools/settings_doc.py")
            return 1
        return 0
    DOC.write_text(text[:m.start(1)] + want + text[m.end(1):], encoding="utf-8", newline="\n")
    print(f"Wrote the settings tables in {DOC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
