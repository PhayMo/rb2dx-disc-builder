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
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from . import proc

USER_AGENT = "rb2dxbuilder"


class Tool:
    def __init__(self, key, exe, purpose, source, finders=(), probe=None,
                 manual=None):
        self.key = key
        self.exe = exe
        self.purpose = purpose
        self.source = source
        # Each returns a download URL, tried in the order given, so a site that
        # is down or refusing to serve costs a moment rather than the download.
        self.finders = tuple(finders)
        self.probe = probe or [exe]   # arguments that make it print something
        self.manual = manual          # why the user must supply it themselves

    @property
    def downloadable(self):
        return bool(self.finders)


def _spare_certificates():
    """A context trusting the certificate list certifi ships, or None if absent.

    Windows collects most root certificates as it first needs them and keeps
    expired ones alongside, so a machine that has never had to verify a
    particular root cannot, and says a certificate has expired when it means it
    has none to check against. Nobody downloading FFmpeg can put that right, so
    a refused certificate gets one more try against a list of our own.
    """
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def _open(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as exc:
        spare = None
        if isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError):
            spare = _spare_certificates()
        if spare is None:
            raise
        return urllib.request.urlopen(req, timeout=timeout, context=spare)


def _get_json(url):
    with _open(url, 60) as fp:
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


def _ffmpeg_mirror():
    # The same builds, published as GitHub releases: the zip is byte for byte
    # what gyan.dev serves.
    return _github_asset("GyanD/codexffmpeg",
                         lambda n: n.endswith("essentials_build.zip"))


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
         "gyan.dev FFmpeg build", (_ffmpeg_url, _ffmpeg_mirror), ["-version"]),
    Tool("ffprobe", "ffprobe.exe",
         "Reads durations and stream details from the source audio.",
         "gyan.dev FFmpeg build", (_ffmpeg_url, _ffmpeg_mirror), ["-version"]),
    Tool("onyx", "onyx.exe",
         "Converts each chart to Rock Band 2 form and computes its ranks.",
         "Onyx Music Game Toolkit", (_onyx_url,), ["--help"]),
    Tool("arkhelper", "arkhelper.exe",
         "Unpacks and repacks the game's MAIN archive.",
         "Mackiloha", (_mackiloha_url,), ["--help"]),
    Tool("dtab", "dtab.exe",
         "Compiles and encrypts the song list the game reads.",
         "dtab by mtolly", (_dtab_url,), ["--help"]),
    Tool("superfreq", "superfreq.exe",
         "Converts album art to the PS2's paletted texture format.",
         "Mackiloha", (_mackiloha_url,), ["--help"]),
    Tool("ps2str", "ps2str.exe",
         "Muxes the video and audio into the .pss streams the game plays.",
         "Sony PS2 SDK (you must supply this)", (), [],
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


def site_of(url):
    return urllib.parse.urlsplit(url).netloc or url


def why_failed(exc):
    """A download failure in the terms the user needs to hear it."""
    reason = getattr(exc, "reason", None) or exc
    if isinstance(reason, ssl.SSLCertVerificationError):
        return ("its certificate was refused (%s), which is either for the site "
                "to put right or this machine's clock being wrong"
                % (getattr(reason, "verify_message", None) or reason))
    return str(reason) or type(exc).__name__


def fetch(url, archive, progress=None):
    """Download one URL to a file.

    progress is called with (bytes_done, bytes_total) where total may be 0 if
    the server does not say.
    """
    with _open(url, 120) as src:
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


def download(tool, settings, progress=None, status=None):
    """Fetch and unpack a tool, returning the path to its executable.

    Each place the tool can come from is tried in turn, so one of them being
    down, blocked or out of certificate costs a moment instead of the download.
    """
    if not tool.downloadable:
        raise ToolError(tool.manual or "%s must be supplied by hand." % tool.key)

    dest = os.path.join(settings.downloads, tool.key)
    archive = os.path.join(settings.downloads, "%s.zip" % tool.key)
    trouble = []
    for finder in tool.finders:
        try:
            url = finder()
        except Exception as exc:
            trouble.append("could not ask for a link: %s" % why_failed(exc))
            continue
        if not url:
            trouble.append("no download offered")
            continue
        if trouble and status:
            status("Trying %s instead ..." % site_of(url))
        try:
            fetch(url, archive, progress)
            break
        except Exception as exc:
            trouble.append("%s: %s" % (site_of(url), why_failed(exc)))
    else:
        raise ToolError("Could not download %s (%s). You can fetch it yourself "
                        "from %s and use Locate instead."
                        % (tool.key, "; ".join(trouble), tool.source))

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
        path = download(tool, settings, progress, status)
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
