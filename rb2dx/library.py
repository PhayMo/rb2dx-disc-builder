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

from . import midfix, proc

AUDIO_EXT = (".ogg", ".opus", ".mp3", ".wav")
CHART_NAMES = ("notes.mid", "notes.chart")
ART_NAMES = ("album.png", "album.jpg", "album.jpeg", "cover.png", "cover.jpg")

PARTS = ("drum", "bass", "guitar", "vocals")

# Clone Hero .chart sections, without their difficulty prefix, and the Rock Band
# part each becomes. Rock Band 2 has no keys and no six-fret parts, so those
# sections have nowhere to go.
CHART_SECTIONS = {
    "single": "guitar",
    "doubleguitar": "guitar",
    "doublebass": "bass",
    "doublerhythm": "bass",
    "drums": "drum",
    "realdrums": "drum",
}
CHART_DIFFS = ("expert", "hard", "medium", "easy")
CHART_NOTE = re.compile(r"^\d+\s*=\s*N\b")

# Which stems feed which part, and how wide that part's submix is. Bass and
# vocals are mono and guitar is stereo, as in retail.
PART_ROLES = [
    ("bass",   ("rhythm", "bass"),                  1),
    ("guitar", ("guitar",),                         2),
    ("vocals", ("vocals", "vocals_1", "vocals_2"),  1),
]
# The vocal and the backing in stereo instead, for whoever would rather spend the
# channels than have those two averaged into one each. Neither is the retail shape
# for a song - 87 of the 88 entries sing from one centred channel and 71 leave one
# channel for the backing - but both are shapes the game plays: the vocal training
# songs are a mono vocal over a stereo backing, and the frame rate test sings from
# a pair panned hard left and right.
WIDE_ROLES = {"vocals": 2, "backing": 2}
# Stems that end up in the backing track when no part claims them, in the order
# they are mixed. Keys fold in here because Rock Band 2 has no keys. Anything not
# named here - crowd audio, a folder's own preview clip - stays off the disc.
BACKING_STEMS = ("song", "keys", "drums", "drums_1", "drums_2", "drums_3",
                 "drums_4", "bass", "rhythm", "guitar", "vocals", "vocals_1",
                 "vocals_2")

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
        self.tier = kw.get("tier")
        self.has_art = kw.get("has_art", False)
        self.stems = kw.get("stems") or []
        self.parts = kw.get("parts") or []
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
                "tier": self.tier, "has_art": self.has_art,
                "stems": self.stems, "parts": self.parts}

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


def charted_parts(folder):
    """The parts this song's chart actually has notes on.

    A song can only offer what its chart plays. Listing an instrument the chart
    has nothing on crashes the game as the song loads, and no retail entry does
    it: on the disc, every part with a rank has a chart track and audio channels
    of its own. A guitar-only chart therefore becomes a guitar-only song.

    Lyrics are not a vocals part. Most Clone Hero charts carry them for the
    karaoke display alone, and the chart converter turns them into a stub where
    every syllable sits on the same pitch, which is not something to hand
    somebody as a part to sing.
    """
    mid = os.path.join(folder, "notes.mid")
    if os.path.exists(mid):
        # Clone Hero prefers notes.mid where a folder has both, so match it.
        try:
            return midfix.instrument_parts(mid)
        except (OSError, ValueError, IndexError):
            return set()
    chart = os.path.join(folder, "notes.chart")
    if not os.path.exists(chart):
        return set()
    parts, section = set(), ""
    with open(chart, encoding="utf-8", errors="replace") as fp:
        for line in fp:
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip().lower()
                for diff in CHART_DIFFS:
                    if section.startswith(diff):
                        section = section[len(diff):]
                        break
            elif section in CHART_SECTIONS and CHART_NOTE.match(line):
                parts.add(CHART_SECTIONS[section])
    return parts


def drum_roles(names):
    """How drum stems split into channels, by how many the chart provides.

    Clone Hero conventions: with 4 stems the split is kick/snare/toms/cymbals,
    with 3 it is kick/snare/kit, with 2 it is kick/rest-of-kit, and a lone
    drums.ogg is the whole kit. Only kick and snare collapse to mono; anything
    representing the wider kit stays stereo.

    Widths are deliberately held to 2 or 4, the only two drum submixes proven to
    load on this build: Can't Buy Me Love plays a stereo kit against a chart
    asking for drums0, and retail Afterlife has four channels against drums3.
    A 2-stem source therefore folds its kick back into the kit rather than
    producing a 3-wide submix whose mix event we cannot verify - it costs kick
    isolation during fills, which is what any 2-channel retail song like
    marchofthepigs already lives with.

    A chart with drums but no drum stems still gets a kit, silent, because the
    part has to have somewhere to play from.
    """
    numbered = [n for n in ("drums_1", "drums_2", "drums_3", "drums_4")
                if n in names]
    if not numbered:
        return [{"role": "kit", "width": 2,
                 "keys": ["drums"] if "drums" in names else []}]
    if len(numbered) >= 3:
        return [
            {"role": "kick",  "width": 1, "keys": numbered[0:1]},
            {"role": "snare", "width": 1, "keys": numbered[1:2]},
            {"role": "kit",   "width": 2, "keys": numbered[2:]},
        ]
    return [{"role": "kit", "width": 2, "keys": numbered}]


def channel_plan(names, parts, wide=False):
    """The channels a song's audio becomes: [{role, width, keys}] in disc order.

    names are the stem names in the song folder, parts the instruments the
    chart plays. One place decides this so a song can be priced from its folder
    alone and mixed later to the same layout. wide keeps the stereo of the roles
    in WIDE_ROLES rather than averaging each into one channel.
    """
    def width_of(role, width):
        return WIDE_ROLES[role] if wide and role in WIDE_ROLES else width

    plan, claimed = [], set()
    if "drum" in parts:
        plan = drum_roles(names)
        claimed |= {n for n in names if n.startswith("drums")}
    for role, candidates, width in PART_ROLES:
        if role not in parts:
            continue
        keys = [c for c in candidates if c in names]
        claimed.update(keys)
        plan.append({"role": role, "width": width_of(role, width), "keys": keys})
    # Everything nobody is playing is mixed together as the backing track, as
    # the retail mixes do: a stem for a part this chart skips belongs there
    # rather than being dropped, or the song would be missing that instrument.
    backing = [n for n in BACKING_STEMS if n in names and n not in claimed]
    if backing:
        plan.append({"role": "backing", "width": width_of("backing", 1),
                     "keys": backing})
    return plan


def channels_for(stems, parts, wide=False):
    """How many audio channels this song's stems will become."""
    names = {os.path.splitext(f)[0].lower() for f in stems}
    return sum(p["width"] for p in channel_plan(names, parts, wide))


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
            known = cache.get(d)
            unchanged = bool(known) and known.get("stems") == sorted(stems)
            parts = known.get("parts") if unchanged else None
            if parts is None:
                # Reading the chart costs a moment per song, so a folder that
                # has not changed since the last scan keeps what it said then.
                parts = sorted(charted_parts(d))
            if not parts:
                skipped.append(
                    (os.path.basename(os.path.normpath(d)),
                     "the chart has no drums, bass, guitar or vocals"))
                continue
            song = Song(lib.name, d,
                        title=meta.get("name"), artist=meta.get("artist"),
                        tier=tier,
                        has_art=any(a in files for a in ART_NAMES),
                        stems=sorted(stems), parts=parts)
            if unchanged and known.get("seconds"):
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
