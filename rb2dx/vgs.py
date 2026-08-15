"""Encode a staged song's audio into Harmonix VGS files, with no GUI step.

Onyx exposes VGS conversion only in its GUI - the command line build has no VGS
command, and its only VGS writer is buried in the GH1/GH2 PS2 targets, which fix
the channel layout and so cannot produce a 7- or 9-channel Rock Band mix. That
left one hand-driven step in the middle of an otherwise scripted pipeline, which
is why only one song of six ever got converted.

The format is undemanding: a 0x80 header, then 16-byte PS-ADPCM blocks
interleaved round-robin across channels, 28 samples each. Byte 0 of a block is
the filter in the high nibble and the shift in the low nibble, byte 1 is the
channel index, and the remaining 14 bytes hold the sample nibbles low-first.
All of that was read back from Onyx's own VGS output, and this encoder was
measured against what Onyx achieves on the same mix.

Blocks are encoded independently, each seeded with the source's own preceding
two samples rather than the previous block's reconstruction. That keeps every
block computable at once instead of chasing a 3-million-step dependency chain
through Python, at the cost of a small prediction error where blocks meet.

Each song gets two files: <id>.vgs, the multichannel mix that feeds the .pss
mux, and prev_<id>.vgs for the song list. Channel counts come from layout.json
rather than being typed in. Getting that bookkeeping wrong by hand is what
shipped a mix one channel wider than any retail song and hung every load, so the
arithmetic lives here now.
"""

import json
import os
import struct

import numpy as np

from . import proc

HEADER = 0x80
BLOCK_BYTES = 16
BLOCK_SAMPLES = 28
RATE = 22050
# Onyx writes 2, meaning a plaintext payload; retail's 3/4 are encrypted. Keep
# that label: relabelling ours as 4 made the game try to decrypt plaintext and
# play silence, while version 2 plays fine.
VERSION = 2
PREVIEW_SECONDS = 30.0
PREVIEW_CHANNELS = 2

# The five PS-ADPCM predictors, as sixty-fourths.
FILTERS = np.array([[0, 0], [60, 0], [115, -52], [98, -55], [122, -60]],
                   dtype=np.int64)
MAX_SHIFT = 12
NIBBLE_MAX = 7
# Trying all five filters against every sample of a whole song at once wants a
# gigabyte of residuals, so the work is done a chunk of blocks at a time. Blocks
# are independent here, so chunking changes nothing about the output.
CHUNK_BLOCKS = 4096


