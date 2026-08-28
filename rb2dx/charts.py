"""Produce RB2 chart assets for a staged song by way of Onyx.

Onyx has no direct PS2 Rock Band 2 target, but its Xbox 360 RB2 target emits a
correct RB2 MIDI, a generated weights.bin, and a songs.dta containing properly
computed difficulty ranks. This imports one Clone Hero folder, builds the RB2
CON, extracts it, and copies the useful pieces into the staging folder under our
own song id.

The CON's audio is discarded; PS2 uses the VGS built separately.

check_mix is the gate that runs afterwards: it compares the drum mix events in
the finished chart against the channel layout of the audio it will be played
with, because the two disagreeing is what hangs the console.
"""

import json
import locale
import os
import re
import shutil
import subprocess

from . import midfix, proc
from .errors import BuildError

# The encoding Onyx writes its output in. See nameable below.
CONSOLE_ENCODING = locale.getpreferredencoding(False)

# Extensions left out of a staged import: the video is encoded separately.
SKIP_FOR_IMPORT = (".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v", ".mpg",
                   ".mpeg", ".ogv")

# Drum channel count each mix mode needs, for the two modes this disc pins down
# exactly: Can't Buy Me Love plays two channels against drums0, and retail
# Afterlife has four against drums3. The other modes are not pinned - the DX
# placeholder songs pair drums1 and drums2 with five-channel submixes, which
# only bounds them from above - so every chart is retargeted to one of these
# two on the way through. Seeing anything else here means that retarget did
# not happen.
DRUM_MODE_CHANNELS = {0: 2, 3: 4}

EVENT = re.compile(rb"\[mix (\d+) drums(\d+)([a-z]*)\]")


def run(cmd, timeout=None):
    # Onyx can wedge rather than fail. Nothing passes a timeout today, so the
    # default leaves the wait exactly as it has always been; when one is given,
    # running out of time comes back as an ordinary failure message instead of
    # an exception, so it reaches the caller the same way a build error does.
    try:
        r = proc.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "timed out after %s seconds" % timeout
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def magma_errors(out, limit=4):
    """What Magma objected to, out of everything else its log says.

    Its complaints are the one useful part of a failed build and each names the
    track it is unhappy with, but they arrive among hundreds of lines of
    progress, and prefixed with the path of a scratch file nobody has any use
    for.
    """
    seen = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("ERROR"):
            continue
        line = re.sub(r"^MIDI Compiler:\s*", "",
                      line[len("ERROR"):].lstrip(":! "))
        line = re.sub(r"^\S+\.mid\s*", "", line)
        if line and line not in seen:
            seen.append(line)
    if not seen:
        return ""
    said = "; ".join(seen[:limit])
    if len(seen) > limit:
        said += " (and %d more)" % (len(seen) - limit)
    return said


def nameable(path):
    """Whether Onyx can print this path.

    Onyx writes the folder it is importing to its output, and a character that
    output's encoding cannot represent ends it with 'commitBuffer: invalid
    argument (invalid character)' before the chart is read.
    """
    try:
        path.encode(CONSOLE_ENCODING)
        return True
    except UnicodeEncodeError:
        return False


def stage_for_import(source_dir, dest):
    """Put a song's files somewhere Onyx can name. Returns that folder.

    Linked rather than copied where the filesystem allows it.
    """
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    for name in sorted(os.listdir(source_dir)):
        src = os.path.join(source_dir, name)
        if not os.path.isfile(src):
            continue
        if os.path.splitext(name)[1].lower() in SKIP_FOR_IMPORT:
            continue
        target = os.path.join(dest, name)
        try:
            os.link(src, target)
        except OSError:
            shutil.copy2(src, target)
    return dest


