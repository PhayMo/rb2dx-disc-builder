"""Build each staged song's main audio file: a PS2 .pss stream.

PS2 Rock Band never stores a song's main audio as a bare .vgs. Every retail song
ships a .pss, an MPEG-2 program stream holding the venue video as stream 0xE0
and the multichannel VGS as data stream 1. Without it the game finds no audio
for the song and hangs on the loading screen.

The mux is done by ps2str (PS2 SDK). Its direction-file grammar is:

    pss
    stream video:0
    input <file.m2v>
    end
    stream data:1
    input <file.vgs>
    rate <bytes per second>
    end
    end

Re-muxing a retail song's own demuxed streams with this recipe reproduces the
original .pss byte for byte, which is how these settings were confirmed.
"""

import hashlib
import json
import os
import random
import re
import shutil
import struct
import subprocess

from . import proc
from .errors import BuildError
from .library import read_ini
from .settings import VIDEO_EXT, own_video, videos_in

# Retail video: 400x304 MPEG-2, 29.97 fps, constant bit rate. Retail uses
# 2000 kbit/s; 1500 is the tutorial's recommendation and buys ~8 MB of disc,
# which matters because every megabyte counts against the size limit below
# which the console treats the image as a CD rather than a DVD. The rate itself
# comes from the settings, which default to 1500.
WIDTH, HEIGHT = 400, 304
# The console plays that frame across the whole screen, so with the game set to
# 16:9 it comes out a third wider than it was drawn. A disc meant for that setting
# has the picture squeezed to suit: the clip is framed 16:9 at this width and then
# pressed into the 400 the stream carries, which the game's own stretch undoes.
# The same is true, less obviously, of a 4:3 screen: 400 by 304 is not quite 4:3,
# so the shape a picture has to be fitted inside is 406 at that height.
WIDE_WIDTH = 540
FLAT_WIDTH = 406
FPS = "30000/1001"
# Retail sequence headers declare a 40-unit VBV buffer (units are 16384 bits).
# ffmpeg's own default is far larger, and the PS2's video decoder has only a
# small buffer to fill, so state retail's figure explicitly.
VBV_BITS = 40 * 16384
# Retail's picture shape too: an I frame every 18, two B frames between each
# anchor, IBBPBBPBB... A frame that only repeats the one before it is nearly free
# as a B frame, and what that buys is the I frames. Retail spends 27 kB on an I
# frame inside the same buffer a flat run of P frames leaves 13 kB for, which is
# the difference between a clip that holds its picture and one that visibly
# redraws it every time the frames come round.
GOP, B_FRAMES = 18, 2
# Both cost nothing on the disc and a little encoding time: trellis quantisation
# spends each frame's bits where they show, and mv0 has the encoder try leaving a
# block exactly where it was, which is the right answer for most of an animation.
STEADY = ("-trellis", "2", "-mpv_flags", "+mv0")
# The console starts the chart 3s into the stream, so audio and video both open
# with 3s of nothing. Some songs' audio opens with more than that, and the video
# is sized from the finished audio below, so it stays the shorter of the two.
LEAD_IN = 3.0
# A preview of what a background will look like, for the Songs page: how long it
# runs, and what each side of it is levelled to so neither drowns the other.
WATCH_SECS = 25.0
LEVEL = "loudnorm=I=-18:TP=-2:dual_mono=true"
# "The audio needs to be longer than the video, otherwise the song will freeze
# when you finish it" - so the video stops short of the audio.
TAIL_SLACK = 2.0

SAMPLES_PER_BLOCK = 28
ADPCM_BLOCK = 16

# Bumped when the encode changes, so a song staged by an older version has its
# video made again while its chart, art and audio are left alone. 2: retail's
# picture shape, and a steadier encode for a clip that holds still.
SHAPE = 2


