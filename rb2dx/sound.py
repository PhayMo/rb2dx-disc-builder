"""Play a stretch of a song against a video's own audio, out loud.

Lining a video up is done by ear, so the ear has to be given something to judge:
the clip in one channel and the song in the other, from wherever the video has been
put, over and over while it is dragged. That means playing on demand and starting
again the moment the offset changes, which rules out asking ffmpeg each time.

So both sides are read once into memory and every stretch after that is cut out with
arithmetic - loud enough to hear, wrapped where the clip wraps, silent where the disc
would be showing black - written out as a small wave file and handed to Windows to
play. Anything ffmpeg can open is decoded on the way in, so a clip's aac and a song's
oggs arrive as the same kind of thing.
"""

import os
import subprocess
import wave

import numpy as np

from . import proc

# Plenty for a mix nobody is mastering: half the rate of a CD, one channel of each
# side, which keeps a whole song in a few megabytes.
RATE = 22050
# How long a stretch is played. Long enough to take in a phrase, short enough that a
# loop comes round again while the last one is still in mind.
PLAY_SECS = 12.0
# Where each side is levelled to, and the most that is allowed to be turned up. A
# television rip and a set of stems are rarely within ten decibels of each other and
# the quieter one has to be audible, but lifting near-silence only brings up hiss.
AIM_DB = -20.0
MOST_DB = 18.0
# The ends are taken down over this long, so a stretch played round and round comes
# back to its beginning without a click where the two edges meet.
EASE_MS = 12.0

try:
    import winsound
except ImportError:                                   # not Windows
    winsound = None


def can_play():
    """Whether this machine can be asked to make a noise."""
    return winsound is not None


def read(settings, args, seconds):
    """Whatever ffmpeg is told to read, as one channel of samples at RATE."""
    cmd = [settings.tool("ffmpeg"), "-v", "error"] + list(args)
    cmd += ["-vn", "-t", "%.2f" % max(seconds, 0.1), "-ac", "1", "-ar", str(RATE),
            "-f", "s16le", "-acodec", "pcm_s16le", "-"]
    raw = proc.run(cmd, capture_output=True, stdin=subprocess.DEVNULL).stdout
    return np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0


def _lifted(part):
    """One side of the mix, brought to a level the other can be judged against."""
    if part is None or not len(part):
        return np.zeros(0, np.float32)
    heard = part[np.abs(part) > 0.0005]
    if not len(heard):
        return part
    now = 20 * np.log10(np.sqrt((heard * heard).mean()) or 1e-6)
    by = min(AIM_DB - now, MOST_DB)
    return part * (10 ** (by / 20.0))


def _cut(source, at, length, wrap=0.0):
    """`length` samples of `source` from second `at`, silent where there is none.

    A clip the disc plays round and round is read round and round to match, so what
    comes out is what will be heard over that stretch rather than what the file
    happens to hold there.
    """
    out = np.zeros(length, np.float32)
    if source is None or not len(source):
        return out
    first = int(round(at * RATE))
    if wrap:
        wide = max(int(round(wrap * RATE)), 1)
        where = (first + np.arange(length)) % wide
        inside = where < len(source)
        out[inside] = source[where[inside]]
        return out
    if first < 0:
        keep = min(length + first, len(source))
        if keep > 0:
            out[-first:-first + keep] = source[:keep]
        return out
    keep = max(min(len(source) - first, length), 0)
    if keep:
        out[:keep] = source[first:first + keep]
    return out


def pair(song, clip, song_at, clip_at, seconds=PLAY_SECS, wrap=0.0, hush=0.0,
         want_song=True, want_clip=True):
    """The two sides side by side, as (left, right) at a level worth hearing.

    `hush` is how long the clip has yet to start, over which its side stays quiet
    the way the screen stays black.
    """
    length = int(round(seconds * RATE))
    theirs = _lifted(_cut(song, song_at, length)) if want_song else \
        np.zeros(length, np.float32)
    mine = _lifted(_cut(clip, clip_at, length, wrap)) if want_clip else \
        np.zeros(length, np.float32)
    if hush > 0:
        quiet = min(int(round(hush * RATE)), length)
        mine = mine.copy()
        mine[:quiet] = 0.0
    # The clip on the left and the song on the right, which is how the rendered
    # preview puts them too, so what is heard here is heard there.
    return mine, theirs


def _eased(part):
    """The same, with the very ends taken down so a loop does not click."""
    edge = int(EASE_MS / 1000.0 * RATE)
    if len(part) < 2 * edge or not edge:
        return part
    ramp = np.linspace(0.0, 1.0, edge, dtype=np.float32)
    part = part.copy()
    part[:edge] *= ramp
    part[-edge:] *= ramp[::-1]
    return part


def save(path, left, right):
    """Write the two sides out as a wave file Windows will play."""
    left, right = _eased(left), _eased(right)
    both = np.empty(2 * len(left), np.float32)
    both[0::2] = left
    both[1::2] = right[:len(left)] if len(right) >= len(left) else \
        np.pad(right, (0, len(left) - len(right)))
    # Room left over the top so a loud pair of sides cannot come out as a crackle.
    peak = float(np.abs(both).max() or 1.0)
    if peak > 0.89:
        both *= 0.89 / peak
    with wave.open(path, "wb") as fp:
        fp.setnchannels(2)
        fp.setsampwidth(2)
        fp.setframerate(RATE)
        fp.writeframes((both * 32767).astype("<i2").tobytes())
    return path


class Player(object):
    """Plays one wave file at a time, and stops when told to.

    Two file names are used in turn: Windows holds the one it is playing, and
    starting the next stretch while the last is still open would fail on the copy.
    """

    def __init__(self, folder):
        self.folder = folder
        self.turn = 0
        self.playing = False

    def play(self, left, right, loop=True):
        if not can_play():
            return ""
        self.stop()
        self.turn = 1 - self.turn
        path = os.path.join(self.folder, "hear%d.wav" % self.turn)
        try:
            save(path, left, right)
        except OSError:
            return ""
        flags = winsound.SND_FILENAME | winsound.SND_ASYNC
        if loop:
            flags |= winsound.SND_LOOP
        try:
            winsound.PlaySound(path, flags)
        except RuntimeError:
            return ""
        self.playing = True
        return path

    def stop(self):
        self.playing = False
        if can_play():
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except RuntimeError:
                pass
