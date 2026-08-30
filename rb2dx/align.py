"""Work out where a song's own video belongs, by listening to it.

A folder that brings a music video usually brings the song on that video's own
audio track, and where the picture belongs is wherever that track lines up with
the stems. Both sides are reduced to a curve of how loud they are and the two
curves compared at every offset at once; the offset that fits best is the answer.

A curve of where the hits are is used rather than the waveform because the two
recordings are rarely the same file - a different master, a different loudness,
mono against stereo, an .aac against an .ogg - and none of that moves the shape of
the song. What does move it is a different arrangement, so a live take against a
studio one finds nothing, and saying so is part of the job: see match().

A song also agrees with itself a bar or two out, being made of repeats, so no one
comparison settles it. The clip is cut into pieces and each piece asked separately
where in the whole song it sits; the wrong answers land somewhere different every
time and the right one does not.

Asking about the whole song rather than a window around the start matters for the
clips people actually have. A television opening is ninety seconds of a four minute
song and often not the first ninety: Naruto's opens a full half minute in, and Re:Re
opens a minute in. It also shows up the ones that cannot be lined up at all, because
they were cut from two or three parts of the song with the rest thrown away - the
pieces then agree among themselves in groups rather than all together, which is how
an edit is told from the wrong song, and only the opening stretch of one can ever be
put where it belongs.
"""

import os
import subprocess

import numpy as np

from . import proc
from .audio import stems_in
from .video import has_sound

# The curves are sampled this often, which is as finely as an answer can land.
# Five milliseconds is a fraction of a frame at the disc's thirty a second.
HOP_HZ = 200
RATE = 8000
# Waveforms to look at need far fewer points than matching does: fifty a second is
# finer than any screen shows and keeps a four minute song to a few thousand.
DRAW_HZ = 50
# Where the bottom of a drawn waveform sits, under its own loudest moment. Thirty
# decibels reads as silence while leaving the loud stretches of a modern master
# room to differ from each other, which a deeper floor flattens away.
DRAW_FLOOR_DB = 30.0
# How much of each side to listen to. Long enough for any song anyone charts, and
# the work is a couple of seconds of decoding either way.
LISTEN = 150.0
MOST = 600.0
# The pieces the clip is cut into, each this long, taken this often. Fifteen seconds
# holds enough of a song to be somewhere in particular, and five between them leaves
# most of the pieces of an edit clear of its cuts, a piece lying across one belonging
# to two places at once and fitting neither.
PIECE = 15.0
STRIDE = 5.0
# Answers this close together are the same answer.
AGREE_SECS = 0.15
# How well a piece has to fit before its answer counts for anything, as a coefficient
# where one is the same recording twice. This is a floor to keep the noise out and no
# more, because it is not what tells a match from nothing: a television rip of the
# right recording scores between 0.15 and 0.5 where unrelated music reaches 0.12, and
# the two would overlap on a rougher recording. What decides it is how many pieces
# agree - see match - so the floor is set where the noise stops and the counting does
# the rest. A clip with room for one piece and nothing to agree with is held to more.
PIECE_FIT = 0.14
SURE_ENOUGH = 0.15
ALONE_FIT = 0.30
# How many pieces have to agree before their answer is anybody's answer.
AGREE_LEAST = 3
# How much a piece has to be doing to be able to say where it is: how far it moves
# about its own average, against a whole clip's worth of movement being one, and how
# many of its points carry it. See _tells_apart.
LIVELY = 0.04
TELLING = 20.0
# What share of the pieces that found themselves have to agree for one offset to be
# the answer for the whole clip. Below this the clip is in pieces that each belong
# somewhere different, which is an edit rather than a recording.
MOSTLY = 0.5


def _pcm(settings, args, seconds=LISTEN):
    """Whatever ffmpeg is told to read, as mono samples."""
    cmd = [settings.tool("ffmpeg"), "-v", "error"] + list(args)
    cmd += ["-vn", "-t", "%.1f" % seconds, "-ac", "1", "-ar", str(RATE),
            "-f", "s16le", "-acodec", "pcm_s16le", "-"]
    raw = proc.run(cmd, capture_output=True, stdin=subprocess.DEVNULL).stdout
    return np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0


def _curve(settings, args, seconds=LISTEN):
    """A flattened loudness curve for whatever ffmpeg is told to read."""
    x = _pcm(settings, args, seconds)
    step = RATE // HOP_HZ
    if len(x) < step * HOP_HZ:
        return None
    n = len(x) // step * step
    env = np.abs(x[:n]).reshape(-1, step).mean(axis=1)
    # Loudness rather than amplitude, so a quiet verse counts for as much as a
    # loud chorus, and then how sharply it rises rather than how loud it is: a hit
    # is a hit whatever the master does with the level around it.
    env = np.log1p(env * 100)
    env = np.maximum(np.diff(env, prepend=env[:1]), 0.0)
    env -= env.mean()
    return env / (env.std() or 1.0)