def decode_pcm(settings, path, rate=RATE, seconds=None):
    """Decode any audio file to int64 samples, shaped (channels, samples)."""
    cmd = [settings.tool("ffmpeg"), "-v", "error", "-i", path]
    if seconds:
        cmd += ["-t", "%f" % seconds]
    cmd += ["-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(rate), "-"]
    probe = proc.run(
        [settings.tool("ffprobe"), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=channels", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    nch = int(probe.stdout.strip())
    raw = proc.run(cmd, capture_output=True).stdout
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.int64)
    return pcm.reshape(-1, nch).T.copy(), nch


def choose_filters(xb, prev1, prev2):
    """Pick a filter and shift per block from the source signal.

    Both are chosen against the original samples: the residual a filter leaves
    decides which one to use, and the largest residual in a block decides how
    fine a step the 4-bit samples can afford.
    """
    # Predict every sample under all five filters at once.
    hist1 = np.concatenate([prev1[:, :, None], xb[:, :, :-1]], axis=2)
    hist2 = np.concatenate([prev2[:, :, None], prev1[:, :, None],
                            xb[:, :, :-2]], axis=2)
    f0 = FILTERS[:, 0][:, None, None, None]
    f1 = FILTERS[:, 1][:, None, None, None]
    resid = xb[None] - ((f0 * hist1[None] + f1 * hist2[None]) >> 6)

    energy = (resid.astype(np.float64) ** 2).sum(axis=3)
    best = energy.argmin(axis=0)

    peak = np.abs(resid).max(axis=3)
    peak = np.take_along_axis(peak, best[None], axis=0)[0]
    # A block's step is 2**(12-shift), and a nibble spans +-7 steps, so the
    # step must reach ceil(peak/7). Silence keeps the finest step.
    need = -(-peak // NIBBLE_MAX)
    # bits = ceil(log2(need)), done by bit length so exact powers of two do not
    # ride on floating point rounding and cost a step of precision.
    bits = np.zeros_like(need)
    # A silent block needs no range at all; keep rest non-negative or the shift
    # below turns -1 into -1 forever.
    rest = np.maximum(need - 1, 0)
    while rest.any():
        bits += rest > 0
        rest >>= 1
    shift = np.clip(MAX_SHIFT - bits, 0, MAX_SHIFT)
    return best, shift


def encode_blocks(xb, prev1, prev2):
    """Encode one chunk of blocks, shaped (channels, blocks, 28)."""
    nch, nblocks = xb.shape[0], xb.shape[1]
    filt, shift = choose_filters(xb, prev1, prev2)
    f0 = FILTERS[filt, 0]
    f1 = FILTERS[filt, 1]
    down = MAX_SHIFT - shift
    half = np.left_shift(1, down) >> 1

    nib = np.zeros((nch, nblocks, BLOCK_SAMPLES), np.int64)
    h1, h2 = prev1.copy(), prev2.copy()
    for i in range(BLOCK_SAMPLES):
        pred = (f0 * h1 + f1 * h2) >> 6
        diff = xb[:, :, i] - pred
        code = np.clip((diff + half) >> down, -NIBBLE_MAX - 1, NIBBLE_MAX)
        recon = np.clip(pred + (code << down), -32768, 32767)
        nib[:, :, i] = code
        h2, h1 = h1, recon

    out = np.zeros((nch, nblocks, BLOCK_BYTES), np.uint8)
    out[:, :, 0] = ((filt << 4) | shift).astype(np.uint8)
    out[:, :, 1] = np.arange(nch, dtype=np.uint8)[:, None]
    low = nib[:, :, 0::2] & 0xF
    high = nib[:, :, 1::2] & 0xF
    out[:, :, 2:] = ((high << 4) | low).astype(np.uint8)
    return out.transpose(1, 0, 2).tobytes(), h1, h2


def encode_channels(x):
    """Encode (channels, samples) into the interleaved VGS payload."""
    nch, n = x.shape
    nblocks = int(-(-n // BLOCK_SAMPLES))
    pad = nblocks * BLOCK_SAMPLES - n
    if pad:
        x = np.concatenate([x, np.zeros((nch, pad), np.int64)], axis=1)

    xb = x.reshape(nch, nblocks, BLOCK_SAMPLES)
    # Every block is seeded from the source's own preceding samples. Feeding the
    # previous block's reconstruction back instead - what a sequential encoder
    # sees - was tried and measured worse, 21.8 dB against 23.0 dB mean, because
    # re-anchoring to the true signal every 28 samples stops quantization error
    # from feeding forward.
    prev1 = np.zeros((nch, nblocks), np.int64)
    prev2 = np.zeros((nch, nblocks), np.int64)
    prev1[:, 1:] = xb[:, :-1, -1]
    prev2[:, 1:] = xb[:, :-1, -2]

    pieces = []
    for start in range(0, nblocks, CHUNK_BLOCKS):
        sl = slice(start, min(start + CHUNK_BLOCKS, nblocks))
        payload, _, _ = encode_blocks(xb[:, sl], prev1[:, sl], prev2[:, sl])
        pieces.append(payload)
    return b"".join(pieces), nblocks


def write_vgs(settings, src, dst, rate=RATE, seconds=None, lead_ms=0):
    x, nch = decode_pcm(settings, src, rate, seconds)
    if lead_ms:
        # Silence in front of the song, on top of what the mix already carries.
        # Added here rather than in the mix because how much is needed only
        # becomes known once the chart has been built: see charts.measure_pad.
        pad = int(round(lead_ms / 1000.0 * rate))
        x = np.concatenate([np.zeros((nch, pad), x.dtype), x], axis=1)
    payload, nblocks = encode_channels(x)

    head = bytearray(HEADER)
    head[0:4] = b"VgS!"
    struct.pack_into("<I", head, 4, VERSION)
    for c in range(nch):
        struct.pack_into("<II", head, 8 + c * 8, rate, nblocks)
    with open(dst, "wb") as fp:
        fp.write(bytes(head))
        fp.write(payload)
    return nch, nblocks, nblocks * BLOCK_SAMPLES / float(rate)


def outputs(settings, sid):
    d = os.path.join(settings.stage, sid)
    return (os.path.join(d, "%s.vgs" % sid),
            os.path.join(d, "prev_%s.vgs" % sid))


def is_done(settings, sid):
    return all(os.path.exists(p) for p in outputs(settings, sid))


def encode(settings, sid, log=None):
    """Encode one staged song's mix and preview. Returns (ok, message)."""
    d = os.path.join(settings.stage, sid)
    layout = os.path.join(d, "layout.json")
    if not os.path.exists(layout):
        return False, "no layout.json yet (mix this song's audio first)"
    with open(layout, encoding="utf-8") as fp:
        info = json.load(fp)
    want = info["channels"]
    lead_ms = info.get("extra_lead_ms")
    if lead_ms is None:
        return False, ("this song's chart has not been built yet, so how much "
                       "silence its audio needs is unknown")

    main_ogg = os.path.join(d, "%s.ogg" % sid)
    prev_ogg = os.path.join(d, "prev_%s.ogg" % sid)
    for path in (main_ogg, prev_ogg):
        if not os.path.exists(path):
            return False, ("no %s yet (mix this song's audio first)"
                           % os.path.basename(path))
    main_vgs, prev_vgs = outputs(settings, sid)

    if log:
        log("encoding the %d channel mix%s"
            % (want, "" if not lead_ms else
               " with %.3f s more silence in front of it" % (lead_ms / 1000.0)))
    nch, _, secs = write_vgs(settings, main_ogg, main_vgs, lead_ms=lead_ms)
    if nch != want:
        return False, ("main mix encoded %d channels but layout.json says %d"
                       % (nch, want))
    if log:
        log("encoding the preview")
    pch, _, psecs = write_vgs(settings, prev_ogg, prev_vgs,
                              seconds=PREVIEW_SECONDS)
    if pch != PREVIEW_CHANNELS:
        return False, ("preview encoded %d channels, expected %d"
                       % (pch, PREVIEW_CHANNELS))
    return True, ("%d ch main %.1f s, %d ch preview %.1f s"
                  % (nch, secs, pch, psecs))
