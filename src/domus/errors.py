"""The error Domus raises for something a person can put right: a missing setting, a file in a format it can't read,
a boundary that isn't a polygon. Its message says what, and where.

Library code raises it rather than ending the program, so a site tool, a test or the server can catch it. The
command line shows just the message, as a command's own errors.
"""


class DomusError(Exception):
    """Something about a site's data, settings or files that a person can put right. The message says what."""


def as_command(run):
    """Runs a command, `run()`, the way the command line does: a DomusError ends it with its message."""
    try:
        return run()
    except DomusError as e:
        raise SystemExit(str(e)) from None
