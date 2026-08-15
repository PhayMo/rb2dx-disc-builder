"""Running the external tools without console windows popping up.

The GUI is a windowed program, so a child process launched the ordinary way gets
a console window of its own for as long as it runs. Startup checks seven tools in
a row, so that shows up as the program apparently opening and closing itself
several times before the window appears, and a build flashes a window per song.

Everything that runs a tool goes through here.
"""

import subprocess
import sys

# CREATE_NO_WINDOW. Spelled out because it is missing from subprocess on the
# Python versions this has to run on.
_NO_WINDOW = 0x08000000


def hidden(kw):
    """Add the flag that keeps a child process off the screen, on Windows."""
    if sys.platform == "win32":
        kw["creationflags"] = kw.get("creationflags", 0) | _NO_WINDOW
    return kw


def run(cmd, **kw):
    return subprocess.run(cmd, **hidden(kw))


def popen(cmd, **kw):
    return subprocess.Popen(cmd, **hidden(kw))
