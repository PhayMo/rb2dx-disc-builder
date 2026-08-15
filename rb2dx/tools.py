"""Finding, fetching and checking the external programs the build shells out to.

Most of these are freely redistributable and can be downloaded on demand. One
cannot: ps2str is part of Sony's PS2 SDK, so the user has to supply their own
copy. Each tool below says where it comes from and what it is used for, and that
text is what the Setup page shows.

Everything here is Windows-only, because Onyx runs Magma (the official Rock Band
compiler) internally and Magma is a Windows program.
"""

import glob
import json
import os
import shutil
import subprocess
import urllib.request
import zipfile

from . import proc

USER_AGENT = "rb2dxbuilder"


class Tool:
    def __init__(self, key, exe, purpose, source, finder=None, probe=None,
                 manual=None):
        self.key = key
        self.exe = exe
        self.purpose = purpose
        self.source = source
        self.finder = finder          # returns a download URL, or None
        self.probe = probe or [exe]   # arguments that make it print something
        self.manual = manual          # why the user must supply it themselves

    @property
    def downloadable(self):
        return self.finder is not None


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as fp:
        return json.load(fp)


def _github_asset(repo, match):
    """Newest release asset from a GitHub repo whose name matches a predicate."""
    data = _get_json("https://api.github.com/repos/%s/releases" % repo)
    for release in data:
        for asset in release.get("assets", []):
            if match(asset["name"].lower()):
                return asset["browser_download_url"]
    return None


def _ffmpeg_url():
    # Gyan's Windows builds are the ones the pipeline was developed against.
    # This name always points at the current release and redirects to the
    # versioned zip.
    return "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def _onyx_url():
    return _github_asset("mtolly/onyx",
                         lambda n: "command-line" in n and "windows" in n
                         and n.endswith(".zip"))


def _mackiloha_url():
    return _github_asset("PikminGuts92/Mackiloha",
                         lambda n: "win" in n and n.endswith(".zip"))


def _dtab_url():
    # Released on its own, not as part of the Mackiloha bundle.
    return _github_asset("mtolly/dtab",
                         lambda n: "windows" in n and n.endswith(".zip"))


CATALOG = [
    Tool("ffmpeg", "ffmpeg.exe",
         "Mixes the song stems and encodes the background video.",
         "gyan.dev FFmpeg build", _ffmpeg_url, ["-version"]),
    Tool("ffprobe", "ffprobe.exe",
         "Reads durations and stream details from the source audio.",
         "gyan.dev FFmpeg build", _ffmpeg_url, ["-version"]),
    Tool("onyx", "onyx.exe",
         "Converts each chart to Rock Band 2 form and computes its ranks.",
         "Onyx Music Game Toolkit", _onyx_url, ["--help"]),
    Tool("arkhelper", "arkhelper.exe",
         "Unpacks and repacks the game's MAIN archive.",
         "Mackiloha", _mackiloha_url, ["--help"]),
    Tool("dtab", "dtab.exe",
         "Compiles and encrypts the song list the game reads.",
         "dtab by mtolly", _dtab_url, ["--help"]),
    Tool("superfreq", "superfreq.exe",
         "Converts album art to the PS2's paletted texture format.",
         "Mackiloha", _mackiloha_url, ["--help"]),
    Tool("ps2str", "ps2str.exe",
         "Muxes the video and audio into the .pss streams the game plays.",
         "Sony PS2 SDK (you must supply this)", None, [],
         manual="ps2str is part of Sony's PS2 development kit and cannot be "
                "distributed, so point at your own copy of ps2str.exe."),
]

BY_KEY = {t.key: t for t in CATALOG}