def fix_track_number(yml_path):
    """Give the song a track number, because Magma refuses to build without one.

    song.ini's album_track is often 0 or absent and Onyx passes that through, so
    Magma stops at 'track_number: value is 0, which is less than the minimum
    value of 1'.
    """
    with open(yml_path, encoding="utf-8") as fp:
        text = fp.read()
    found = re.search(r"^(?P<indent>[ \t]+)track-number:[ \t]*(?P<value>\S*)",
                      text, re.M)
    if found:
        try:
            if int(found.group("value")) >= 1:
                return ""
        except ValueError:
            pass
        was = found.group("value") or "unset"
        text = (text[:found.start()] + found.group("indent")
                + "track-number: 1" + text[found.end():])
    else:
        head = re.search(r"^metadata:[ \t]*$", text, re.M)
        if not head:
            return ""
        was = "unset"
        text = text[:head.end()] + "\n  track-number: 1" + text[head.end():]
    with open(yml_path, "w", encoding="utf-8") as fp:
        fp.write(text)
    return ("track number was %s, which Magma will not build; it is now 1"
            % was)


def add_rb2_target(yml_path):
    with open(yml_path, "r", encoding="utf-8") as fp:
        text = fp.read()
    if re.search(r"^  rb2:", text, re.M):
        return
    if "targets:" not in text:
        text = text.rstrip() + "\ntargets:\n"
    text = text.rstrip() + "\n  rb2:\n    game: rb2\n"
    with open(yml_path, "w", encoding="utf-8") as fp:
        fp.write(text)


def outputs(settings, sid):
    song_stage = os.path.join(settings.stage, sid)
    return (os.path.join(song_stage, sid + ".mid"),
            os.path.join(song_stage, sid + ".pan"))


def is_done(settings, sid):
    mid, pan = outputs(settings, sid)
    # The .pan is meant to be empty, so only its presence says anything.
    if not (os.path.exists(mid) and os.path.getsize(mid) > 0
            and os.path.exists(pan)):
        return False
    # A chart that never recorded how far Onyx pushed it leaves the audio with
    # nothing to line itself up against, so it counts as unbuilt.
    return recorded_lead(settings, sid) is not None


def _layout(settings, sid):
    """The layout the audio step wrote, which the chart has to agree with.

    It says how many drum channels the mix has, for the chart's mix events, and
    which parts the song offers, which are the only ones the chart may hold.
    """
    layout = os.path.join(settings.stage, sid, "layout.json")
    try:
        with open(layout, encoding="utf-8") as fp:
            info = json.load(fp)
        return (len(info["tracks"].get("drum", [])),
                set(info["parts"]))
    except (OSError, ValueError, KeyError):
        raise BuildError(
            "%s has no usable audio layout yet, so its chart cannot be lined "
            "up with the mix. Build this song's audio first." % sid)


