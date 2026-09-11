"""The look of the menus: your own logo and your own words under it.

Both logos are 512 by 128 textures inside their scenes - ui/splash/gen/splash.milo_ps2
and ui/main/gen/main.milo_ps2 - four bits a pixel. Four bits is the one depth superfreq
will not read, and the console stores those pixels in an order of its own, so the disc's
own logo cannot be turned back into a picture to draw on. The replacement is composed
from a picture instead - the Deluxe logo beside this file, or one the user chose - and
goes in at eight bits: that is what superfreq writes, what the album art path has always
used, and it costs about 33 KB of video memory more than the four-bit original. The one
field in the texture object saying how deep it is changes from 4 to 8 to match.

Those 512 by 128 pixels are stretched over a fixed shape on screen, so how big the logo
looks is how much of them it fills. Clear margins around the art are therefore cut off
before it is scaled, and the words below it are given no more rows than they need.

The words themselves are drawn in the display face beside this file, and measured before
they are drawn, so a long subtitle comes out smaller rather than running off the edge.

Both scenes are done, because the logo on the main menu is the same logo.

The colour of the highlight is a different kind of change, and is parked: the code below
rewrites ui/gen/color_presets.dtb, filling custom_colors - which every button on the main
menu names and the game ships empty - and default_colors with the chosen colour. It runs
only when menu_highlight is set, and nothing in the builder sets it, because no disc built
this way has yet come back with a menu that looks any different.

Everything happens in the copy of the game each build makes, after the songs are
injected and before the archive is packed, so the unpacked original is never touched.
"""

import os
import re
import shutil
import struct

from . import proc
from .errors import BuildError

# The logos, and the scenes holding them.
LOGOS = (("ui/splash/gen/splash.milo_ps2", "rb2_logo.tex", "title screen"),
         ("ui/main/gen/main.milo_ps2", "main_rblogo.tex", "main menu"))
WIDE, TALL = 512, 128

# Which Milo version these scenes are, which superfreq has to be told.
MILO = "25"

# The colour table, and the two presets in it that matter here. Every one of the 41
# buttons on the main menu names custom_colors, which the table ships empty - so that is
# the one to fill, and filling it is what a repaint of the menus amounts to. Its colours
# are taken from default_colors, which is the set the menus already look like: silver
# writing, that yellow behind the line you are on. default_colors is written too, in case
# a screen falls back to it.
#
# The fields: focus is where the cursor rests and selecting is the flash as it is
# confirmed, which are the highlight. normal and disabled are the writing, and selected
# is the same orange in every preset in the file, so those three are left as they are.
PRESETS = "ui/gen/color_presets.dtb"
CUSTOM = "custom_colors"
STOCK = "default_colors"
HIGHLIGHT = ("focus_color", "selecting_color")
# What the game ships that highlight as: 200,200,0, the yellow behind Quickplay.
STOCK_HIGHLIGHT = 51400

# Inside a texture object: its shape, how deep it is, and the length of the path the
# art was made from, after which the bitmap follows nine bytes later.
SHAPE_AT = 0x11
BPP_AT = 0x19
PATH_LEN_AT = 0x1D
GAP = 9

# The band at the bottom of the texture the words get, how much of the width they may
# fill, and the largest they are drawn. Every row given to the band is a row the logo
# cannot have, and the logo is the taller of the two by far, so the band is only as deep
# as the words need: enough for 34 points, which is where the face below sits under the
# logo without competing with it. A long subtitle gets whatever size fits instead. With no
# words at all there is no band, and the picture has the whole texture.
BAND = 42
MARGIN = 24
BIGGEST = 34
SMALLEST = 10
# Measured at this size first, then scaled to what fits: one drawing tells us the shape
# of the words at every size, since type scales evenly.
MEASURE_AT = 100
# Rows down from the middle of the band, once the words are centred in it. drawtext is
# told where the top of a line of type goes rather than where its ink starts, and the
# room a face leaves above the letters is its own business, so where they land is nudged
# rather than calculated. This is as low as they go: any further and a descender runs off
# the bottom of the texture, which is why the gap under the logo comes from the band's
# depth rather than from here.
DROP = 1
# How much alpha a pixel needs before it counts as part of the picture rather than the
# margin around it. Art saved with a soft edge fades to nothing over a pixel or two.
CLEAR = 8

