# Your site

This folder is where one property's project lives in your copy of Domus. Domus comes in two parts:

- **Common** (`src/domus`): the tools, objects and patterns anyone can use, for any property, anywhere. It's the
  app the container ships.
- **Site** (this folder): one property's data, settings and wall options, plus any tools and tests written just
  for it.

Git ignores everything you add here, so your property's plans, photos, measurements and tools can't end up in the
public repo by accident. Only these generic examples are tracked:

| File | What it is |
|---|---|
| `README.md` | This guide |
| `AGENTS.md` | Headings for your property's own notes for agents: the rules only it has |
| `tools/example.py` | A small site tool that reads the site through Domus |
| `tests/conftest.py`, `tests/test_example.py` | Example checks of the site's data, run with pytest |

Copy an example before you change it. Your copy is yours and stays private; the tracked one stays generic.

## Start your property here

From the top of the repo, with an address:

```sh
uv run domus create "12 Example Street, Christchurch" site
```

Or with a boundary of your own:

```sh
uv run domus new site --name "My place" --boundary lot.geojson
uv run domus --site site fetch
uv run domus --site site build
```

Then have a look:

```sh
uv run domus --site site serve
```

[docs/new-site.md](../docs/new-site.md) walks through it, and [docs/site-format.md](../docs/site-format.md) lists
every file and setting.

## More than one property

Use `sites/<name>/` instead, which git ignores too, or keep each property in a folder or repository of its own. A
site works the same wherever it is: point Domus at it with `--site PATH` or `DOMUS_SITE`.

## What goes here, and what goes in common

Ask: would someone with a different property, somewhere else, want this unchanged? If so, it belongs in common,
with its numbers coming from settings or data, and a pull request is welcome. If it names your property, a survey,
a document or a product, or only makes sense for your site, it goes here.

When you're not sure, start here. A tool that turns out to be general can move into common later: give its numbers
settings, test it on the made-up sites, and document it. [ARCHITECTURE.md](../ARCHITECTURE.md) has the longer
version.

## Running the examples

```sh
uv run python site/tools/example.py site
uv run pytest site/tests
```

Both read the site in this folder, or the one `DOMUS_SITE` names.
