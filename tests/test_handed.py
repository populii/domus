"""Every part of Domus is handed its site. Only the places a process starts from read the site the process was started
for (domus.project.site.started(), or start() for --site): the command line, the public API's open_site() and a
module's own `if __name__ == "__main__":`. This looks at the code itself, for parts the tests don't reach."""

import ast
from pathlib import Path

import domus

SRC = Path(domus.__file__).parent
STARTED = {"started", "start"}          # the site a process was started for
ALLOWED = {"project/site.py": STARTED, "app/cli.py": STARTED, "api.py": {"started"}}


def script(node):
    """Whether a module's statement is its `if __name__ == "__main__":` block, where it starts as a process."""
    return isinstance(node, ast.If) and "__name__" in ast.unparse(node.test) and "__main__" in ast.unparse(node.test)


def reads(tree):
    """Where a module's code reads the site the process was started for, outside its `__main__` block: (line, name)."""
    found = []
    for top in tree.body:
        for node in ast.walk(top):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else f.attr if (
                    isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "sites") else None
                if name in STARTED and not script(top):
                    found.append((node.lineno, name))
    return found


def test_no_part_reads_the_site_the_process_was_started_for():
    left = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        allowed = ALLOWED.get(rel, set())
        left += [f"{rel}:{line} {name}" for line, name in reads(ast.parse(path.read_text(encoding="utf-8")))
                 if name not in allowed]
    assert not left, "hand the part its Site (domus.project.site) instead:\n" + "\n".join(left)


def test_the_look_finds_them():
    code = ("from ..project import site as sites\nfrom ..project.site import start\n"
            "def f():\n    return sites.started(), start('.')\n"
            "if __name__ == '__main__':\n    f(sites.started())\n")
    assert sorted(name for _, name in reads(ast.parse(code))) == ["start", "started"]