# Room enough for a setlist's name without turning the logo into a caption.
LIMIT = 42

# The font travels beside this file, because Windows ships nothing in the Avant Garde
# line the Rock Band lettering comes from. The Windows ones behind it are only so a
# missing file still leaves something to draw with.
FONTS = (os.path.join(os.path.dirname(os.path.abspath(__file__)), "title",
                      "avantgarde-bold.ttf"),
         r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf",
         r"C:\Windows\Fonts\arial.ttf")

# The words are drawn in passes, back to front: each is a colour and how thick a line to
# put around it. The dark pass keeps them readable over whatever the wallpaper is doing
# behind them, and the grey is the one the game's own "Press START button" sits at - as
# bright as anything belongs under a logo drawn in outline. A line the same colour as its
# own pass would thicken the letters, and on the console that read heavier than the logo
# above them, so nothing here does.
STYLE = (("0x101010", 3), ("0x9AA0A6", 0))


def _say(log, text):
    if log:
        log(text)


def logo_art(settings=None):
    """The picture the replacement is composed from: the user's own, or the one shipped."""
    own = (getattr(settings, "title_art", "") or "").strip('"')
    if own and os.path.exists(own):
        return own
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "title",
                        "rb2dx_logo.png")


def tidy(text):
    """A subtitle as it will be drawn: one line, no runs of blanks, not too long."""
    return " ".join((text or "").split())[:LIMIT]


def _font():
    for path in FONTS:
        if os.path.exists(path):
            return path
    raise BuildError("No font to draw the title screen text with: %s is missing "
                     "and Windows has none of the fallbacks either." % FONTS[0])


def _work(settings, words):
    """The scratch folder, with the font and the words in it under plain names.

    ffmpeg splits a filter's options on ':', which every Windows path has after its
    drive letter, and how that is escaped differs by shell. Naming the files alone and
    running ffmpeg from the folder they are in avoids the question entirely.
    """
    work = settings.work_dir("menus")
    shutil.copyfile(_font(), os.path.join(work, "font.ttf"))
    with open(os.path.join(work, "words.txt"), "w", encoding="utf-8") as fp:
        fp.write(words)
    return work


