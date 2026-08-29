"""Prepare PS2 Rock Band 2 audio from Clone Hero source stems.

For each song this writes into that song's stage folder:
  <id>.ogg       multichannel mix, 22050 Hz, 3s of leading silence
  prev_<id>.ogg  stereo preview excerpt, cut from that mix
  layout.json    channel layout + metadata, consumed by the songs.dta generator

The 3 seconds of silence exist because PS2 Rock Band starts reading the chart
3 seconds in; the chart itself is left untouched. Channel order here must match
the tracks/pans/vols arrays written into songs.dta.

Which channels a song gets is decided by library.channel_plan, from the stems in
the folder and the parts its chart plays.
"""

import json
import math
import os
import re

from . import library, proc
from .library import AUDIO_EXT, read_ini

RATE = 22050
# The console starts reading a chart three seconds into the audio stream, so
# every mix opens with that much silence. Charts that Onyx pushed further along
# need more, which is only known once the chart is built and so is added when the
# mix is encoded for the console: see charts.measure_pad.
LEAD_SILENCE_MS = 3000
PREVIEW_SECS = 30
# Where the preview starts when song.ini has no preview_start_time, or has one it
# does not know (a -1, which is most of what the charting tools write): half a
# minute in, which is past most intros.
DEFAULT_PREVIEW_MS = 30000
# The clip fades in, so cutting into the middle of a song does not click, and out
# at the end so it can loop.
PREVIEW_FADE_IN = 0.05
PREVIEW_FADE_OUT = 2
# How close to full scale the finished clip may come. The song list plays it as
# it is, with none of the levels the console applies to a song, so anything that
# reaches full scale here is louder than the song it belongs to and clips on top.
PREVIEW_PEAK_DB = -1.0
DRUM_ROLES = ("kick", "snare", "kit")

# What ffmpeg is asked for when measuring a mixdown: one figure for the whole
# stream, no per-channel breakdown to read past.
MEASURE = "measure_perchannel=none:measure_overall=RMS_level"
PEAK_MEASURE = "measure_perchannel=none:measure_overall=Peak_level"
# Mixing two channels into one cannot cost more than 3 dB, and four cannot cost
# more than 6. Anything past that is a measurement gone wrong, not a mix.
MAX_FOLD_DB = 6.0
# The song list carries levels to a tenth of a dB, so anything that rounds away
# to nothing is not worth putting in the file or the log.
MIN_TRIM_DB = 0.05


def stems_in(folder):
    """The audio files in a song folder, keyed by stem name.

    Any format ffmpeg reads counts, not just .ogg: plenty of charts ship a
    single song.wav or .mp3, and the library lists those as usable, so the mixer
    has to agree. A folder carrying the same stem twice keeps the .ogg.
    """
    out = {}
    for name in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(name)
        if ext.lower() not in AUDIO_EXT:
            continue
        key = stem.lower()
        if key not in out or ext.lower() == ".ogg":
            out[key] = os.path.join(folder, name)
    return out


def probe_audio(settings, path):
    out = proc.run(
        [settings.tool("ffprobe"), "-v", "error", "-show_entries",
         "stream=channels:format=duration", "-of", "json", path],
        capture_output=True, text=True)
    d = json.loads(out.stdout)
    return int(d["streams"][0]["channels"]), float(d["format"]["duration"])


def fold_loss(settings, files, width):
    """What a role loses by playing from one channel instead of two, in dB.

    Averaging the two sides keeps whatever they hold in common and costs up to
    3 dB of whatever they do not, so a wide stereo vocal arrives quieter against
    the band than it was in the source while a close-to-mono one barely moves.
    The figure is measured rather than assumed: the role is mixed down both ways
    and the results compared. A role the game gives two channels keeps its sides
    and loses nothing.
    """
    if width != 1:
        return 0.0
    channels = [probe_audio(settings, f)[0] for f in files]
    if not any(ch > 1 for ch in channels):
        return 0.0

    parts, wide, thin = [], [], []
    for i, ch in enumerate(channels):
        # Mono stems go through both sides untouched: they fold to nothing, but
        # they are part of what the role sounds like either way.
        parts.append("[%d:a]aresample=%d,asplit=2[w%d][t%d]" % (i, RATE, i, i))
        parts.append("[w%d]%s[wide%d]" % (i, pan_expr(ch, 2), i))
        parts.append("[t%d]%s[thin%d]" % (i, pan_expr(ch, 1), i))
        wide.append("[wide%d]" % i)
        thin.append("[thin%d]" % i)

    for labels, name in ((wide, "wide"), (thin, "thin")):
        joined = "".join(labels)
        if len(labels) > 1:
            parts.append("%samix=inputs=%d:normalize=0,astats@%s=%s[%s]"
                         % (joined, len(labels), name, MEASURE, name))
        else:
            parts.append("%sastats@%s=%s[%s]" % (joined, name, MEASURE, name))

    cmd = [settings.tool("ffmpeg"), "-hide_banner", "-nostats", "-loglevel",
           "info"]
    for f in files:
        cmd += ["-i", f]
    cmd += ["-filter_complex", ";".join(parts),
            "-map", "[wide]", "-f", "null", os.devnull,
            "-map", "[thin]", "-f", "null", os.devnull]
    r = proc.run(cmd, capture_output=True, text=True)

    levels = {}
    for name, value in re.findall(
            r"\[astats@(\w+) [^\]]*\] RMS level dB:\s*(-?[\d.]+)",
            r.stderr or ""):
        levels[name] = float(value)
    if len(levels) != 2:
        # Nothing to measure, silence among them: leave the balance alone.
        return 0.0
    # Never a boost, and never more than mixing down can account for.
    return max(0.0, min(levels["wide"] - levels["thin"], MAX_FOLD_DB))


