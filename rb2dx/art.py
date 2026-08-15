"""Album art, from a Clone Hero album.jpg to the PS2's texture format.

Each song needs two files in its gen/ subfolder, matching the retail layout:
  <id>_keep.png_ps2          256x256 PS2 texture
  <id>_nomip_keep.bmp_ps2    byte-identical copy under the alternate name
"""

import os
import shutil

from . import proc

SIZE = 256
# What a 256x256 8-bit paletted PS2 texture weighs: header, 64 KB of indices and
# a 1 KB palette. Retail art matches this exactly, and a truecolour one comes out
# at 196,640 instead, so the size alone tells the two apart.
PALETTED_BYTES = 66592

ART_NAMES = ("album.png", "album.jpg", "album.jpeg", "cover.png", "cover.jpg")


def find_art(source_dir):
    for name in ART_NAMES:
        path = os.path.join(source_dir, name)
        if os.path.exists(path):
            return path
    return None


def outputs(settings, sid):
    gen = os.path.join(settings.stage, sid, "gen")
    return (os.path.join(gen, "%s_keep.png_ps2" % sid),
            os.path.join(gen, "%s_nomip_keep.bmp_ps2" % sid))


def is_done(settings, sid):
    keep, nomip = outputs(settings, sid)
    return (os.path.exists(keep) and os.path.exists(nomip)
            and os.path.getsize(keep) == PALETTED_BYTES)


def build(settings, sid, source_dir):
    """Convert one song's cover. Returns (ok, message)."""
    art = find_art(source_dir)
    if not art:
        return False, "the song folder has no album art"

    song_dir = os.path.join(settings.stage, sid)
    gen = os.path.join(song_dir, "gen")
    os.makedirs(gen, exist_ok=True)
    keep, nomip = outputs(settings, sid)

    # Square off the art without distorting it: scale to cover, then crop.
    # Retail art is an 8-bit paletted PS2 texture, so quantise to 256 colours
    # first - superfreq mirrors the input PNG's bit depth, and a truecolour
    # source would otherwise triple the file size.
    png = os.path.join(song_dir, "_art_%d.png" % SIZE)
    vf = ("scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,"
          "split[a][b];[b]palettegen=max_colors=256:stats_mode=single[p];"
          "[a][p]paletteuse=dither=sierra2_4a" % (SIZE, SIZE, SIZE, SIZE))
    r = proc.run([settings.tool("ffmpeg"), "-hide_banner", "-loglevel",
                  "error", "-y", "-i", art, "-filter_complex", vf, png],
                 capture_output=True, text=True)
    if not os.path.exists(png):
        return False, "could not read the cover image: %s" % (
            (r.stderr or r.stdout).strip()[:160])

    # png2tex leaves an existing target untouched and still exits 0, so a
    # rebuild would otherwise keep whatever was there - which once shipped
    # truecolour textures from before the art was quantised.
    for path in (keep, nomip):
        if os.path.exists(path):
            os.remove(path)
    r = proc.run([settings.tool("superfreq"), "png2tex", png, keep,
                  "-p", "ps2", "-l", "error"],
                 capture_output=True, text=True)
    if not os.path.exists(keep):
        os.remove(png)
        return False, "texture conversion failed: %s" % (
            (r.stderr or r.stdout).strip()[:160])

    size = os.path.getsize(keep)
    if size != PALETTED_BYTES:
        os.remove(png)
        return False, ("texture came out %d bytes, expected %d for a %dx%d "
                       "paletted image" % (size, PALETTED_BYTES, SIZE, SIZE))

    shutil.copyfile(keep, nomip)
    os.remove(png)
    return True, "%d bytes" % size