def build(settings, sid, source_dir, log=None):
    """Convert one song's chart. Returns (ok, message).

    Onyx takes about half a minute per song, which is an hour across a full
    disc. Each song builds in its own scratch directory, so several of these
    can run at the same time; the work is Onyx subprocesses rather than Python.
    """
    onyx = settings.tool("onyx")
    drum_width, parts = _layout(settings, sid)

    song_stage = os.path.join(settings.stage, sid)
    work = os.path.join(settings.chart_scratch, sid)
    proj = os.path.join(work, "proj")
    con = os.path.join(work, "rb2con")
    xdir = os.path.join(work, "extract")
    for d in (proj, xdir):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(work, exist_ok=True)

    from_dir = source_dir
    if not nameable(source_dir):
        from_dir = stage_for_import(source_dir, os.path.join(work, "src"))
        _say(log, "this folder's name has characters Onyx cannot print, so its "
                  "files are imported from %s instead" % from_dir)

    code, out = run([onyx, "import", from_dir, "--to", proj])
    if not os.path.exists(os.path.join(proj, "song.yml")):
        return False, "import failed: %s" % out.strip()[-200:]

    add_rb2_target(os.path.join(proj, "song.yml"))
    note = fix_track_number(os.path.join(proj, "song.yml"))
    if note:
        _say(log, note)
    # Magma is strict about things the source charts get away with, and it runs
    # before we ever see the RB2 MIDI, so the fixes go in on the way through.
    # Parts the song does not offer go first, since a fault in one of those is
    # no reason to fail the song.
    notes = os.path.join(proj, "notes.mid")
    for line in midfix.keep_only_parts(notes, parts):
        _say(log, line)
    for line in midfix.fix_lyrics(notes):
        _say(log, line)
    for line in midfix.fix_vocal_phrases(notes):
        _say(log, line)
    for line in midfix.fix_vocal_notes(notes):
        _say(log, line)
    # Before the reductions, which take their lanes from what Expert plays.
    for line in midfix.fix_wide_chords(notes):
        _say(log, line)
    for line in midfix.fix_reductions(notes):
        _say(log, line)
    for line in midfix.fix_coda_overrun(notes):
        _say(log, line)
    for line in midfix.fix_big_rock_ending(notes):
        _say(log, line)
    for line in midfix.fix_shared_phrases(notes):
        _say(log, line)
    # After the phrases have stopped moving, so nothing is left hanging out of one.
    for line in midfix.fix_notes_in_phrases(notes):
        _say(log, line)
    # Last, so it lands past whatever the fixes above left behind.
    for line in midfix.fix_end_marker(notes):
        _say(log, line)

    if os.path.exists(con):
        os.remove(con)
    code, out = run([onyx, "build", os.path.join(proj, "song.yml"),
                     "--target", "rb2", "--to", con])
    # The whole log stays on disk next to the project, since a failure usually
    # takes more reading than one line of it can hold.
    log_path = os.path.join(work, "build.log")
    with open(log_path, "w", encoding="utf-8") as fp:
        fp.write(out)
    if not os.path.exists(con):
        return False, "%s (the whole log is in %s)" % (
            magma_errors(out) or out.strip()[-300:], log_path)

    code, out = run([onyx, "extract", con, "--to", xdir])
    songs_root = os.path.join(xdir, "songs")
    if not os.path.isdir(songs_root):
        return False, "extract failed: %s" % out.strip()[-200:]

    inner = [d for d in os.listdir(songs_root)
             if os.path.isdir(os.path.join(songs_root, d))]
    if not inner:
        return False, "no song folder inside CON"
    con_id = inner[0]
    con_song = os.path.join(songs_root, con_id)

    gen_out = os.path.join(song_stage, "gen")
    os.makedirs(gen_out, exist_ok=True)

    mid_src = os.path.join(con_song, con_id + ".mid")
    if not os.path.exists(mid_src):
        return False, "no .mid produced"
    # Onyx targets RB2 on Xbox 360, which accepts chart content the PS2 build
    # hangs on. Keep its output for reference and ship a conformed copy; see
    # midfix.py for what gets removed and why.
    raw = os.path.join(song_stage, sid + "_onyx.mid")
    shutil.copyfile(mid_src, raw)
    shipped = os.path.join(song_stage, sid + ".mid")
    fixes = midfix.conform(raw, shipped,
                           do_events=True, do_lighting=True, do_order=True,
                           rename=sid, drum_width=drum_width, keep_parts=parts)
    for line in fixes:
        _say(log, line)

    # The song list will offer exactly the parts the audio was mixed for, so the
    # chart has to hold all of them. Onyx can drop one - a part too sparse to
    # reduce, say - and shipping a song that offers a part with no notes behind
    # it is what crashes the game as it loads.
    missing = parts - midfix.instrument_parts(shipped)
    if missing:
        return False, ("the built chart has nothing on %s, which the song "
                       "offers to play" % ", ".join(sorted(missing)))

    lead = record_lead(settings, sid,
                       measure_pad(os.path.join(proj, "notes.mid"), raw))
    _say(log, "onyx moved the chart %.3f s, so the audio gets that much more "
              "silence in front of it" % (lead / 1000.0))

    w_src = os.path.join(con_song, "gen", con_id + "_weights.bin")
    if os.path.exists(w_src):
        shutil.copyfile(w_src, os.path.join(gen_out, sid + "_weights.bin"))

    # Retail PS2 songs carry a .pan; Onyx emits an empty one, so match that.
    open(os.path.join(song_stage, sid + ".pan"), "wb").close()

    dta_src = os.path.join(songs_root, "songs.dta")
    if os.path.exists(dta_src):
        shutil.copyfile(dta_src, os.path.join(song_stage, "onyx_songs.dta"))

    mid_kb = os.path.getsize(os.path.join(song_stage, sid + ".mid")) / 1024.0
    return True, "mid=%.0f KB weights=%s dta=%s conformed=%d" % (
        mid_kb,
        "yes" if os.path.exists(os.path.join(gen_out, sid + "_weights.bin")) else "NO",
        "yes" if os.path.exists(os.path.join(song_stage, "onyx_songs.dta")) else "NO",
        len(fixes))


