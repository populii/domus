"""The command line: every command and step prints its usage with --help and does nothing else, and a command run
without a site says so, plainly."""

import argparse
import sys

import pytest

from domus.app import cli


def commands(ap, words=()):
    """Every command and step the command line has, as the words that name it: its parser's subcommands, all the way
    down, so a new one is here as soon as it's there."""
    yield words
    for action in ap._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from commands(sub, (*words, name))


EVERY = [" ".join(words) for words in commands(cli.parser())]


@pytest.fixture
def nowhere(tmp_path, monkeypatch):
    """An empty folder, current and named by DOMUS_SITE: there's no site, and nowhere else a command could write."""
    from domus import api
    from domus.project import site as sites

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOMUS_SITE", str(tmp_path))
    monkeypatch.delenv("DOMUS_OUTPUT", raising=False)
    monkeypatch.setattr(sites, "_started", None)
    monkeypatch.setattr(api, "_opened", {})
    monkeypatch.setattr(sys, "argv", list(sys.argv))        # a step is handed its arguments in sys.argv
    return tmp_path


def run(words):
    try:
        return cli.main(words)
    except SystemExit as e:
        return e.code


def test_the_command_line_has_its_commands_and_their_steps():
    for words in ("", "build", "site upgrade", "video merge", "video export", "prep canopy-plot", "sketch-worker"):
        assert words in EVERY
    assert len(EVERY) > 50


@pytest.mark.parametrize("words", EVERY, ids=lambda words: words or "domus")
def test_every_command_and_step_prints_its_usage_and_does_nothing_else(words, nowhere, capsys):
    assert run([*words.split(), "--help"]) in (0, None)
    assert "usage:" in capsys.readouterr().out
    assert not list(nowhere.iterdir())


@pytest.mark.parametrize("words", ["build", "fetch", "canopy", "marks", "title", "points --ground", "export model.glb",
                                   "scan walk.ply --tag walk --epsg 2193", "shots shots.json", "site upgrade", "site trust",
                                   "site shift spec.json backup", "video export", "prep canopy-plot"])
def test_a_command_without_a_site_says_so(words, nowhere, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(words.split())
    assert "isn't a site project: it has no data/site.toml" in str(e.value.code)
    assert "Traceback" not in capsys.readouterr().err
    assert not list(nowhere.iterdir())


def test_a_step_takes_what_follows_it_as_it_is(nowhere, monkeypatch):
    from domus import capture

    handed = []
    monkeypatch.setattr(capture, "main", lambda site, argv: handed.append(argv) or 0)
    assert run(["video", "place", "--tag", "x", "walk", "--via", "other"]) == 0
    assert run(["video"]) == 0
    assert handed == [["place", "--tag", "x", "walk", "--via", "other"], []]
    assert run(["build", "--fresh", "--frsh"]) == 2      # a command's own arguments are the command line's to check