def stems_args(source_dir):
    """What to tell ffmpeg to read a song's stems as one thing, or None."""
    stems = [p for name, p in sorted(stems_in(source_dir).items())
             if name != "crowd"]
    if not stems:
        return None
    args = []
    for path in stems:
        args += ["-i", path]
    if len(stems) > 1:
        args += ["-filter_complex", "%samix=inputs=%d:normalize=0"
                 % ("".join("[%d:a]" % i for i in range(len(stems))), len(stems))]
    else:
        args += ["-map", "0:a:0"]
    return args


def _stems_curve(settings, source_dir, seconds=LISTEN):
    """The song's stems, mixed the rough way, as one curve."""
    args = stems_args(source_dir)
    return _curve(settings, args, seconds) if args else None


def shape(settings, args, seconds, hz=DRAW_HZ):
    """How loud something is over time, as a line to draw.

    Drawn in decibels rather than straight amplitude, and against its own loudest
    moment. A modern master sits near the top of the scale almost throughout, and
    on a straight scale that is a solid block with no structure to see; in decibels
    the verses, choruses and breaks are all where anyone looking for them expects.
    """
    x = _pcm(settings, args, seconds)
    step = max(int(RATE / hz), 1)
    if len(x) < step:
        return None
    n = len(x) // step * step
    blocks = x[:n].reshape(-1, step)
    env = np.sqrt((blocks * blocks).mean(axis=1))
    db = 20 * np.log10(np.maximum(env, 1e-6))
    db -= db.max()
    return np.clip((db + DRAW_FLOOR_DB) / DRAW_FLOOR_DB, 0.0, 1.0)


def song_shape(settings, source_dir, seconds, hz=DRAW_HZ):
    """The song's stems as one line to draw, or None if it has no audio."""
    args = stems_args(source_dir)
    return shape(settings, args, seconds, hz) if args else None


def laid_out(song, width):
    """The song prepared to be asked about, once, for every piece of a clip.

    Padded in front by a piece's length, so a piece may hang off the start for a
    video whose music begins before the song's does, and carrying the running totals
    that every window's own average and spread are worked out from.
    """
    padded = np.concatenate([np.zeros(width, np.float32), song]).astype(np.float64)
    size = 1 << int(np.ceil(np.log2(len(padded) + width)))
    run = np.concatenate([[0.0], np.cumsum(padded)])
    power = np.concatenate([[0.0], np.cumsum(padded * padded)])
    return padded, np.fft.rfft(padded, size), size, run, power


def _wherever(laid, piece):
    """(where in the song this piece of clip sits, how well it fits), in seconds.

    The fit is how alike the two shapes are, from one for the same recording twice
    down to nothing, each window of the song measured against its own average and
    spread rather than the song's. Anything less says nothing: fifteen seconds of
    near-silence, or holding one drum hit, tots up against any song as well as a real
    match does, and only a proper coefficient refuses it.
    """
    padded, spread, size, run, power = laid
    width = len(piece)
    room = len(padded) - width + 1
    if room <= 0:
        return 0.0, 0.0
    own = piece.astype(np.float64) - piece.mean()
    corr = np.fft.irfft(spread * np.conj(np.fft.rfft(own, size)), size)[:room]
    sums = run[width:width + room] - run[:room]
    squares = power[width:width + room] - power[:room]
    # A window's spread about its own average. What the piece has already had taken
    # off it does not have to come off the song as well: the two averages multiply
    # out to nothing once one side sums to zero.
    spreads = np.maximum(squares - sums * sums / width, 1e-9)
    fits = corr / np.sqrt(spreads * (own @ own or 1e-9))
    # The first few offsets have the piece hanging off the front of the song, where
    # a window is mostly the padding and agrees with anything about as well as it
    # agrees with nothing. Only a window with a song in it gets a say.
    fits[spreads < LIVELY * width] = -1.0
    best = int(np.argmax(fits))
    return (best - width) / float(HOP_HZ), float(fits[best])