def _say(log, text):
    if log:
        log(text)


# The console starts reading a chart three seconds into the song's audio stream,
# which is why every mix is built with three seconds of silence in front of it.
# Onyx adds silence of its own on top of that: its build pushes a chart forward
# by a whole number of seconds until the first note sits at least three seconds
# in, so a chart that starts at zero moves three seconds and one that already
# starts late is left alone. That padding has to be matched by the audio or the
# notes turn up that much after the music - which is what a community chart
# starting near zero used to do, by up to three seconds. It is measured rather
# than assumed: the same chart before and after Onyx's build, and the difference
# between where their first notes land.
MAX_PAD_SECONDS = 4.0


def measure_pad(source_mid, built_mid):
    """Seconds of silence Onyx's build put in front of a chart."""
    before = midfix.first_note_seconds(source_mid)
    after = midfix.first_note_seconds(built_mid)
    if before is None or after is None:
        raise BuildError("chart has no playable notes on any instrument")
    pad = after - before
    if not -0.001 <= pad <= MAX_PAD_SECONDS:
        raise BuildError(
            "Onyx moved this chart %.3f s, which is outside anything this has "
            "been proven against, so the audio cannot be lined up with it. Its "
            "first note went from %.3f s to %.3f s." % (pad, before, after))
    return max(pad, 0.0)


def recorded_lead(settings, sid):
    """The extra silence noted for this song, in ms, or None if there is none."""
    path = os.path.join(settings.stage, sid, "layout.json")
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp).get("extra_lead_ms")
    except (OSError, ValueError):
        return None


def record_lead(settings, sid, pad_seconds):
    """Note in the layout how much extra silence this song's audio needs."""
    path = os.path.join(settings.stage, sid, "layout.json")
    with open(path, encoding="utf-8") as fp:
        info = json.load(fp)
    info["extra_lead_ms"] = int(round(pad_seconds * 1000))
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(info, fp, indent=2)
    return info["extra_lead_ms"]


# ---- the mix gate ---------------------------------------------------------
#
# A chart's drum mix events name a submix width: [mix N drums0] means a stereo
# kit, [mix N drums3] means the four-channel kick/snare/kit split. If the chart
# asks for more drum channels than the mix actually has, the game points its
# mixer at channels that do not exist and the song hangs forever on the loading
# screen - which is exactly what happened when our two-channel audio was paired
# with Afterlife's drums3 chart. Asking for fewer than exist is harmless.
#
# Onyx derives its mix events from the source stems and audio.py derives our
# channel layout from the same stems, so the two normally agree; this exists to
# catch the case where they do not, before a build reaches the console.


def chart_modes(path):
    """Every drum mix mode the chart asks for.

    The events are plain ASCII inside MIDI text meta-events, so scanning the
    raw bytes finds them all without walking the track structure; which track
    they sit on does not change what the mixer is asked for.
    """
    with open(path, "rb") as fp:
        data = fp.read()
    return sorted({int(m.group(2)) for m in EVENT.finditer(data)})


def check_mix(settings, sid):
    """Return (ok, reason) for one staged song."""
    d = os.path.join(settings.stage, sid)
    mid = os.path.join(d, "%s.mid" % sid)
    layout = os.path.join(d, "layout.json")
    if not (os.path.exists(mid) and os.path.exists(layout)):
        return True, "no chart or layout yet, nothing to check"

    with open(layout, encoding="utf-8") as fp:
        info = json.load(fp)
    have = len(info.get("tracks", {}).get("drum", []))
    modes = chart_modes(mid)
    if not modes:
        return True, "chart has no drum mix events"

    worst = max(modes)
    need = DRUM_MODE_CHANNELS.get(worst)
    if need is None:
        return False, ("chart asks for drums%d, a mode of unverified width - "
                       "rebuild the chart so it is retargeted" % worst)
    if need > have:
        return False, ("chart asks for drums%d (%d drum channels) but the mix "
                       "has %d" % (worst, need, have))
    return True, ("drums%s over %d drum channels"
                  % ("/".join(str(m) for m in modes), have))