def _ffmpeg(settings, work, args, capture=False):
    cmd = [settings.tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
    if capture:
        return proc.run(cmd + args, capture_output=True, cwd=work)
    return proc.run(cmd + args, capture_output=True, text=True, cwd=work)


def _ink_box(settings, art):
    """Where the picture sits inside its own file, as crop takes it.

    Clear margins around a logo are pixels the scale below would spend on nothing, and
    with the height as tight as it is those margins come straight off how big the logo
    lands. A picture with no transparency has no margins to find and comes back whole.
    """
    r = proc.run([settings.tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
                  "-show_entries", "stream=width,height", "-of", "csv=p=0", art],
                 capture_output=True, text=True)
    try:
        wide, tall = (int(x) for x in (r.stdout or "").strip().split(",")[:2])
    except ValueError:
        raise BuildError("Could not read %s as a picture. A PNG with a see-through "
                         "background is what belongs here." % art)
    r = proc.run([settings.tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
                  "-i", art, "-frames:v", "1", "-vf", "format=rgba,alphaextract",
                  "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True)
    alpha = r.stdout or b""
    if len(alpha) < wide * tall:
        raise BuildError("Could not read the see-through parts of %s, so it cannot be "
                         "drawn under the title." % art)
    lit = bytes(0 if v <= CLEAR else 1 for v in range(256))
    left, top, right, bottom = wide, tall, -1, -1
    for y in range(tall):
        row = alpha[y * wide:(y + 1) * wide].translate(lit)
        first = row.find(b"\x01")
        if first < 0:
            continue
        left = min(left, first)
        right = max(right, row.rfind(b"\x01"))
        top = min(top, y)
        bottom = max(bottom, y)
    if right < 0:
        raise BuildError("%s is see-through all over, so there is nothing to draw."
                         % art)
    return right - left + 1, bottom - top + 1, left, top


def _words_shape(settings, work, size):
    """How wide and tall the words come out at this size, in pixels of ink."""
    wide, tall = 2048, 256
    vf = ("drawtext=fontfile=font.ttf:textfile=words.txt:fontcolor=white:"
          "fontsize=%d:x=32:y=32" % size)
    r = _ffmpeg(settings, work,
                ["-f", "lavfi", "-i", "color=c=black:s=%dx%d:d=1" % (wide, tall),
                 "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-pix_fmt",
                 "gray", "-"], capture=True)
    grey = r.stdout or b""
    if len(grey) < wide * tall:
        raise BuildError("Could not measure the title screen text: %s"
                         % (r.stderr or b"").decode("utf-8", "replace")[:200])
    lit = bytes(0 if v <= 24 else 1 for v in range(256))
    left, top, right, bottom = wide, tall, -1, -1
    for y in range(tall):
        row = grey[y * wide:(y + 1) * wide].translate(lit)
        first = row.find(b"\x01")
        if first < 0:
            continue
        left = min(left, first)
        right = max(right, row.rfind(b"\x01"))
        top = min(top, y)
        bottom = max(bottom, y)
    if right < 0:
        return 0, 0
    return right - left + 1, bottom - top + 1


def _fits(settings, work, log=None):
    """The largest size those words fit the band at, and how tall they are then."""
    at_big = _words_shape(settings, work, MEASURE_AT)
    if not at_big[0]:
        raise BuildError("The title screen text drew nothing. Try plain letters "
                         "and numbers.")
    for_width = MEASURE_AT * (WIDE - MARGIN * 2) / float(at_big[0])
    for_height = MEASURE_AT * (BAND - 8) / float(at_big[1])
    size = max(SMALLEST, min(BIGGEST, int(for_width), int(for_height)))
    return size, _words_shape(settings, work, size)[1]


def _clear_to_black(bitmap):
    """The same texture with black in the palette entries that stand for clear.

    ffmpeg keeps one entry back for the clear background and writes it as pure green.
    No pixel ever shows that colour - but the console draws a texture by mixing
    neighbouring texels together, and it mixes their colour whether or not they are
    clear, so the green comes back as a fringe around every letter. The game's own two
    logos have black in those entries, which is what this puts there.
    """
    out = bytearray(bitmap)
    painted = 0
    for i in range(1 << bitmap[1]):
        at = 32 + i * 4
        if not out[at + 3] and any(out[at:at + 3]):
            out[at:at + 3] = b"\x00\x00\x00"
            painted += 1
    return bytes(out), painted


def _compose(settings, work, words, log=None):
    """The picture, with the words under it if there are any, as a texture the game takes."""
    art = logo_art(settings)
    room = TALL - BAND if words else TALL
    steps = ["format=rgba", "crop=%d:%d:%d:%d" % _ink_box(settings, art),
             "scale=%d:%d:force_original_aspect_ratio=decrease:flags=lanczos"
             % (WIDE - 8, room),
             "pad=%d:%d:(ow-iw)/2:0:color=0x00000000" % (WIDE, TALL)]
    size = 0
    if words:
        size, tall = _fits(settings, work, log=log)
        top = TALL - BAND + max(0, (BAND - tall) // 2) + DROP
        steps += ["drawtext=fontfile=font.ttf:textfile=words.txt:fontcolor=%s:"
                  "fontsize=%d:x=(w-text_w)/2:y=%d:borderw=%d:bordercolor=%s"
                  % (colour, size, top, thick, colour) for colour, thick in STYLE]
    chain = (
        "[0:v]" + ",".join(steps) + ","
        # The eight-bit path takes 256 colours and one of them has to be the clear
        # background, which is what reserve_transparent keeps back; the threshold
        # decides what counts as clear rather than dithering the logo's edge into it.
        "split[a][b];[b]palettegen=max_colors=256:reserve_transparent=1[p];"
        "[a][p]paletteuse=dither=none:alpha_threshold=128")
    flat = os.path.join(work, "logo.png")
    r = _ffmpeg(settings, work, ["-i", art, "-filter_complex", chain,
                                 "-frames:v", "1", flat])
    if not os.path.exists(flat):
        raise BuildError("Could not draw the title screen: %s"
                         % (r.stderr or r.stdout or "").strip()[:200])

    made = os.path.join(work, "logo.png_ps2")
    if os.path.exists(made):
        os.remove(made)
    r = proc.run([settings.tool("superfreq"), "png2tex", flat, made, "-p", "ps2",
                  "-l", "error"], capture_output=True, text=True)
    if not os.path.exists(made):
        raise BuildError("Could not turn the title screen logo into a texture: %s"
                         % (r.stderr or r.stdout).strip()[:200])
    with open(made, "rb") as fp:
        bitmap = fp.read()
    got_wide, got_tall = struct.unpack_from("<HH", bitmap, 7)
    if (got_wide, got_tall, bitmap[1]) != (WIDE, TALL, 8):
        raise BuildError("The title screen logo came out %dx%d at %d bits a pixel "
                         "rather than %dx%d at 8, so it is not safe to ship."
                         % (got_wide, got_tall, bitmap[1], WIDE, TALL))
    bitmap, painted = _clear_to_black(bitmap)
    drawn = ("\"%s\" drawn at %d points under it" % (words, size) if words
             else "no words under it")
    _say(log, "  %s, %s, %d bytes of texture, %d clear palette entr%s blacked out"
              % (os.path.basename(art), drawn, len(bitmap), painted,
                 "y" if painted == 1 else "ies"))
    return bitmap


def _swap(settings, work, rel, name, where, bitmap, log=None):
    """Put the composed logo into one scene, in the tree about to be packed."""
    superfreq = settings.tool("superfreq")
    live = os.path.join(settings.ark_build, *rel.split("/"))
    if not os.path.exists(live):
        raise BuildError("%s is not in the unpacked game, so the %s logo cannot be "
                         "changed. Is that folder a Deluxe PS2 release?"
                         % (rel, where))
    scene_dir = os.path.join(work, name.replace(".tex", ""))
    shutil.rmtree(scene_dir, ignore_errors=True)
    r = proc.run([superfreq, "milo2dir", live, scene_dir, "-m", MILO, "-l",
                  "error"], capture_output=True, text=True)
    inside = os.path.join(scene_dir, "Tex", name)
    if not os.path.exists(inside):
        raise BuildError("Could not open the %s scene: %s"
                         % (where, (r.stderr or r.stdout).strip()[:200]))

    with open(inside, "rb") as fp:
        was = fp.read()
    wide, tall, bpp = struct.unpack_from("<III", was, SHAPE_AT)
    if (wide, tall) != (WIDE, TALL):
        raise BuildError("This release's %s logo is %dx%d rather than %dx%d, so the "
                         "replacement would not fit." % (where, wide, tall, WIDE,
                                                         TALL))
    length, = struct.unpack_from("<I", was, PATH_LEN_AT)
    head = bytearray(was[:0x21 + length + GAP])
    struct.pack_into("<I", head, BPP_AT, bitmap[1])
    with open(inside, "wb") as fp:
        fp.write(bytes(head) + bitmap)

    made = os.path.join(work, os.path.basename(rel))
    if os.path.exists(made):
        os.remove(made)
    r = proc.run([superfreq, "dir2milo", scene_dir, made, "-m", MILO, "-l",
                  "error"], capture_output=True, text=True)
    if not os.path.exists(made):
        raise BuildError("Could not pack the %s scene again: %s"
                         % (where, (r.stderr or r.stdout).strip()[:200]))
    shutil.copyfile(made, live)
    _say(log, "  %s: %s was %d bits a pixel, now %d, and the scene packs again"
              % (where, name, bpp, bitmap[1]))


def relogo(settings, log=None):
    """Put the disc's own logo, words or picture or both, on both screens.

    A picture on its own is reason enough to redraw them: someone with their own art and
    nothing to say under it gets it whole, across the height the words would have taken.
    """
    words = tidy(settings.title_text)
    own = (getattr(settings, "title_art", "") or "").strip('"')
    if not words and not own:
        return
    art = logo_art(settings)
    if not os.path.exists(art):
        raise BuildError("There is no picture to draw the title screen from: %s" % art)
    _say(log, "drawing the logo on the title screen and the main menu:")
    work = _work(settings, words)
    bitmap = _compose(settings, work, words, log=log)
    for rel, name, where in LOGOS:
        _swap(settings, work, rel, name, where, bitmap, log=log)


# ---- the colour -----------------------------------------------------------


def number(colour):
    """#rrggbb as this table writes a colour, or None if it is not one."""
    text = (colour or "").strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        red, green, blue = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None
    return red + green * 256 + blue * 65536


def as_hex(value):
    """A colour out of the table, back as #rrggbb."""
    return "#%02x%02x%02x" % (value & 0xFF, (value >> 8) & 0xFF,
                              (value >> 16) & 0xFF)


def _block(text, name):
    """Where the node named name begins and ends, brackets counted rather than guessed."""
    at = text.find("(%s\n" % name)
    if at < 0:
        at = text.find("(%s " % name)
    if at < 0:
        return None
    depth = 0
    for i in range(at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if not depth:
                return at, i + 1
    return None


def recolour(settings, log=None):
    """Put the chosen colour behind the highlighted line in the menus."""
    wanted = number(settings.menu_highlight)
    if wanted is None:
        return
    dtab = settings.tool("dtab")
    live = os.path.join(settings.ark_build, *PRESETS.split("/"))
    if not os.path.exists(live):
        raise BuildError("%s is not in the unpacked game, so the menu colour cannot "
                         "be changed. Is that folder a Deluxe PS2 release?" % PRESETS)
    work = settings.work_dir("menus")

    def convert(step, args):
        if os.path.exists(args[-1]):
            os.remove(args[-1])
        r = proc.run([dtab] + args, capture_output=True, text=True)
        if not os.path.exists(args[-1]):
            raise BuildError("Could not %s the colour table: %s"
                             % (step, (r.stderr or r.stdout).strip()[:200]))

    plain = os.path.join(work, "presets.dtb")
    text_path = os.path.join(work, "presets.dta")
    convert("decrypt", ["-d", live, plain])
    convert("read", ["-a", plain, text_path])
    with open(text_path, encoding="latin-1", errors="replace") as fp:
        before = fp.read()

    # The set the menus already look like: a name, then its five colours one to a line.
    stock = _block(before, STOCK)
    if not stock:
        raise BuildError("The colour table has no %s, so the menu colour cannot be "
                         "changed." % STOCK)
    lines = re.findall(r"\([a-z_]+ -?\d+\)", before[stock[0]:stock[1]])
    if len(lines) < len(HIGHLIGHT):
        raise BuildError("%s is not laid out the way this expects, so the menu "
                         "colour cannot be changed." % STOCK)
    was = None
    body = []
    for line in lines:
        field, value = line.strip("()").split()
        if field in HIGHLIGHT:
            was = was if was is not None else int(value)
            value = wanted
        body.append(" (%s %s)" % (field, value))
    filled = "\n".join(body)

    after = before
    for name in (CUSTOM, STOCK):
        where = _block(after, name)
        if not where:
            raise BuildError("The colour table has no %s, so the menu colour cannot "
                             "be changed." % name)
        after = (after[:where[0]] + "(%s\n%s)" % (name, filled)
                 + after[where[1]:])

    edited = os.path.join(work, "presets edited.dta")
    with open(edited, "w", encoding="latin-1", errors="replace") as fp:
        fp.write(after)
    built = os.path.join(work, "presets built.dtb")
    ship = os.path.join(work, "presets.out")
    convert("compile", ["-b", edited, built])
    convert("encrypt", ["-e", built, ship])

    # Read it back the way the game will: a table it cannot parse takes the menus down.
    back_plain = os.path.join(work, "presets back.dtb")
    back_text = os.path.join(work, "presets back.dta")
    convert("check", ["-d", ship, back_plain])
    convert("check", ["-a", back_plain, back_text])
    with open(back_text, encoding="latin-1", errors="replace") as fp:
        again = fp.read()
    if again != after:
        raise BuildError("The edited colour table does not read back as it was "
                         "written, so it is not safe to put on a disc.")

    shutil.copyfile(ship, live)
    _say(log, "menu highlight: %s rather than %s, written into %s and %s, and the "
              "table reads back clean" % (as_hex(wanted), as_hex(was), CUSTOM, STOCK))


def dress(settings, log=None):
    """Everything about the menus this program changes, in the tree to be packed."""
    relogo(settings, log=log)
    recolour(settings, log=log)