def _tells_apart(piece):
    """Whether a piece has enough going on in it to be found anywhere at all.

    Two ways it might not. Flat - a stretch of silence, or of noise at one level -
    has no shape to place and sits equally well anywhere in anything. One spike and
    nothing else has too much: it lines up with the loudest moment of any song at
    some offset or other and calls that a match. The count is of how many points
    really carry the piece, which is one for a spike and hundreds for a band.
    """
    own = piece.astype(np.float64) - piece.mean()
    squared = own * own
    total = squared.sum()
    if total <= 0 or total / len(piece) < LIVELY:
        return False
    return (total * total) / float((squared * squared).sum()) >= TELLING


def _grouped(found):
    """The pieces that gave the same answer, in groups, the biggest first.

    Each group is (offset, fit, [pieces]) with the offset averaged over its pieces
    and weighted by how well each fits, which lands nearer the truth than any one
    piece does.
    """
    groups = []
    for piece in found:
        near = [p for p in found if abs(p[1] - piece[1]) <= AGREE_SECS]
        weight = sum(p[2] for p in near)
        middle = sum(p[1] * p[2] for p in near) / (weight or 1.0)
        if any(abs(middle - was) <= AGREE_SECS for was, _, _ in groups):
            continue
        groups.append((middle, weight / len(near), near))
    groups.sort(key=lambda g: (len(g[2]), g[1]), reverse=True)
    return groups


def match(settings, source_dir, video_path):
    """Where this video belongs against this song's audio.

    Returns (seconds, fit, trouble, note). seconds is what the nudge should be:
    positive to start that far into the clip, negative to hold it back. trouble is
    empty when there is an answer worth using and says what is wrong when there is
    not. note carries a caveat that comes with an answer that is still worth using.
    """
    if not has_sound(settings, video_path):
        return 0.0, 0.0, "this video has no audio track to listen to", ""
    song = _stems_curve(settings, source_dir, MOST)
    if song is None:
        return 0.0, 0.0, "this song's audio could not be read", ""
    clip = _curve(settings, ["-i", video_path], MOST)
    if clip is None:
        return 0.0, 0.0, "the video's audio could not be read", ""

    width = int(PIECE * HOP_HZ)
    if len(clip) < width or len(song) <= width:
        return 0.0, 0.0, "there is too little audio here to line a video up by", ""
    # The song is laid out once and every piece of the clip asked against it.
    laid = laid_out(song, width)
    found = []
    for at in range(0, len(clip) - width + 1, int(STRIDE * HOP_HZ)):
        piece = clip[at:at + width]
        if not _tells_apart(piece):
            continue
        where, fit = _wherever(laid, piece)
        # What the whole clip's offset would be if this piece is where it belongs.
        found.append((at / float(HOP_HZ), at / float(HOP_HZ) - where, fit))
    if not found:
        return 0.0, 0.0, ("the video's audio is too quiet or too bare to line "
                          "anything up by"), ""

    nowhere = ("the video's audio is not this recording, so there is nothing to "
               "line up to")
    sure = [piece for piece in found if piece[2] >= PIECE_FIT]
    if not sure:
        best = max(found, key=lambda p: p[2])
        return best[1], best[2], nowhere, ""
    groups = _grouped(sure)
    secs, fit, agreeing = groups[0]
    # A clip too short to cut up has nothing to agree with it and has to carry the
    # answer on how well it fits alone.
    if len(found) < AGREE_LEAST:
        if fit < ALONE_FIT:
            return secs, fit, nowhere, ""
        return secs, fit, "", ("there are only a few seconds of audio to go on, so "
                               "check it before building")
    if len(agreeing) < AGREE_LEAST or fit < SURE_ENOUGH:
        return secs, fit, nowhere, ""
    if len(agreeing) >= MOSTLY * len(sure):
        return secs, fit, "", ""

    # Several parts of the clip, each agreeing with itself but not with the others:
    # the clip holds more than one stretch of the song with the rest cut out, so no
    # one offset lines all of it up. What plays first is what to serve.
    parts = [g for g in groups if len(g[2]) >= AGREE_LEAST]
    opening = min(sure, key=lambda p: p[0])
    mine = next((g for g in parts if opening in g[2]), None) or parts[0]
    if len(parts) < 2 or mine[1] < SURE_ENOUGH:
        return secs, fit, nowhere, ""
    return mine[0], mine[1], "", (
        "this video's audio is a shortened edit, cut from %d parts of the song, so "
        "only the part it opens with can line up" % len(parts))


def describe(seconds, fit):
    """One line about a match, for the log or the dialog.

    What a nudge then does about it depends on whether the clip loops, so that is
    left to whoever is doing it.
    """
    if not round(seconds, 2):
        return "the video is already where it should be (match %.2f)" % fit
    side = "before" if seconds > 0 else "after"
    return ("the video's music starts %.2f s %s the song's (match %.2f)"
            % (abs(seconds), side, fit))
