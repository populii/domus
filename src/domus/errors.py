"""The error Domus raises for something a person can put right: a missing setting, a file in a format it can't read,
a boundary that isn't a polygon. Its message says what, and where.

Library code raises it rather than ending the program, so a site tool, a test or the server can catch it. The
command line shows just the message, as a command's own errors.

And the warning Domus gives when site code uses a name it's replacing: a DomusDeprecationWarning, which names the
replacement and points at the line of site code that used the old name.
"""

import importlib
import os
import warnings

# The frames a deprecation warning passes over to reach the code that used the old name: Domus's own, and the import
# system's (an old module name is used by importing it, with an import statement or importlib.import_module()).
NOT_THE_CALLER = (os.path.dirname(__file__) + os.sep, os.path.dirname(importlib.__file__) + os.sep, "<frozen importlib")


class DomusError(Exception):
    """Something about a site's data, settings or files that a person can put right. The message says what."""


class DomusDeprecationWarning(DeprecationWarning):
    """Site code used a name Domus is replacing: the message says what with. The old names keep working until they're
    removed, in a release whose changelog says so."""


def deprecated(message):
    """Warns that something's deprecated (a DomusDeprecationWarning saying `message`), at the first place outside
    Domus that led here: the line of site code that used the old name."""
    warnings.warn(message, DomusDeprecationWarning, skip_file_prefixes=NOT_THE_CALLER)


def as_command(run):
    """Runs a command, `run()`, the way the command line does: a DomusError ends it with its message."""
    try:
        return run()
    except DomusError as e:
        raise SystemExit(str(e)) from None