def validate(tool, path):
    """Check a path really is the tool it claims to be.

    Return codes are useless here - these programs variously exit non-zero for
    --help - so this only asks that the file runs and says something.
    """
    if not path or not os.path.isfile(path):
        return False, "not found"
    if os.path.basename(path).lower() != tool.exe:
        return False, "expected %s" % tool.exe
    if not tool.probe:
        return True, "found"
    try:
        r = proc.run([path] + tool.probe, capture_output=True, text=True,
                     timeout=90)
    except OSError as exc:
        return False, "will not run: %s" % exc
    except subprocess.TimeoutExpired:
        return False, "did not respond"
    if not ((r.stdout or "") + (r.stderr or "")).strip():
        return False, "ran but printed nothing"
    return True, "ready"


def check_all(settings):
    """Every tool's state, for the Setup page: (tool, path, ok, detail)."""
    out = []
    for tool in CATALOG:
        path = settings.tools.get(tool.key, "")
        ok, detail = validate(tool, path)
        out.append((tool, path, ok, detail))
    return out


def find_in(root, exe):
    """Look for an executable anywhere under a folder, nearest match first."""
    hits = glob.glob(os.path.join(root, "**", exe), recursive=True)
    hits.sort(key=lambda p: (p.count(os.sep), len(p)))
    return hits[0] if hits else None


def download(tool, settings, progress=None):
    """Fetch and unpack a tool, returning the path to its executable.

    progress is called with (bytes_done, bytes_total) where total may be 0 if
    the server does not say.
    """
    if not tool.downloadable:
        raise ToolError(tool.manual or "%s must be supplied by hand." % tool.key)

    try:
        url = tool.finder()
    except Exception as exc:
        raise ToolError("Could not reach the download site for %s (%s). Check "
                        "your connection, or fetch it yourself from: %s"
                        % (tool.key, exc, tool.source))
    if not url:
        raise ToolError("Could not find a download for %s. Fetch it yourself "
                        "from: %s" % (tool.key, tool.source))

    dest = os.path.join(settings.downloads, tool.key)
    archive = os.path.join(settings.downloads, "%s.zip" % tool.key)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as src:
            total = int(src.headers.get("Content-Length") or 0)
            done = 0
            with open(archive, "wb") as fp:
                while True:
                    chunk = src.read(1 << 18)
                    if not chunk:
                        break
                    fp.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except Exception as exc:
        raise ToolError("Downloading %s failed (%s). You can fetch it yourself "
                        "from %s and use Locate instead."
                        % (tool.key, exc, tool.source))

    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ToolError("The %s download could not be unpacked (%s)."
                        % (tool.key, exc))
    finally:
        if os.path.exists(archive):
            os.remove(archive)

    found = find_in(dest, tool.exe)
    if not found:
        raise ToolError("%s was downloaded but %s was not inside it."
                        % (tool.key, tool.exe))
    return found


def install(keys, settings, progress=None, status=None):
    """Download the named tools and record where they landed.

    Tools that share an archive (ffmpeg and ffprobe, the three Mackiloha ones)
    are fetched once and the siblings picked out of the same folder.
    """
    for key in keys:
        tool = BY_KEY[key]
        if not tool.downloadable:
            continue
        # An earlier download in this run may already have supplied it.
        ok, _ = validate(tool, settings.tools.get(key, ""))
        if ok:
            continue
        if status:
            status("Downloading %s ..." % tool.key)
        path = download(tool, settings, progress)
        settings.tools[tool.key] = path

        # ffmpeg and ffprobe arrive together, as do the three Mackiloha tools,
        # so the rest are picked out of the same download.
        dest = os.path.join(settings.downloads, tool.key)
        for other in CATALOG:
            if other.key == tool.key or settings.tools.get(other.key):
                continue
            if other.source == tool.source:
                sibling = find_in(dest, other.exe)
                if sibling:
                    settings.tools[other.key] = sibling
                    if status:
                        status("Found %s in the same download." % other.exe)
    settings.save()


def missing(settings):
    """Keys of tools that still need attention."""
    return [t.key for t, _, ok, _ in check_all(settings) if not ok]


class ToolError(Exception):
    """A tool could not be fetched or is not usable."""
