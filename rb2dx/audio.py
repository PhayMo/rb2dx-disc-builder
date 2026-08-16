"""Prepare PS2 Rock Band 2 audio from Clone Hero source stems.

For each song this writes into that song's stage folder:
  <id>.ogg       multichannel mix, 22050 Hz, 3s of leading silence
  prev_<id>.ogg  stereo preview excerpt
  layout.json    channel layout + metadata, consumed by the songs.dta generator

The 3 seconds of silence exist because PS2 Rock Band starts reading the chart
3 seconds in; the chart itself is left untouched. Channel order here must match
the tracks/pans/vols arrays written into songs.dta.

Which channels a song gets is decided by library.channel_plan, from the stems in
the folder and the parts its chart plays.
"""

import json
import os

from . import library, proc
from .library import AUDIO_EXT, read_ini

RATE = 22050
# The console starts reading a chart three seconds into the audio stream, so
# every mix opens with that much silence. Charts that Onyx pushed further along
# need more, which is only known once the chart is built and so is added when the
# mix is encoded for the console: see charts.measure_pad.
LEAD_SILENCE_MS = 3000
PREVIEW_SECS = 30
DRUM_ROLES = ("kick", "snare", "kit")


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
            for p in library.channel_plan(set(present), parts)]
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

    # Stereo preview excerpt: full mix, starting at the chart's preview point.
    prev_start = int(meta.get("preview_start_time", "0") or 0) / 1000.0
    all_stems = [f for k, f in present.items() if k != "crowd"]
    p_inputs, p_filters, p_labels = [], [], []
    for i, f in enumerate(all_stems):
        ch, _ = probe_audio(settings, f)
        p_inputs += ["-ss", "%.3f" % prev_start, "-t", str(PREVIEW_SECS), "-i", f]
        p_filters.append("[%d:a]aresample=%d,%s[p%d]" % (i, RATE, pan_expr(ch, 2), i))
        p_labels.append("[p%d]" % i)
    p_filters.append("%samix=inputs=%d:normalize=0,afade=t=out:st=%d:d=2[pout]"
                     % ("".join(p_labels), len(p_labels), PREVIEW_SECS - 2))
    if log:
        log("cutting the %d s preview" % PREVIEW_SECS)
    r = proc.run([settings.tool("ffmpeg"), "-hide_banner", "-loglevel",
                        "error", "-y"] + p_inputs +
                       ["-filter_complex", ";".join(p_filters), "-map", "[pout]",
                        "-c:a", "libvorbis", "-q:a", "5", prev_out],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(prev_out):
        return False, "building the preview failed: %s" % (
            (r.stderr or r.stdout).strip()[:160])

    # Assign channel indices and derive the songs.dta arrays.
    tracks, pans, vols, cores = {}, [], [], []
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
            vols.append(0.0)
            cores.append(1 if p["role"] == "guitar" else -1)
    if drum_ch:
        tracks["drum"] = drum_ch

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
    with open(layout_path, "w", encoding="utf-8") as fp:
        json.dump(info, fp, indent=2)

    mb = os.path.getsize(main_out) / 1048576.0
    return True, ("%d channels, %s, %.1f MB ogg"
                  % (ch_i, ",".join("%s:%d" % (p["role"], p["width"])
                                    for p in plan), mb))