def levels_for(plan, losses):
    """The per-channel dB for songs.dta, restoring what the mixdown took.

    A channel that lost 2 dB against one that lost none has to come back up by
    that much or the song is not the mix it was, but no shipped entry asks the
    game for a boost - of 748 retail channels, 623 are cut and 125 sit at zero -
    so the same balance is had by holding the worst-hit channel at zero and
    trimming the rest to meet it.
    """
    worst = max(losses.values()) if losses else 0.0
    out = []
    for p in plan:
        db = round(losses.get(p["role"], 0.0) - worst, 1)
        out += [db] * p["width"]
    return out


def pan_expr(src_ch, want_ch):
    """Build a pan filter turning src_ch channels into exactly want_ch."""
    if want_ch == 1:
        if src_ch == 1:
            return "pan=mono|c0=c0"
        terms = "+".join("%.4f*c%d" % (1.0 / src_ch, i) for i in range(src_ch))
        return "pan=mono|c0=" + terms
    if src_ch == 1:
        return "pan=stereo|c0=c0|c1=c0"
    if src_ch == 2:
        return "pan=stereo|c0=c0|c1=c1"
    left = "+".join("%.4f*c%d" % (2.0 / src_ch, i) for i in range(0, src_ch, 2))
    right = "+".join("%.4f*c%d" % (2.0 / src_ch, i) for i in range(1, src_ch, 2))
    return "pan=stereo|c0=%s|c1=%s" % (left, right)


def preview_start(meta, seconds):
    """How far into the song the preview is cut from, in seconds.

    song.ini carries the point in milliseconds. Charts made from Guitar Hero rips
    and the like usually carry a -1 instead, meaning nobody chose one, and a few
    carry nothing at all; those take the default. A point so late that the clip
    would run off the end of the song is pulled back to fit.
    """
    raw = (meta.get("preview_start_time") or "").strip()
    try:
        ms = float(raw)
    except ValueError:
        ms = -1.0
    if ms < 0:
        ms = DEFAULT_PREVIEW_MS
    start = ms / 1000.0
    if seconds and start + PREVIEW_SECS > seconds:
        start = max(0.0, seconds - PREVIEW_SECS)
    return start


def console_mix(pans, vols):
    """A pan filter folding the disc's channels the way the console does.

    Each channel is placed by its pan and set by its level, and what comes out is
    what the song sounds like when it plays. A channel of its own on each side
    arrives whole; one in the middle is held back 3 dB, as panning to the middle
    does, so it is no louder for being in both.
    """
    left, right = [], []
    for i, (pan, vol) in enumerate(zip(pans, vols)):
        gain = 10 ** (vol / 20.0)
        for side, weight in ((left, math.cos((pan + 1) * math.pi / 4)),
                             (right, math.sin((pan + 1) * math.pi / 4))):
            if gain * weight > 1e-4:
                side.append("%.4f*c%d" % (gain * weight, i))
    return "pan=stereo|c0=%s|c1=%s" % ("+".join(left) or "0*c0",
                                       "+".join(right) or "0*c0")


def peak_of(settings, path, filters, start, seconds):
    """The loudest sample in one stretch of a file once filtered, in dBFS."""
    r = proc.run([settings.tool("ffmpeg"), "-hide_banner", "-nostats",
                  "-loglevel", "info", "-ss", "%.3f" % start, "-t",
                  "%.3f" % seconds, "-i", path, "-af",
                  "%s,astats=%s" % (filters, PEAK_MEASURE),
                  "-f", "null", os.devnull], capture_output=True, text=True)
    found = re.findall(r"Peak level dB:\s*(-?[\d.]+)", r.stderr or "")
    return float(found[-1]) if found else None


