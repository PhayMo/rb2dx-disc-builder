"""Deciding which songs fit on the disc.

A song's cost is driven almost entirely by its length. The audio is 4-bit ADPCM
at a fixed rate per channel, and the video is whatever bitrate the video stage
encodes at, so a three minute song at 1500 kbps runs to roughly 40 MB. Adding six
songs took one disc from 0.84 to 1.10 GB, which is where these numbers come from.

Which songs to drop when they do not all fit is left to whoever is building the
disc: this only prices them and says how much room is left.
"""

import json
import os

from . import library

# Audio is 4-bit ADPCM at 22050 Hz per channel, and the preview clip is a fixed
# 30 seconds of stereo on top.
ADPCM_BYTES_PER_SEC_PER_CH = 22050 * 16 / 28.0
PREVIEW_SECONDS = 30

# Archive entry alignment, the ISO's own structures and the boot files sit on top
# of the song data, so a plan stops short of the ceiling by this much.
MARGIN_BYTES = 0.35e9

PROBLEMS_NAME = "problems.json"


def channels(settings, song):
    """How many channels this song gets under the settings as they stand.

    A wider mix costs a channel or two, and a song has to be priced the way it is
    about to be built.
    """
    return library.channels_for(song.stems, song.parts, settings.wide_mix)


def estimate(settings, song):
    """What a song will weigh on disc, before it has been built."""
    audio = (song.seconds * channels(settings, song) + PREVIEW_SECONDS * 2) \
        * ADPCM_BYTES_PER_SEC_PER_CH
    video = song.seconds * settings.encode_kbps * 1000 / 8.0
    return audio + video


def built_at(settings, sid):
    """The bitrate a staged song's video was encoded at, or None if unknown."""
    stamp = os.path.join(settings.work, "stamps", "%s.json" % sid)
    try:
        with open(stamp, encoding="utf-8") as fp:
            return json.load(fp).get("video_kbps")
    except (OSError, ValueError):
        return None


def measured(settings):
    """(bytes, channels) for each already-built song, by its source folder.

    The estimate above is close but runs a little under, mostly because the .pss
    container adds its own overhead, so anything already staged is weighed for
    real instead.

    Songs built at another bitrate are left to the estimate: turning the
    background black rebuilds them a third of the size, and pricing them as they
    stand would hide the room that frees up.
    """
    from . import ark

    out = {}
    stage = settings.stage
    if not os.path.isdir(stage):
        return out
    for sid in os.listdir(stage):
        layout = os.path.join(stage, sid, "layout.json")
        if not os.path.exists(layout):
            continue
        was = built_at(settings, sid)
        if was is not None and was != settings.encode_kbps:
            continue
        try:
            with open(layout, encoding="utf-8") as fp:
                info = json.load(fp)
            source, built_channels = info["source"], info["channels"]
        except (ValueError, KeyError):
            continue
        total = 0
        for name in ark.required_files(sid):
            # Textures and weights sit in the song's gen/ subfolder, as on disc.
            found = [p for p in (os.path.join(stage, sid, name),
                                 os.path.join(stage, sid, "gen", name))
                     if os.path.exists(p)]
            if not found:
                total = 0
                break
            total += os.path.getsize(found[0])
        if total:
            out[source] = (total, built_channels)
    return out


def price(settings, songs):
    """Attach a byte cost to every song, measuring the ones already built."""
    built = measured(settings)
    for song in songs:
        # A song staged with a different number of channels is about to be mixed
        # again, so what it weighs now is not what it will weigh.
        was = built.get(song.path)
        if was and was[1] == channels(settings, song):
            song.bytes, song.priced_from_build = was[0], True
        else:
            song.bytes, song.priced_from_build = estimate(settings, song), False
    return songs


def budget(settings):
    """How many bytes of songs the disc can hold."""
    try:
        base = settings.base_ark_bytes()
    except Exception:
        base = 0.82e9
    return max(0, settings.ceiling_bytes - base - MARGIN_BYTES)


def disc_bytes(settings, songs):
    """The whole image's size, songs plus the base game."""
    try:
        base = settings.base_ark_bytes()
    except Exception:
        base = 0.82e9
    return sum(getattr(s, "bytes", 0) for s in songs) + base


def room_left(settings, songs):
    """Bytes still free once these songs are on the disc, negative if over."""
    price(settings, songs)
    return budget(settings) - sum(s.bytes for s in songs)


# ---- songs that could not be built ---------------------------------------

def problems_path(settings):
    return os.path.join(settings.work_dir(), PROBLEMS_NAME)


def load_problems(settings):
    """Songs a previous build could not produce, keyed by source folder.

    Keeping this means a rebuild fills the disc with songs that work instead of
    stalling on the same broken chart every time.
    """
    path = problems_path(settings)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except ValueError:
        return {}


def record_problem(settings, song_path, stage, reason):
    data = load_problems(settings)
    data[song_path] = {"stage": stage, "reason": reason}
    with open(problems_path(settings), "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=1)


def forget_problems(settings, song_paths=None):
    """Clear remembered failures so those songs are tried again."""
    if song_paths is None:
        data = {}
    else:
        data = load_problems(settings)
        for path in song_paths:
            data.pop(path, None)
    with open(problems_path(settings), "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=1)


def usable(settings, songs):
    """Songs worth attempting, with known-broken ones held back."""
    bad = load_problems(settings)
    return ([s for s in songs if s.path not in bad],
            [(s, bad[s.path]) for s in songs if s.path in bad])
