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
import os
import random
import shutil
import struct
import subprocess

from . import proc
from .errors import BuildError
from .library import read_ini
from .settings import VIDEO_EXT, videos_in

# Retail video: 400x304 MPEG-2, 29.97 fps, constant bit rate. Retail uses
# 2000 kbit/s; 1500 is the tutorial's recommendation and buys ~8 MB of disc,
# which matters because every megabyte counts against the size limit below
# which the console treats the image as a CD rather than a DVD. The rate itself
# comes from the settings, which default to 1500.
WIDTH, HEIGHT = 400, 304
FPS = "30000/1001"
# Retail sequence headers declare a 40-unit VBV buffer (units are 16384 bits).
# ffmpeg's own default is far larger, and the PS2's video decoder has only a
# small buffer to fill, so state retail's figure explicitly.
VBV_BITS = 40 * 16384
# The console starts the chart 3s into the stream, so audio and video both open
# with 3s of nothing. Some songs' audio opens with more than that, and the video
# is sized from the finished audio below, so it stays the shorter of the two.
LEAD_IN = 3.0
# "The audio needs to be longer than the video, otherwise the song will freeze
# when you finish it" - so the video stops short of the audio.
TAIL_SLACK = 2.0

SAMPLES_PER_BLOCK = 28
ADPCM_BLOCK = 16

# A song folder can carry its own video, which Clone Hero plays behind that song
# and so do we, in place of a venue clip. Clone Hero names it video.<ext>; some
# charts use background.<ext> for the same thing, next to the still image of that
# name. Every format Clone Hero accepts for an animated background is here -
# .mp4, .avi, .webm, .ogv, .mpeg - plus a few near neighbours of those that
# ffmpeg reads just as happily, since nothing here has to run on Clone Hero's
# players. Its animated highways are .webm too, but the highway is not a
# background: Rock Band 2 draws its own, so those are left alone.
SONG_VIDEO_NAMES = ("video", "background")
SONG_VIDEO_EXTS = VIDEO_EXT + (".ogv",)


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
    for name in sorted(os.listdir(source_dir)):
        stem, ext = os.path.splitext(name)
        if stem.lower() in SONG_VIDEO_NAMES and ext.lower() in SONG_VIDEO_EXTS:
            return os.path.join(source_dir, name)
    return ""


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


def video_offset(source_dir):
    """(seconds into the video to start, seconds of black to add first).

    song.ini's video_start_time is where in the video Clone Hero starts playing
    when the song starts. A negative value holds the video back instead, which
    here means that much black in front of it.
    """
    ini = os.path.join(source_dir or "", "song.ini")
    if not os.path.exists(ini):
        return 0.0, 0.0
    try:
        raw = float(read_ini(ini).get("video_start_time", 0) or 0) / 1000.0
    except ValueError:
        return 0.0, 0.0
    return (raw, 0.0) if raw > 0 else (0.0, abs(raw))


def encode_video(settings, src, dst, seconds, start=0.0, delay=0.0):
    """Encode one clip to the retail MPEG-2 shape, looping to length.

    start skips that far into the source and delay puts that much extra black in
    front of it, which is how a song's own video is lined up with its audio. An
    empty src means a black background, generated here rather than read from a
    file: there is nothing to loop, scale or line up, so the whole stream is
    black and the bitrate can be a fraction of a real clip's.
    """
    kbps = settings.encode_kbps
    cmd = [settings.tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
    if src:
        vf = ("scale=%d:%d:force_original_aspect_ratio=increase,"
              "crop=%d:%d,fps=%s,tpad=start_duration=%s:start_mode=add:color=black"
              % (WIDTH, HEIGHT, WIDTH, HEIGHT, FPS, LEAD_IN + delay))
        cmd += ["-stream_loop", "-1"]
        if start:
            cmd += ["-ss", "%.3f" % start]
        cmd += ["-i", src, "-vf", vf]
    else:
        cmd += ["-f", "lavfi", "-i", "color=c=black:s=%dx%d:r=%s"
                % (WIDTH, HEIGHT, FPS)]
    cmd += ["-an", "-t", "%.3f" % seconds,
            "-c:v", "mpeg2video",
            "-b:v", "%dk" % kbps,
            "-minrate", "%dk" % kbps,
            "-maxrate", "%dk" % kbps,
            "-bufsize", "%d" % VBV_BITS,
            "-pix_fmt", "yuv420p", dst]
    return proc.run(cmd, capture_output=True, text=True)


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
    start, delay = video_offset(source_dir) if own else (0.0, 0.0)
    what = "black" if not venue else os.path.basename(venue)

    if log:
        log("audio: %d ch @ %d Hz, %.2fs (VGS v%d) -> %d bytes/sec"
            % (info["channels"], info["rate"], info["seconds"],
               info["version"], rate))
        log("%s: %s%s" % ("the song's own video" if own else "background", what,
                          (", from %.2fs in" % start) if start else
                          (", %.2fs of black first" % delay) if delay else ""))

    m2v, pss = outputs(settings, sid)
    r = encode_video(settings, venue, m2v, vid_secs, start, delay)
    if r.returncode != 0 or not os.path.exists(m2v) or os.path.getsize(m2v) == 0:
        return False, "could not encode the background video: %s" % (
            (r.stderr or r.stdout).strip()[:160])
    if log:
        w, h, fps, br = (probe_video(settings, m2v) + ["?"] * 4)[:4]
        log("video: %sx%s @ %s, %s bit/s, %.2fs (%.1f MB)"
            % (w, h, fps, br, vid_secs, os.path.getsize(m2v) / 1048576.0))

    mux(settings, m2v, vgs, pss, rate)
    return True, ("%s, %d ch audio, %.1f MB pss"
                  % (what, info["channels"],
                     os.path.getsize(pss) / 1048576.0))
