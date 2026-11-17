# Contributing

```sh
uv sync
uv run pytest
uv run ruff check .
```

The tests run on the made-up demo site. Keep real sites out of this repo: their plans, photos and
addresses belong in their own folders.

## Proving a change left the model alone

Build a site before and after, with `DOMUS_OUTPUT` pointing somewhere different each time, then:

```sh
python tools/compare_builds.py BEFORE_DIR AFTER_DIR
```

It compares every value in the model and every texture, and fails on any difference.

## Releasing

Versions are calendar versions, `YEAR.MONTH.COUNT`: `2026.11.0` is the first release of November 2026, and `2026.11.1` the next.
Months use UTC, and there's no zero padding.

1. Write the notes under `## Unreleased` in `CHANGELOG.md`.
2. `python tools/release.py`, which works out the version, dates the notes, commits and tags.
3. `git push origin HEAD vYEAR.MONTH.COUNT`, as it prints.

The tag runs the tests, publishes `ghcr.io/populii/domus` as `YEAR.MONTH.COUNT`, `YEAR.MONTH` and `latest`, and makes
a GitHub release from the changelog.

Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md).
