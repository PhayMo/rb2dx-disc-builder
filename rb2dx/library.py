"""Finding out what songs are available and what each one contains.

Songs are read in the layout Clone Hero and YARG use: one folder per song, with
a chart, a song.ini and separate audio stems. This walks the configured folders,
works out which songs are usable, and measures the one thing that decides how
many fit on a disc - their length.

Reading durations means running ffprobe once per stem, which takes a few minutes
across a thousand songs, so results are cached and only re-read when a song
folder changes.
"""

import concurrent.futures
import json
import os
import re

from . import proc

AUDIO_EXT = (".ogg", ".opus", ".mp3", ".wav")
CHART_NAMES = ("notes.mid", "notes.chart")
ART_NAMES = ("album.png", "album.jpg", "album.jpeg", "cover.png", "cover.jpg")

# song.ini records band difficulty as Rock Band's own tier index, 0-6, which the
# game shows as Warmup through Impossible. The top tier is the one to drop first
# when space is short: nobody plays it and those songs are usually long.
TIER_NAMES = ["Warmup", "Apprentice", "Solid", "Moderate", "Challenging",
              "Nightmare", "Impossible"]

CACHE_NAME = "library.json"


def tier_name(tier):
    if tier is None or not 0 <= tier < len(TIER_NAMES):
        return "Unrated"
    return TIER_NAMES[tier]


class Song:
    def __init__(self, library, path, **kw):
        self.library = library
        self.path = path
        self.folder = os.path.basename(os.path.normpath(path))
        self.title = kw.get("title") or self.folder
        self.artist = kw.get("artist") or ""
        self.seconds = kw.get("seconds") or 0.0
        self.channels = kw.get("channels") or 0
        self.tier = kw.get("tier")
        self.has_art = kw.get("has_art", False)
        self.stems = kw.get("stems") or []
        self.sid = kw.get("sid") or ""

    @property
    def label(self):
        return "%s - %s" % (self.artist, self.title) if self.artist else self.title

    @property
    def minutes(self):
        return self.seconds / 60.0

    def as_dict(self):
        return {"library": self.library, "path": self.path, "title": self.title,
                "artist": self.artist, "seconds": self.seconds,
                "channels": self.channels, "tier": self.tier,
                "has_art": self.has_art, "stems": self.stems}

    @classmethod
    def from_dict(cls, d):
        return cls(d["library"], d["path"], **{k: v for k, v in d.items()
                                               if k not in ("library", "path")})


def read_ini(path):
    meta = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fp:
        for line in fp:
            if "=" in line and not line.strip().startswith("["):
                k, v = line.split("=", 1)
                meta[k.strip().lower()] = v.strip()
    return meta


def make_id(meta, folder, used):
    """The short ASCII id the game files are named after, unique per disc."""
    base = "%s%s" % (meta.get("artist", ""),
                     meta.get("name", os.path.basename(folder)))
    sid = re.sub(r"[^a-z0-9]", "", base.lower())[:24]
    if not sid:
        sid = "song"
    if sid[0].isdigit():
        sid = "s" + sid
    out, n = sid, 2
    while out in used:
        out = "%s%d" % (sid, n)
        n += 1
    used.add(out)
    return out


def channels_for(stems):
    """How many audio channels this song's stems will become.

    Mirrors the role plan the audio stage applies, so a song can be priced
    before it is built. Every part is given channels whether the folder has a
    stem for it or not, so seven is the floor.
    """
    names = {os.path.splitext(f)[0].lower() for f in stems}
    drums = sorted(s for s in names if s.startswith("drums"))
    # 2 or 4 drum channels, the only widths the game is proven to accept.
    total = 4 if len(drums) >= 3 else 2
    total += 1          # bass, mono
    total += 2          # guitar, stereo
    total += 1          # vocals, mono
    if {"song", "keys"} & names:
        total += 1      # whatever is left over, mixed down to a mono backing
    return total


def _duration(ffprobe, path):
    r = proc.run([ffprobe, "-v", "error", "-show_entries",
                  "format=duration", "-of", "csv=p=0", path],
                 capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _candidates(lib):
    """Song folders in one library, with the reason any are unusable."""
    out, skipped = [], []
    if not os.path.isdir(lib.path):
        return out, [(lib.path, "folder is missing")]
    for entry in sorted(os.listdir(lib.path)):
        d = os.path.join(lib.path, entry)
        if not os.path.isdir(d):
            continue
        files = os.listdir(d)
        stems = [f for f in files if f.lower().endswith(AUDIO_EXT)]
        if not any(c in files for c in CHART_NAMES):
            skipped.append((entry, "no chart file"))
        elif not stems:
            skipped.append((entry, "no audio files"))
        else:
            out.append((d, files, stems))
    return out, skipped


def scan(settings, rescan=False, progress=None, log=None):
    """Every usable song across the enabled libraries, plus what was skipped.

    Returns (songs, skipped) where skipped is a list of (folder, reason).
    """
    cache_path = os.path.join(settings.work_dir(), CACHE_NAME)
    cache = {}
    if os.path.exists(cache_path) and not rescan:
        try:
            with open(cache_path, encoding="utf-8") as fp:
                for d in json.load(fp):
                    cache[d["path"]] = d
        except (ValueError, KeyError):
            cache = {}

    ffprobe = settings.tool("ffprobe")
    songs, skipped, fresh = [], [], []
    for lib in settings.libraries:
        if not lib.enabled:
            continue
        found, missed = _candidates(lib)
        skipped.extend(missed)
        for d, files, stems in found:
            meta = {}
            ini = os.path.join(d, "song.ini")
            if os.path.exists(ini):
                meta = read_ini(ini)
            tier = None
            try:
                value = int(meta.get("diff_band", -1))
                tier = value if 0 <= value < len(TIER_NAMES) else None
            except ValueError:
                pass
            song = Song(lib.name, d,
                        title=meta.get("name"), artist=meta.get("artist"),
                        channels=channels_for(stems), tier=tier,
                        has_art=any(a in files for a in ART_NAMES),
                        stems=sorted(stems))
            known = cache.get(d)
            if known and known.get("stems") == song.stems and known.get("seconds"):
                song.seconds = known["seconds"]
            else:
                fresh.append(song)
            song._meta = meta
            songs.append(song)

    # Durations are the slow part, so only new or changed songs are probed.
    if fresh:
        if log:
            log("Reading the length of %d song%s ..."
                % (len(fresh), "" if len(fresh) == 1 else "s"))
        done = 0
        with concurrent.futures.ThreadPoolExecutor(settings.jobs) as pool:
            jobs = {pool.submit(_longest, ffprobe, s): s for s in fresh}
            for job in concurrent.futures.as_completed(jobs):
                jobs[job].seconds = job.result()
                done += 1
                if progress:
                    progress(done, len(fresh))

    songs = [s for s in songs if s.seconds > 0]
    used = set()
    for song in songs:
        song.sid = make_id(getattr(song, "_meta", {}), song.path, used)

    with open(cache_path, "w", encoding="utf-8") as fp:
        json.dump([s.as_dict() for s in songs], fp, indent=1)
    return songs, skipped


def _longest(ffprobe, song):
    return max(_duration(ffprobe, os.path.join(song.path, s))
               for s in song.stems)


def summarise(songs):
    """Per-library counts and hours, for the library page's header."""
    out = {}
    for song in songs:
        count, seconds = out.get(song.library, (0, 0.0))
        out[song.library] = (count + 1, seconds + song.seconds)
    return out