# An animation or a GIF is usually drawn at 6 frames a second, so playing it at
# the disc's 30 holds each picture for four or five frames. Those held frames are
# where a flicker comes from: the encoder has to send something for every one of
# them, and what it sends is never quite the picture it sent before, so a still
# image quietly crawls. Clips like that get the two settings below, which cost a
# touch of detail and take most of that movement out. A clip that is really shot
# footage repeats nothing and is left sharp.
STILL_SHARE = 0.35
STILL_SAMPLE = 10.0
STILL_SIZE = (100, 76)
# Fine detail is what the encoder cannot hold steady, and line art downscaled to
# 400x304 is nothing but fine detail. Half a pixel of blur is not visible behind
# a note highway; the crawling is.
SOFTEN = "gblur=sigma=0.5"
# And a floor under the quantiser, so the bitrate is not spent chasing detail
# finer than the format can keep still from one frame to the next.
STILL_QMIN = 3


def vgs_info(path):
    with open(path, "rb") as fp:
        head = fp.read(0x80)
    if head[:4] != b"VgS!":
        raise BuildError("%s is not a VGS file. Delete it and encode this "
                         "song's audio again." % path)
    version = struct.unpack_from("<I", head, 4)[0]
    chans = []
    for i in range(15):
        rate, blocks = struct.unpack_from("<II", head, 8 + i * 8)
        if rate == 0:
            break
        chans.append((rate, blocks))
    rate, blocks = chans[0]
    return {
        "version": version,
        "channels": len(chans),
        "rate": rate,
        "seconds": blocks * SAMPLES_PER_BLOCK / float(rate),
    }


def data_rate(rate, channels):
    """Bytes per second to declare for the VGS stream.

    PS-ADPCM packs 28 samples into 16 bytes, so a channel costs
    16 * rate / 28 bytes per second. Every retail stream then declares 100
    bytes/sec more than that figure, and the PS2 customs notes call the extra
    100 out explicitly as required, so match it.
    """
    return int(ADPCM_BLOCK * rate / SAMPLES_PER_BLOCK) * channels + 100


def song_video(source_dir):
    """The video a song folder brought with it, or "" if it has none."""
    if not source_dir or not os.path.isdir(source_dir):
        return ""
    name = own_video(os.listdir(source_dir))
    return os.path.join(source_dir, name) if name else ""


def pick_venue(sid, venue_dir):
    vids = videos_in(venue_dir)
    if not vids:
        raise BuildError(
            "There are no background videos to play behind the songs. Point the "
            "venue folder at some video files (%s), or set the background to "
            "black." % ", ".join(VIDEO_EXT))
    # Seed from the song id so a rebuild picks the same clip.
    seed = int(hashlib.md5(sid.encode()).hexdigest()[:8], 16)
    return os.path.join(venue_dir, random.Random(seed).choice(vids))


def choose_video(sid, venue_dir, source_dir=""):
    """What plays behind one song: its own video if it has one, else a venue clip.

    Returns (path, is_the_song's_own).
    """
    own = song_video(source_dir)
    if own:
        return own, True
    return pick_venue(sid, venue_dir), False


def shift_for(settings, source_dir):
    """Seconds this song's own video is moved by, and where that came from.

    The Songs page keeps a nudge per folder and that is the last word on it.
    Failing one, song.ini's video_start_time is honoured, which is where in the
    video Clone Hero starts playing when the song starts; a negative value there
    holds the video back instead.
    """
    nudge = settings.nudge(source_dir)
    if nudge:
        return nudge, "nudged"
    ini = os.path.join(source_dir or "", "song.ini")
    if not os.path.exists(ini):
        return 0.0, ""
    try:
        raw = float(read_ini(ini).get("video_start_time", 0) or 0) / 1000.0
    except ValueError:
        return 0.0, ""
    return raw, "song.ini" if raw else ""


