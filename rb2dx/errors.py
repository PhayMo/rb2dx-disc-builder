"""Errors the user is meant to read.

Anything raised as a BuildError should say what went wrong in plain language and,
where possible, what to do about it: these messages end up in a dialog box, not
a stack trace.
"""


class BuildError(Exception):
    """A build could not continue."""


class Cancelled(Exception):
    """The user stopped the build."""
