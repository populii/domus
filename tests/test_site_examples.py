"""site/ holds only its examples, and they work on the made-up demo site, so a copy of them starts from something that runs."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def run(*args, site):
    env = {**os.environ, "DOMUS_SITE": str(site), "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, *args], cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8",
                          timeout=600, check=False)


def test_the_example_tool_runs_on_the_demo(site):
    r = run("site/tools/example.py", str(site), site=site)
    assert r.returncode == 0, r.stderr
    assert "Demo site (made up)" in r.stdout


def test_the_example_tests_pass_on_the_demo(site):
    r = run("-m", "pytest", "-q", "-p", "no:cacheprovider", "site/tests", site=site)
    assert r.returncode == 0 and re.search(r"\b\d+ passed\b", r.stdout) and "skipped" not in r.stdout, r.stdout + r.stderr


def test_git_tracks_nothing_it_ignores():
    """A site's files can't slip in with `git add -f`: nothing tracked is something .gitignore ignores, so site/ holds
    only the examples its own .gitignore lets through."""
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    r = subprocess.run(["git", "ls-files", "--cached", "--ignored", "--exclude-standard"], cwd=REPO, capture_output=True,
                       text=True, check=True)
    assert r.stdout == ""