def clip_seconds(settings, path):
    """How long a clip runs, or 0 if that cannot be read."""
    out = proc.run(
        [settings.tool("ffprobe"), "-v", "error", "-show_entries",
         "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def offsets(settings, src, shift, seconds):
    """(seconds to skip once the clip is looping, seconds of black in front).

    A clip shorter than the song plays round and round, and moving it moves where
    it starts: a backwards nudge wraps to the end of the clip rather than showing
    black, a loop having no beginning to hold back from. A clip long enough to
    cover the song on its own does have one, so there a backwards nudge is black.
    """
    if not shift or not src:
        return 0.0, 0.0
    length = clip_seconds(settings, src)
    if length and length < seconds:
        return shift % length, 0.0
    return (shift, 0.0) if shift > 0 else (0.0, -shift)


def still_share(settings, src, start=0.0):
    """The share of frames this clip would repeat, played at the disc's rate.

    Measured small and only over the opening, which is enough to tell a six-frame
    animation from shot footage and costs a fraction of a second.
    """
    cmd = [settings.tool("ffmpeg"), "-hide_banner", "-loglevel", "error"]
    if start:
        cmd += ["-ss", "%.3f" % start]
    cmd += ["-t", "%.1f" % STILL_SAMPLE, "-i", src,
            "-vf", "scale=%d:%d,fps=%s,signalstats,metadata=print:file=-"
            % (STILL_SIZE[0], STILL_SIZE[1], FPS),
            "-f", "null", os.devnull]
    r = proc.run(cmd, capture_output=True, text=True)
    seen = re.findall(r"lavfi\.signalstats\.YDIF=([\d.]+)", r.stdout or "")
    if not seen:
        return 0.0
    return len([v for v in seen if float(v) == 0.0]) / float(len(seen))


def shape_filter(settings, whole=False):
    """Filters that turn any clip into the frame the disc carries.

    Fills that frame, cropping whatever will not fit, and for a widescreen disc
    squeezes the wider frame into the stream's 400 across.

    `whole` keeps all of the picture instead, fitting it inside the frame and filling
    what is left over with black. A background clip is wallpaper and is better off
    filling the screen, but a song's own video was chosen for what is in it: cropping
    a widescreen music video into a 4:3 frame takes a quarter of its width away, which
    on a title card means the words run off both sides.
    """
    frame = WIDE_WIDTH if settings.widescreen else WIDTH
    if whole:
        # Fitted in a frame of the shape the screen shows rather than of the shape
        # the stream is stored in. The 400 across is drawn over the whole of a 4:3
        # screen, so what a picture has to fit inside is 406 at this height, and
        # the squeeze back to 400 below is undone by the screen. Fitting in the
        # stored shape instead would leave a 4:3 clip a couple of lines of black
        # it has no need of.
        frame = WIDE_WIDTH if settings.widescreen else FLAT_WIDTH
        vf = ("scale=%d:%d:force_original_aspect_ratio=decrease:"
              "force_divisible_by=2,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,"
              "fps=%s" % (frame, HEIGHT, frame, HEIGHT, FPS))
    else:
        vf = ("scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,fps=%s"
              % (frame, HEIGHT, frame, HEIGHT, FPS))
    if frame != WIDTH:
        # setsar keeps the stream's header saying square pixels, as retail's does.
        # Left to itself ffmpeg would write 16:9 there, and what the console makes
        # of a header it never sees in its own videos is not worth finding out:
        # the squeeze is in the picture, not in a flag.
        vf += ",scale=%d:%d,setsar=1" % (WIDTH, HEIGHT)
    return vf


def extra_lead(settings, sid):
    """Silence the chart stage put in front of this song's audio, in seconds.

    The mix opens with three seconds of nothing because the console starts the
    chart three seconds in, and the video opens with the same. A chart that Onyx
    pushed further along needs more than that, and the video has to wait as long as
    the music does or the picture runs ahead of it.
    """
    try:
        with open(os.path.join(settings.stage, sid, "layout.json"),
                  encoding="utf-8") as fp:
            return (json.load(fp).get("extra_lead_ms") or 0) / 1000.0
    except (OSError, ValueError):
        return 0.0


def encode_video(settings, src, dst, seconds, start=0.0, delay=0.0,
                 steady=False, whole=False):
    """Encode one clip to the retail MPEG-2 shape, looping to length.

    start moves the clip that far along and delay puts that much extra black in
    front of it, which is how a song's own video is lined up with its audio. An
    empty src means a black background, generated here rather than read from a
    file: there is nothing to loop, scale or line up, so the whole stream is
    black and the bitrate can be a fraction of a real clip's. steady is for a
    clip that spends its time holding one picture, and trades a little of its
    detail for keeping that picture still. whole keeps all of the picture rather
    than filling the frame with it; see shape_filter.
    """
    kbps = settings.encode_kbps
    cmd = [settings.tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
    if src:
        vf = shape_filter(settings, whole)
        if start:
            # Moved after the clip is already looping, rather than by seeking the
            # file: a seek is applied again on every pass, which throws the front
            # of the clip away each time round and shortens the loop. Dropping the
            # opening of the looped stream instead moves where the clip starts and
            # keeps all of it, and a nudge longer than the clip simply wraps. The
            # frames skipped are decoded and discarded, which costs a moment.
            vf += ",trim=start=%.3f,setpts=PTS-STARTPTS" % start
        if steady:
            vf += "," + SOFTEN
        vf += ",tpad=start_duration=%s:start_mode=add:color=black" % (LEAD_IN
                                                                     + delay)
        cmd += ["-stream_loop", "-1", "-i", src, "-vf", vf]
    else:
        cmd += ["-f", "lavfi", "-i", "color=c=black:s=%dx%d:r=%s"
                % (WIDTH, HEIGHT, FPS)]
    cmd += ["-an", "-t", "%.3f" % seconds,
            "-c:v", "mpeg2video",
            "-b:v", "%dk" % kbps,
            "-minrate", "%dk" % kbps,
            "-maxrate", "%dk" % kbps,
            "-bufsize", "%d" % VBV_BITS,
            "-g", str(GOP), "-bf", str(B_FRAMES), *STEADY,
            "-pix_fmt", "yuv420p"]
    if steady:
        cmd += ["-qmin", str(STILL_QMIN)]
    cmd += [dst]
    return proc.run(cmd, capture_output=True, text=True)


def still(settings, src, dst, at=0.0, wide=0, whole=True):
    """One frame of a clip, framed as the disc will carry it, to look at.

    `wide` asks for it smaller than the disc's own frame, for a window with less
    room than that to give it. Only a song's own video is ever looked at this way,
    so all of the picture is kept by default, as the disc keeps it.
    """
    vf = shape_filter(settings, whole)
    if wide and wide < WIDTH:
        vf += ",scale=%d:%d" % (wide, round(wide * HEIGHT / float(WIDTH) / 2) * 2)
    r = proc.run([settings.tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
                  "-y", "-ss", "%.3f" % max(at, 0.0), "-i", src,
                  "-vf", vf, "-frames:v", "1", dst],
                 capture_output=True, text=True)
    return r.returncode == 0 and os.path.exists(dst)


def black_still(settings, dst, wide=0):
    """The same frame with nothing on it, for a moment the disc plays black."""
    across = wide if 0 < wide < WIDTH else WIDTH
    down = round(across * HEIGHT / float(WIDTH) / 2) * 2
    r = proc.run([settings.tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
                  "-y", "-f", "lavfi",
                  "-i", "color=c=black:s=%dx%d" % (across, down),
                  "-frames:v", "1", dst], capture_output=True, text=True)
    return r.returncode == 0 and os.path.exists(dst)


def watch(settings, clip, dst, clip_at, stems=(), song_at=0.0,
          seconds=WATCH_SECS, whole=True):
    """A piece of what the disc will play, to see and hear before building it.

    The picture goes through the same filters the disc's own encode uses, so it is
    framed the way the game frames it. The clip's own audio comes out of the left
    ear and the song out of the right, which is how two recordings are told apart
    by ear when they are playing together; a clip with no audio leaves that ear
    empty and the song is heard on its own.
    """
    heard = has_sound(settings, clip)
    cmd = [settings.tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
           "-stream_loop", "-1", "-i", clip]
    for path in stems:
        cmd += ["-i", path]

    # The clip is moved after it is looping, for the reason encode_video gives.
    chain = ["[0:v]%s,trim=start=%.3f,setpts=PTS-STARTPTS[v]"
             % (shape_filter(settings, whole), clip_at)]
    ears = []
    if heard:
        chain.append("[0:a]atrim=start=%.3f,asetpts=PTS-STARTPTS,"
                     "pan=mono|c0=c0,%s[clip]" % (clip_at, LEVEL))
        ears.append("[clip]")
    if stems:
        song = "".join("[%d:a]" % (i + 1) for i in range(len(stems)))
        if len(stems) > 1:
            song += "amix=inputs=%d:normalize=0," % len(stems)
        chain.append("%satrim=start=%.3f,asetpts=PTS-STARTPTS,pan=mono|c0=c0,%s"
                     "[song]" % (song, song_at, LEVEL))
        ears.append("[song]")
    if len(ears) == 2:
        chain.append("%sjoin=inputs=2:channel_layout=stereo[a]"
                     % "".join(ears))
    elif ears:
        chain.append("%sanull[a]" % ears[0])

    cmd += ["-filter_complex", ";".join(chain), "-map", "[v]"]
    if ears:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-t", "%.3f" % seconds, "-c:v", "libx264", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", dst]
    r = proc.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dst):
        raise BuildError("Could not put a preview together: %s"
                         % (r.stderr or r.stdout).strip()[:200])
    return heard


def has_sound(settings, path):
    """Whether a file carries any audio at all."""
    out = proc.run(
        [settings.tool("ffprobe"), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout
    return "audio" in (out or "")


def frame_note(settings, src):
    """How a clip sits in the disc's frame, for the log, or "" if it fills it.

    Worth a line because black at the edges is the sort of thing that gets reported
    as a fault, and the alternative - losing the sides of the picture - is worse.
    """
    got = probe_video(settings, src)
    if len(got) < 2 or not got[0].isdigit() or not got[1].isdigit():
        return ""
    shape = int(got[0]) / float(int(got[1]) or 1)
    frame = (16 / 9.0) if settings.widescreen else (4 / 3.0)
    if abs(shape - frame) < 0.03:
        return ""
    side = ("wider than the %s frame, so it keeps black above and below rather "
            "than losing its sides")
    tall = ("narrower than the %s frame, so it keeps black either side rather "
            "than losing its top and bottom")
    return (side if shape > frame else tall) % settings.screen


def probe_video(settings, path):
    out = proc.run(
        [settings.tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,bit_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True).stdout.split()
    return out


def mux(settings, video, vgs, out, rate):
    # ps2str is a 1999 tool: it splits tokens on ':' and dislikes spaces, so all
    # of its inputs are staged in a short, colon-free directory and named plainly.
    work = settings.tmp_dir("pss")
    v = os.path.join(work, "v.m2v")
    a = os.path.join(work, "a.vgs")
    shutil.copyfile(video, v)
    shutil.copyfile(vgs, a)

    job = os.path.join(work, "job.mux")
    with open(job, "w", newline="\n") as fp:
        fp.write("pss\nstream video:0\ninput v.m2v\nend\n"
                 "stream data:1\ninput a.vgs\nrate %d\nend\nend\n" % rate)

    tmp_out = os.path.join(work, "out.pss")
    if os.path.exists(tmp_out):
        os.remove(tmp_out)
    r = proc.run([settings.tool("ps2str"), "m", "-o", "job.mux", "out.pss"],
                 stdin=subprocess.DEVNULL, capture_output=True,
                 text=True, cwd=work)
    msg = (r.stdout + r.stderr).strip()
    if not os.path.exists(tmp_out) or os.path.getsize(tmp_out) == 0:
        raise BuildError(
            "ps2str could not build the song's audio stream. It cannot cope "
            "with spaces or long paths, so check the temp folder setting. It "
            "said: %s" % msg)
    shutil.move(tmp_out, out)
    for f in (v, a):
        os.remove(f)
    return msg


def outputs(settings, sid):
    d = os.path.join(settings.stage, sid)
    return (os.path.join(d, "%s.m2v" % sid),
            os.path.join(d, "%s.pss" % sid))


def encoded_from(m2v):
    """What the staged clip was encoded from, or {} if that is not recorded."""
    try:
        with open(m2v + ".json", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return {}


def is_done(settings, sid):
    m2v, pss = outputs(settings, sid)
    return (os.path.exists(m2v) and os.path.exists(pss)
            and os.path.getsize(pss) > 0)


def build(settings, sid, venue_dir, source_dir="", log=None):
    """Mux one staged song's video and audio into its .pss. Returns (ok, message)."""
    d = os.path.join(settings.stage, sid)
    vgs = os.path.join(d, "%s.vgs" % sid)
    if not os.path.exists(vgs):
        return False, "no %s.vgs yet (encode this song's audio first)" % sid

    info = vgs_info(vgs)
    rate = data_rate(info["rate"], info["channels"])
    vid_secs = max(1.0, info["seconds"] - TAIL_SLACK)
    # A black background is black behind every song, so a folder's own video is
    # passed over along with the venue clips.
    if settings.black_background:
        venue, own = "", False
    else:
        venue, own = choose_video(sid, venue_dir, source_dir)
    shift, told_by = shift_for(settings, source_dir) if own else (0.0, "")
    start, delay = offsets(settings, venue, shift, vid_secs)
    waited = extra_lead(settings, sid)
    what = "black" if not venue else os.path.basename(venue)

    if log:
        log("audio: %d ch @ %d Hz, %.2fs (VGS v%d) -> %d bytes/sec"
            % (info["channels"], info["rate"], info["seconds"],
               info["version"], rate))
        moved = ""
        if start:
            moved = ", from %.2fs in (%s %+.2fs)" % (start, told_by, shift)
        elif delay:
            moved = ", %.2fs of black first (%s %+.2fs)" % (delay, told_by, shift)
        if waited:
            moved += ", waiting %.2fs for the music" % waited
        if own:
            shaped = frame_note(settings, venue)
            if shaped:
                moved += ", " + shaped
        log("%s: %s%s" % ("the song's own video" if own else "background", what,
                          moved))

    m2v, pss = outputs(settings, sid)
    # The song's audio lives in the .pss alongside the video, so a re-mixed song
    # has to come back through here even though nothing about its background has
    # changed. Encoding the clip again is the expensive half and there is no need
    # for it when the clip that is already staged was made from the same things.
    want = {"clip": os.path.basename(venue), "kbps": settings.encode_kbps,
            "seconds": round(vid_secs, 2), "start": round(start, 3),
            "delay": round(delay + waited, 3), "shape": SHAPE,
            "screen": settings.screen}
    if own:
        # Named only where it applies, so a song behind a venue clip keeps the
        # stamp it already has and the clip it already has with it.
        want["whole"] = True
    note = ""
    if os.path.exists(m2v) and os.path.getsize(m2v) and encoded_from(m2v) == want:
        if log:
            log("video: keeping the clip already encoded for this song")
    else:
        share = still_share(settings, venue, start) if venue else 0.0
        steady = share >= STILL_SHARE
        if steady:
            note = " (repeats %d%% of its frames, so it is held steady)" % (
                share * 100)
            if log:
                log("video:%s" % note)
        r = encode_video(settings, venue, m2v, vid_secs, start, delay + waited,
                         steady, whole=own)
        if r.returncode != 0 or not os.path.exists(m2v) \
                or os.path.getsize(m2v) == 0:
            return False, "could not encode the background video: %s" % (
                (r.stderr or r.stdout).strip()[:160])
        with open(m2v + ".json", "w", encoding="utf-8") as fp:
            json.dump(want, fp, indent=1)
        if log:
            w, h, fps, br = (probe_video(settings, m2v) + ["?"] * 4)[:4]
            log("video: %sx%s @ %s, %s bit/s, %.2fs (%.1f MB)"
                % (w, h, fps, br, vid_secs, os.path.getsize(m2v) / 1048576.0))

    mux(settings, m2v, vgs, pss, rate)
    return True, ("%s%s, %d ch audio, %.1f MB pss"
                  % (what, note, info["channels"],
                     os.path.getsize(pss) / 1048576.0))
