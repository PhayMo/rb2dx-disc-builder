"""Everything the build needs to know about this particular machine.

The pipeline reads every path from here, so nothing is tied to the developer's
drive layout. Settings live in one JSON file that the GUI and the command line
both use, which means a disc started in the GUI can be finished from a script.

Two paths deserve explanation:

  work    Scratch space. Expect this to hold roughly twice the size of the
          finished disc: the staged song files, the assembled archive and the
          ISO all live here at once.

  tmp     A short path with no spaces in it. ps2str is a 1999 Sony tool that
          splits its arguments on ':' and cannot cope with spaces, so its
          inputs are staged here rather than in the work directory.
"""

import json
import os
import sys

APP_NAME = "rb2dxbuilder"

# The stock Deluxe release is a little over 7.6 GiB. Staying at or under it is
# the safe default: a disc that size is known to work on real hardware.
RETAIL_ISO_BYTES = 8186322386
DVD5_BYTES = 4700372992
DVD9_BYTES = 8543666176

# What dropping the Custom Edition's four bundled songs gives back, measured from
# the unpacked tree: 178 MB of Afterlife and 86 MB of songs the game never lists.
DEMO_SONG_BYTES = 264000000

DEFAULTS = {
    "base_game": "",
    "libraries": [],
    "work": "",
    "tmp": "",
    "out_iso": "",
    "venue_dir": "",
    "video_kbps": 1500,
    # What plays behind the songs: "venues" for background clips, or "black" for
    # nothing at all. The video is about two thirds of what a song costs, so
    # black fits roughly two and a half times as many of them.
    "background": "venues",
    "ceiling_bytes": RETAIL_ISO_BYTES,
    # Carry the vocal and the backing in two channels rather than one, keeping
    # the stereo that averaging them into one throws away. Costs two channels of
    # disc space per song, or one where the song has no vocal part.
    "wide_mix": False,
    "jobs": 6,
    # Drop the four songs the Custom Edition ships with, worth about 264 MB of
    # room for your own. See ark.DEMO_SONGS for what they are.
    "drop_demos": True,
    # Also write the disc's files to a folder, for emulator users who need to
    # make the image with ImgBurn. See iso.export_folder. Left empty, the folder
    # goes beside the ISO.
    "disc_folder": False,
    "disc_folder_path": "",
    "tools": {},
}


VIDEO_EXT = (".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v", ".mpg", ".mpeg")

BACKGROUNDS = ("venues", "black")

# A black background still has to be encoded, because the game reads a song's
# audio out of a video stream and finds none without one, but black at this rate
# costs about a fiftieth of a real clip and looks no different for being cheap.
BLACK_KBPS = 150


def videos_in(folder):
    """The video files in a folder, in a fixed order, or [] if it has none."""
    if not folder or not os.path.isdir(folder):
        return []
    return sorted(f for f in os.listdir(folder)
                  if f.lower().endswith(VIDEO_EXT))