def outputs(settings, sid):
    d = os.path.join(settings.stage, sid)
    return (os.path.join(d, "%s.ogg" % sid),
            os.path.join(d, "prev_%s.ogg" % sid),
            os.path.join(d, "layout.json"))


def is_done(settings, sid):
    return all(os.path.exists(p) for p in outputs(settings, sid))


def stage(settings, sid, source_dir, log=None):
    """Mix one song's stems into the staged files. Returns (ok, message)."""
    src = source_dir
    folder_name = os.path.basename(os.path.normpath(src))
    ini = os.path.join(src, "song.ini")
    if not os.path.exists(ini):
        return False, "the song folder has no song.ini"
    meta = read_ini(ini)
    present = stems_in(src)

    # The parts the chart plays get channels; nothing else does. Each of the 40
    # songs the game ships gives every part it ranks channels of its own, and
    # ranks every part it gives channels to - a song list entry that breaks
    # either way round crashes the game as the song loads. A part whose stem is
    # missing still gets channels, silent ones, so missing a note mutes silence
    # and the song carries on from the backing.
    parts = library.charted_parts(src)
    if not parts:
        return False, "the chart has no drums, bass, guitar or vocals"
    plan = [dict(p, files=[present[k] for k in p["keys"]])
            for p in library.channel_plan(set(present), parts,
                                          settings.wide_mix)]
    if not any(p["files"] for p in plan):
        return False, "the song folder has no audio stems"

    # Guard the VGS 15-channel ceiling.
    total = sum(p["width"] for p in plan)
    if total > 15:
        return False, "needs %d channels, over the VGS limit" % total

    out_dir = os.path.join(settings.stage, sid)
    os.makedirs(out_dir, exist_ok=True)
    main_out, prev_out, layout_path = outputs(settings, sid)

    probed = {f: probe_audio(settings, f) for p in plan for f in p["files"]}
    longest = max(dur for _, dur in probed.values())

    inputs, filters, labels = [], [], []
    idx = 0
    for p in plan:
        if not p["files"]:
            # Overrun the song deliberately: amerge ends with its shortest
            # input, so silence must not be what decides the mix's length.
            inputs += ["-f", "lavfi", "-t", "%.3f" % (longest + 1.0), "-i",
                       "anullsrc=channel_layout=%s:sample_rate=%d"
                       % ("stereo" if p["width"] == 2 else "mono", RATE)]
            filters.append("[%d:a]anull[r_%s]" % (idx, p["role"]))
            labels.append("[r_%s]" % p["role"])
            idx += 1
            continue
        merged_from = []
        for f in p["files"]:
            ch, _ = probed[f]
            inputs += ["-i", f]
            lbl = "s%d" % idx
            filters.append("[%d:a]aresample=%d,%s[%s]"
                           % (idx, RATE, pan_expr(ch, p["width"]), lbl))
            merged_from.append("[%s]" % lbl)
            idx += 1
        # Multiple stems on one role (song+keys) get summed, not concatenated.
        if len(merged_from) > 1:
            filters.append("%samix=inputs=%d:normalize=0[r_%s]"
                           % ("".join(merged_from), len(merged_from), p["role"]))
        else:
            filters.append("%sanull[r_%s]" % (merged_from[0], p["role"]))
        labels.append("[r_%s]" % p["role"])

    joined = "".join(labels)
    if len(labels) > 1:
        filters.append("%samerge=inputs=%d[pre]" % (joined, len(labels)))
    else:
        filters.append("%sanull[pre]" % joined)
    filters.append("[pre]adelay=%d:all=1[out]" % LEAD_SILENCE_MS)

    if log:
        log("mixing %d channels: %s"
            % (total, ",".join("%s:%d%s" % (p["role"], p["width"],
                                            "" if p["files"] else " silent")
                               for p in plan)))
    cmd = [settings.tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
           "-y"] + inputs + [
        "-filter_complex", ";".join(filters),
        "-map", "[out]", "-c:a", "libvorbis", "-q:a", "6", main_out]
    r = proc.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(main_out):
        return False, "mixing the stems failed: %s" % (
            (r.stderr or r.stdout).strip()[:160])

    losses = {}
    for p in plan:
        if not p["files"]:
            continue
        db = fold_loss(settings, p["files"], p["width"])
        if db >= MIN_TRIM_DB:
            losses[p["role"]] = db
    vols = levels_for(plan, losses)
    if log and losses:
        log("mixing down cost %s, so the rest is trimmed to match"
            % ", ".join("%s %.1f dB" % (role, db)
                        for role, db in sorted(losses.items())))

    # Assign channel indices and derive the songs.dta arrays.
    tracks, pans, cores = {}, [], []
    ch_i = 0
    drum_ch = []
    for p in plan:
        idxs = list(range(ch_i, ch_i + p["width"]))
        ch_i += p["width"]
        if p["role"] in DRUM_ROLES:
            drum_ch += idxs
        elif p["role"] == "bass":
            tracks["bass"] = idxs
        elif p["role"] == "guitar":
            tracks["guitar"] = idxs
        elif p["role"] == "vocals":
            tracks["vocals"] = idxs
        for j, _ in enumerate(idxs):
            pans.append(0.0 if p["width"] == 1 else (-1.0 if j % 2 == 0 else 1.0))
            cores.append(1 if p["role"] == "guitar" else -1)
    if drum_ch:
        tracks["drum"] = drum_ch

    # The preview is cut from the mix that was just written, not from the stems,
    # and folded the way the console folds it. Summing the stems instead left the
    # song list playing a clip 13 dB above the song and clipping on top of that.
    prev_start = preview_start(meta, longest)
    fold = console_mix(pans, vols)
    peak = peak_of(settings, main_out, fold, prev_start + LEAD_SILENCE_MS / 1000.0,
                   PREVIEW_SECS)
    # A song whose own mix reaches full scale would have the console clip it. The
    # clip cannot follow it there, so it comes down far enough to stay clean.
    trim = min(0.0, PREVIEW_PEAK_DB - peak) if peak is not None else 0.0
    if log:
        log("cutting the %d s preview from %d:%02d in%s"
            % (PREVIEW_SECS, int(prev_start) // 60, int(prev_start) % 60,
               "" if trim > -0.05 else ", %.1f dB down to keep it clear of full "
               "scale" % -trim))
    chain = [fold]
    if trim < -0.05:
        chain.append("volume=%.2fdB" % trim)
    chain.append("afade=t=in:st=0:d=%s" % PREVIEW_FADE_IN)
    chain.append("afade=t=out:st=%d:d=%d"
                 % (PREVIEW_SECS - PREVIEW_FADE_OUT, PREVIEW_FADE_OUT))
    r = proc.run([settings.tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
                  "-y", "-ss", "%.3f" % (prev_start + LEAD_SILENCE_MS / 1000.0),
                  "-t", str(PREVIEW_SECS), "-i", main_out,
                  "-af", ",".join(chain), "-c:a", "libvorbis", "-q:a", "5",
                  prev_out], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(prev_out):
        return False, "building the preview failed: %s" % (
            (r.stderr or r.stdout).strip()[:160])

    info = {
        "id": sid,
        "source": src,
        "title": meta.get("name", folder_name),
        "artist": meta.get("artist", ""),
        "album": meta.get("album", ""),
        "genre": meta.get("genre", ""),
        "year": meta.get("year", ""),
        "channels": ch_i,
        "parts": sorted(parts),
        "tracks": tracks,
        "pans": pans,
        "vols": vols,
        "cores": cores,
        "preview_ms": [int(prev_start * 1000) + LEAD_SILENCE_MS,
                       int(prev_start * 1000) + LEAD_SILENCE_MS + PREVIEW_SECS * 1000],
        "diff": {k: meta.get("diff_" + k, "-1")
                 for k in ("drums", "bass", "guitar", "vocals", "band")},
        "roles": [{"role": p["role"], "width": p["width"]} for p in plan],
        "main_ogg": os.path.basename(main_out),
        "prev_ogg": os.path.basename(prev_out),
    }
    # How much silence the chart needs is worked out by the chart stage and left
    # in here, so mixing a song again on its own must not throw that away: the
    # figure belongs to the chart and the audio it describes has not moved.
    if os.path.exists(layout_path):
        try:
            with open(layout_path, encoding="utf-8") as fp:
                lead = json.load(fp).get("extra_lead_ms")
        except (OSError, ValueError):
            lead = None
        if lead is not None:
            info["extra_lead_ms"] = lead
    with open(layout_path, "w", encoding="utf-8") as fp:
        json.dump(info, fp, indent=2)

    mb = os.path.getsize(main_out) / 1048576.0
    return True, ("%d channels, %s, %.1f MB ogg"
                  % (ch_i, ",".join("%s:%d" % (p["role"], p["width"])
                                    for p in plan), mb))