def bundled_venues():
    """The background videos that ship with the tool, if they are still there.

    A venues folder beside the program wins, so anyone can drop their own clips
    in without touching settings; the copy inside the build is the fallback.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(os.path.dirname(here), "venues"),
                  os.path.join(here, "venues")]
    if getattr(sys, "frozen", False):
        candidates.insert(0, os.path.join(
            os.path.dirname(os.path.abspath(sys.executable)), "venues"))
    for candidate in candidates:
        if videos_in(candidate):
            return candidate
    return ""


def settings_path():
    """Where the settings file lives, overridable for testing or portable use."""
    override = os.environ.get("RB2DX_SETTINGS")
    if override:
        return override
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME, "settings.json")


class Library:
    """A folder of songs in Clone Hero layout, as one named collection."""

    def __init__(self, path, name=None, enabled=True):
        self.path = path
        self.name = name or os.path.basename(os.path.normpath(path))
        self.enabled = enabled

    def as_dict(self):
        return {"path": self.path, "name": self.name, "enabled": self.enabled}

    @classmethod
    def from_dict(cls, d):
        return cls(d["path"], d.get("name"), d.get("enabled", True))


class Settings:
    def __init__(self, data=None):
        merged = dict(DEFAULTS)
        merged.update(data or {})
        self.base_game = merged["base_game"]
        self.libraries = [Library.from_dict(d) for d in merged["libraries"]]
        self.work = merged["work"]
        self.tmp = merged["tmp"]
        self.out_iso = merged["out_iso"]
        self._venue_dir = merged["venue_dir"]
        self.video_kbps = int(merged["video_kbps"])
        self.background = (merged["background"] if merged["background"]
                           in BACKGROUNDS else "venues")
        self.ceiling_bytes = int(merged["ceiling_bytes"])
        # Named stereo_vocals in the first release that had it, before it took in
        # the backing as well.
        self.wide_mix = bool(merged["wide_mix"]
                             or merged.get("stereo_vocals", False))
        self.jobs = int(merged["jobs"])
        self.drop_demos = bool(merged["drop_demos"])
        self.disc_folder = bool(merged["disc_folder"])
        self.disc_folder_path = merged["disc_folder_path"]
        self.tools = dict(merged["tools"])

    # ---- loading and saving ------------------------------------------------

    @classmethod
    def load(cls, path=None):
        path = path or settings_path()
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as fp:
            return cls(json.load(fp))

    def save(self, path=None):
        path = path or settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump({
                "base_game": self.base_game,
                "libraries": [lib.as_dict() for lib in self.libraries],
                "work": self.work,
                "tmp": self.tmp,
                "out_iso": self.out_iso,
                "venue_dir": self._venue_dir,
                "video_kbps": self.video_kbps,
                "background": self.background,
                "ceiling_bytes": self.ceiling_bytes,
                "wide_mix": self.wide_mix,
                "jobs": self.jobs,
                "drop_demos": self.drop_demos,
                "disc_folder": self.disc_folder,
                "disc_folder_path": self.disc_folder_path,
                "tools": self.tools,
            }, fp, indent=2)
        return path

    # ---- derived locations -------------------------------------------------

    @property
    def venue_dir(self):
        """Where background videos come from, falling back to the bundled ones.

        The clips are short and looped to each song's length, so a handful of a
        few seconds each is all this needs.
        """
        return self._venue_dir or bundled_venues()

    @venue_dir.setter
    def venue_dir(self, value):
        # The bundled folder is not worth storing: remembering it by path would
        # break as soon as the tool is moved.
        self._venue_dir = "" if value and value == bundled_venues() else value

    @property
    def black_background(self):
        """Nothing behind the songs, in exchange for much smaller ones."""
        return self.background == "black"

    @property
    def encode_kbps(self):
        """The bitrate the video stage really encodes at."""
        return BLACK_KBPS if self.black_background else self.video_kbps

    def work_dir(self, *parts):
        if not self.work:
            raise SettingsError("No work folder is set yet.")
        path = os.path.join(self.work, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def stage(self):
        """One folder per song, holding everything that gets shipped."""
        return self.work_dir("stage")

    @property
    def downloads(self):
        return self.work_dir("tools")

    @property
    def chart_scratch(self):
        return self.work_dir("charts")

    @property
    def ark_source(self):
        """The base game's archive, unpacked."""
        return self.work_dir("ark_base")

    @property
    def ark_build(self):
        """The tree that gets packed into the finished archive."""
        return self.work_dir("ark_build")

    @property
    def ark_out(self):
        """The packed MAIN.HDR and MAIN_*.ARK ready for the ISO."""
        return self.work_dir("gen_out")

    @property
    def dta_dir(self):
        return self.work_dir("dta")

    def tmp_dir(self, *parts):
        """Short, space-free scratch for tools that cannot handle long paths."""
        if not self.tmp:
            raise SettingsError("No short temp folder is set yet.")
        path = os.path.join(self.tmp, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def tool(self, name):
        path = self.tools.get(name)
        if not path or not os.path.exists(path):
            raise SettingsError(
                "%s has not been set up yet. Open the Setup page and either "
                "download it or point at your own copy." % name)
        return path

    # ---- base game files ---------------------------------------------------

    def boot_elf(self):
        """The game executable, whose name the ISO and SYSTEM.CNF must match."""
        for name in sorted(os.listdir(self.base_game)):
            if name.upper().startswith("SLUS") or name.upper().startswith("SLES"):
                full = os.path.join(self.base_game, name)
                if os.path.isfile(full):
                    return full
        raise SettingsError(
            "No SLUS/SLES executable in %s - that folder should be the "
            "unpacked Rock Band 2 Deluxe PS2 release." % self.base_game)

    def base_ark(self):
        """The base game's archive header, wherever its gen folder sits."""
        for dirpath, _, files in os.walk(self.base_game):
            for f in files:
                if f.upper() == "MAIN.HDR":
                    return os.path.join(dirpath, f)
        raise SettingsError("No MAIN.HDR under %s." % self.base_game)

    def base_ark_bytes(self):
        """Roughly what the base game costs before any songs are added."""
        total = 0
        gen = os.path.dirname(self.base_ark())
        for f in os.listdir(gen):
            if f.upper().endswith((".HDR", ".ARK")):
                total += os.path.getsize(os.path.join(gen, f))
        if self.drop_demos:
            total -= DEMO_SONG_BYTES
        return total

    # ---- checks ------------------------------------------------------------

    def problems(self):
        """Everything standing between these settings and a build, in order."""
        out = []
        if not self.base_game or not os.path.isdir(self.base_game):
            out.append("Point at your unpacked Rock Band 2 Deluxe PS2 folder "
                       "(the one holding SLUS_218.00 and gen/MAIN_0.ARK).")
        else:
            for check in (self.boot_elf, self.base_ark):
                try:
                    check()
                except SettingsError as exc:
                    out.append(str(exc))
        if not [lib for lib in self.libraries if lib.enabled]:
            out.append("Add at least one folder of songs to build from.")
        if not self.work:
            out.append("Choose a work folder with room for about twice the "
                       "size of the finished disc.")
        if not self.tmp:
            out.append("Choose a short temp folder such as C:\\rb2dxtmp; "
                       "ps2str cannot handle spaces or long paths.")
        elif " " in self.tmp:
            out.append("The temp folder must not contain spaces: ps2str will "
                       "fail on %s." % self.tmp)
        if not self.out_iso:
            out.append("Choose where to write the finished ISO.")
        if not self.black_background and not videos_in(self.venue_dir):
            out.append("Choose a folder holding background videos (%s): one "
                       "plays behind every song, and the clips that came with "
                       "this tool are missing. Or set the background to black."
                       % ", ".join(VIDEO_EXT))
        return out


class SettingsError(Exception):
    """Something the user needs to fix before a build can run."""


def suggest(work_root=None):
    """A starting set of settings, with sensible guesses filled in."""
    s = Settings()
    root = work_root or os.path.join(os.path.expanduser("~"), "rb2dx")
    s.work = os.path.join(root, "work")
    s.out_iso = os.path.join(root, "Rock Band 2 Deluxe Custom.iso")
    # Somewhere short, on the same drive as the work folder where possible.
    drive = os.path.splitdrive(os.path.abspath(s.work))[0] or "C:"
    s.tmp = drive + "\\rb2dxtmp"
    return s
