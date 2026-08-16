"""The song list: songs_customs.dta, compiled to the DTB the game reads.

Metadata (difficulty ranks, genre, decade, banks) comes from the songs.dta that
Onyx produced for its Xbox 360 RB2 target. The audio channel layout comes from
layout.json, written when the VGS source audio was built - Onyx's own channel
map is deliberately ignored because our PS2 mix has a different layout.

The emitted entry follows the shape of the retail PS2 'afterlife' entry, which
uses an older, leaner schema than the 360 version: no format/song_id/game_origin
fields, but it does add venue_bank and preview_clip.

Output is compiled with `dtab -b` and encrypted with `dtab -e` (new-style, which
is what these PS2 DTBs actually use), then round-trip verified.
"""

import json
import os
import re

from . import ark, proc
from .errors import BuildError

VENUE_BANK = "world/big_club/big_club_bank.milo"
DEFAULT_BANK = "sfx/tambourine_bank.milo"
SCROLL_SPEED = 2300
CONTEXT = 2000

# Onyx fills vocal_gender in from nothing useful and lands on 'female' for
# everything, which picks the wrong singer model in the venue. Correct it by
# hand per song; anything unlisted falls back to male.
VOCAL_GENDER = {
    "thebeatlescantbuymelove": "male",
}

# A charted part whose source song.ini carried no difficulty rating arrives as
# rank 1, which would show a bogus one-dot tier in the song list.
MIN_RANK = 2

PARTS = ("drum", "guitar", "bass", "vocals")


def grab(text, key, default=None):
    """Pull a simple ('key' value) pair out of an Onyx-generated DTA."""
    m = re.search(r"\(\s*'?%s'?\s+([^()]+?)\s*\)" % re.escape(key), text)
    if not m:
        return default
    val = m.group(1).strip()
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]
    return val.strip("'")


def grab_rank(text, part, default=1):
    m = re.search(r"\(\s*'?rank'?(.*?)\n\s*\)", text, re.S)
    block = m.group(1) if m else text
    r = re.search(r"\(\s*'?%s'?\s+(-?\d+)\s*\)" % part, block)
    return int(r.group(1)) if r else default


def clean_year(raw):
    """song.ini years are free text, e.g. '1964 (July 10)' - keep the year."""
    m = re.search(r"(\d{4})", str(raw))
    return m.group(1) if m else "0"


def fmt_floats(vals):
    return " ".join("%.1f" % v for v in vals)


def fmt_ints(vals):
    return " ".join(str(int(v)) for v in vals)


def quote(s):
    return '"%s"' % str(s).replace('"', "'")


def build_entry(info, onyx_dta):
    sid = info["id"]
    base = "songs/%s/%s" % (sid, sid)

    title = info["title"] or grab(onyx_dta, "name", sid)
    artist = info["artist"] or grab(onyx_dta, "artist", "Unknown")
    album = info["album"] or grab(onyx_dta, "album_name", "")
    year = clean_year(info["year"] or grab(onyx_dta, "year_released", "0"))
    genre = grab(onyx_dta, "genre", "rock")
    decade = grab(onyx_dta, "decade", "the00s")
    gender = VOCAL_GENDER.get(sid, "male")
    tempo = grab(onyx_dta, "anim_tempo", "kTempoMedium")
    bank = grab(onyx_dta, "bank", DEFAULT_BANK)

    ranks = {p: grab_rank(onyx_dta, p) for p in PARTS + ("band",)}
    # A rank of 0 is how the game is told an instrument cannot be played at all,
    # and the parts with no channels are exactly the ones the chart has nothing
    # on: see library.charted_parts.
    offered = [p for p in PARTS if info["tracks"].get(p)]
    for part in PARTS:
        if part not in offered:
            ranks[part] = 0
    # Onyx leaves the band unrated when the source chart carried no band
    # difficulty, which no retail entry does. Rate it by what can actually be
    # played instead of showing a bogus bottom tier in the song list.
    if ranks["band"] < MIN_RANK:
        rated = [ranks[p] for p in offered if ranks[p] >= MIN_RANK]
        ranks["band"] = max(MIN_RANK,
                            int(round(sum(rated) / len(rated))) if rated else 0)
    # Same for an unrated part that is played, rated alongside the band rather
    # than left pinned at the bottom tier.
    for part in offered:
        if ranks[part] < MIN_RANK:
            ranks[part] = ranks["band"]

    # Ranks and channels have to name the same parts. No song the game ships
    # breaks that, and one that does crashes as it loads: the mixer is handed a
    # part with nowhere to read from, or the game is offered a part the chart
    # cannot supply. Both sides come from the same place now, so this only
    # catches a slip.
    trouble = ["%s would be listed to play with no audio channels behind it" % p
               for p in PARTS if ranks[p] >= MIN_RANK and p not in offered]
    trouble += ["%s has audio channels but would not be listed as playable" % p
                for p in offered if ranks[p] < MIN_RANK]
    if trouble:
        raise BuildError(
            "%s's song list entry and its audio disagree about what can be "
            "played, which crashes the game as the song loads: %s. Its mix has "
            "%d channels: %s." % (sid, "; ".join(trouble), info["channels"],
                                  json.dumps(info["tracks"])))

    # Emit tracks in a stable instrument order.
    track_lines = []
    for part in ("drum", "bass", "guitar", "vocals"):
        idxs = info["tracks"].get(part)
        if not idxs:
            continue
        if len(idxs) == 1:
            track_lines.append("     (%s %d)" % (part, idxs[0]))
        else:
            track_lines.append("     (%s\n      (%s))" % (part, fmt_ints(idxs)))

    pv_start, pv_end = info["preview_ms"]

    L = []
    L.append("(%s" % sid)
    L.append("   (name")
    L.append("    %s)" % quote(title))
    L.append("   (artist")
    L.append("    %s)" % quote(artist))
    L.append("   (master TRUE)")
    L.append("   (context %d)" % CONTEXT)
    L.append("   (song")
    L.append("    (name %s)" % base)
    L.append("    (tracks")
    L.append("     (" + "\n".join(track_lines).lstrip() + "))")
    L.append("    (pans")
    L.append("     (%s))" % fmt_floats(info["pans"]))
    L.append("    (vols")
    L.append("     (%s))" % fmt_floats(info["vols"]))
    L.append("    (cores")
    L.append("     (%s))" % fmt_ints(info["cores"]))
    L.append("    (drum_solo")
    L.append("     (seqs")
    L.append("      (kick.cue snare.cue tom1.cue tom2.cue crash.cue)))")
    L.append("    (drum_freestyle")
    L.append("     (seqs")
    L.append("      (kick.cue snare.cue hat.cue ride.cue crash.cue)))")
    L.append("    (midi_file %s.mid))" % base)
    L.append("   (song_scroll_speed %d)" % SCROLL_SPEED)
    L.append("   (bank %s)" % bank)
    # No drum_bank: Onyx emits one because RB2 on Xbox 360 uses it, but not one
    # of the 83 retail PS2 entries does, and it was the only key we emitted that
    # retail never uses.
    L.append("   (venue_bank %s)" % VENUE_BANK)
    L.append("   (anim_tempo %s)" % tempo)
    L.append("   (preview %d %d)" % (pv_start, pv_end))
    # The clip in the shared previews folder, not the copy in the song's own
    # folder: see ark.PREVIEW_DIR for why that is the one that plays.
    L.append("   (preview_clip songs/%s/prev_%s)"
             % (ark.PREVIEW_DIR, sid))
    L.append("   (rank")
    L.append("    (drum %d)" % ranks["drum"])
    L.append("    (guitar %d)" % ranks["guitar"])
    L.append("    (bass %d)" % ranks["bass"])
    L.append("    (vocals %d)" % ranks["vocals"])
    L.append("    (band %d))" % ranks["band"])
    L.append("   (genre %s)" % genre)
    L.append("   (album_art TRUE)")
    L.append("   (decade %s)" % decade)
    L.append("   (vocal_gender %s)" % gender)
    L.append("   (year_released %s)" % year)
    if album:
        L.append("   (album_name")
        L.append("    %s)" % quote(album))
    # PS2 hides any song it treats as downloadable content, since no DLC can be
    # installed. base_song marks the entry as living on the disc.
    L.append("   (base_song TRUE)")
    L.append(")")
    return "\n".join(L)


def outputs(settings):
    """The text song list, the compiled DTB and the encrypted one."""
    d = settings.dta_dir
    return (os.path.join(d, "songs_customs.dta"),
            os.path.join(d, "songs_customs.plain.dtb"),
            os.path.join(d, "songs_customs.dtb"))


def is_done(settings):
    return os.path.exists(outputs(settings)[2])


def write(settings, sids, log=None):
    """Write the song list describing the given songs. Returns its path.

    The ids come from the caller, and only songs that will actually ship belong
    in the list: an entry for a song whose files never reach the disc is worse
    than no entry at all, because the song list draws every cover as it scrolls
    and a missing texture crashes the list. ark.check() decides which songs are
    complete, so the two cannot drift.
    """
    if not sids:
        raise BuildError("There are no finished songs to put in the song list.")

    entries = []
    for sid in sids:
        d = os.path.join(settings.stage, sid)
        layout = os.path.join(d, "layout.json")
        if not os.path.exists(layout):
            raise BuildError(
                "%s has no layout.json, so its channel layout is unknown. "
                "Build its audio again." % sid)
        with open(layout, encoding="utf-8") as fp:
            info = json.load(fp)
        onyx_path = os.path.join(d, "onyx_songs.dta")
        onyx_dta = ""
        if os.path.exists(onyx_path):
            with open(onyx_path, encoding="utf-8", errors="replace") as fp:
                onyx_dta = fp.read()
        entries.append(build_entry(info, onyx_dta))
        if log:
            log("entry: %-26s %d channels" % (sid, info["channels"]))

    dta_text = "\n".join(entries) + "\n"
    dta_path = outputs(settings)[0]
    with open(dta_path, "w", encoding="latin-1", errors="replace") as fp:
        fp.write(dta_text)
    if log:
        log("wrote %s (%d bytes, %d entries)"
            % (dta_path, len(dta_text), len(entries)))
    return dta_path


def top_level_entries(text):
    """(start, end, name) for every outermost bracketed entry in a DTA.

    Quoted strings and ; comments can both hold stray brackets, so neither is
    counted towards the nesting.
    """
    spans = []
    depth, start, i, quoted = 0, None, 0, False
    while i < len(text):
        c = text[i]
        if quoted:
            quoted = c != '"'
        elif c == '"':
            quoted = True
        elif c == ";":
            nl = text.find("\n", i)
            i = len(text) if nl < 0 else nl
            continue
        elif c == "(":
            if depth == 0:
                start = i
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0 and start is not None:
                head = re.match(r"\(\s*'?([^\s()']+)", text[start:i + 1])
                spans.append((start, i + 1, head.group(1) if head else ""))
                start = None
        i += 1
    return spans


def without_entries(text, names):
    """The DTA text with the named entries taken out. Returns (text, removed)."""
    names = set(names)
    kept, removed, last = [], [], 0
    for start, end, name in top_level_entries(text):
        if name not in names:
            continue
        kept.append(text[last:start])
        last = end
        removed.append(name)
        while last < len(text) and text[last] in "\r\n":
            last += 1
    kept.append(text[last:])
    return "".join(kept), removed


def strip_from_dtb(settings, dtb_path, names, log=None):
    """Take songs out of an already-compiled list, in place.

    For the base game's own songs.dtb: a song whose files have been removed has
    to lose its entry as well, or it still shows in the setlist and picking it
    leaves the game loading forever.
    """
    dtab = settings.tool("dtab")
    work = settings.work_dir("dtb_edit")
    os.makedirs(work, exist_ok=True)
    plain = os.path.join(work, "in.dtb")
    text_path = os.path.join(work, "in.dta")
    out_plain = os.path.join(work, "out.dtb")

    for step, args in (("decrypt", ["-d", dtb_path, plain]),
                       ("decompile", ["-a", plain, text_path])):
        # Only this step's own output, or the decompile would delete the file it
        # is about to read.
        if os.path.exists(args[-1]):
            os.remove(args[-1])
        r = proc.run([dtab] + args, capture_output=True, text=True)
        if not os.path.exists(args[-1]):
            raise BuildError("Could not %s %s: %s"
                             % (step, os.path.basename(dtb_path),
                                (r.stderr or r.stdout).strip()[:200]))

    with open(text_path, encoding="latin-1", errors="replace") as fp:
        text = fp.read()
    edited, removed = without_entries(text, names)
    if not removed:
        return []
    with open(text_path, "w", encoding="latin-1", errors="replace") as fp:
        fp.write(edited)

    if os.path.exists(out_plain):
        os.remove(out_plain)
    r = proc.run([dtab, "-b", text_path, out_plain], capture_output=True,
                 text=True)
    if not os.path.exists(out_plain):
        raise BuildError("The edited song list would not compile: %s"
                         % (r.stderr or r.stdout).strip()[:200])
    r = proc.run([dtab, "-e", out_plain, dtb_path], capture_output=True,
                 text=True)

    # Read the result back: a list the game cannot parse is worse than the songs
    # it was meant to save room for.
    check_plain = os.path.join(work, "check.dtb")
    check_text = os.path.join(work, "check.dta")
    for stale in (check_plain, check_text):
        if os.path.exists(stale):
            os.remove(stale)
    proc.run([dtab, "-d", dtb_path, check_plain], capture_output=True, text=True)
    proc.run([dtab, "-a", check_plain, check_text], capture_output=True,
             text=True)
    if not os.path.exists(check_text) or not os.path.getsize(check_text):
        raise BuildError("The edited song list could not be read back, so it is "
                         "not safe to put on a disc.")
    with open(check_text, encoding="latin-1", errors="replace") as fp:
        back = fp.read()
    left = [n for n in removed
            if re.search(r"\(\s*'?%s'?[\s)]" % re.escape(n), back)]
    if left:
        raise BuildError("These songs would not come out of the list: %s"
                         % ", ".join(left))
    before = len(top_level_entries(text))
    after = len(top_level_entries(back))
    if before - after != len(removed):
        raise BuildError("Editing the song list changed %d entries instead of "
                         "%d, so it is not safe to ship."
                         % (before - after, len(removed)))
    if log:
        log("song list: removed %s, %d entries left"
            % (", ".join(removed), after))
    return removed


def compile_dta(settings, sids, log=None):
    """Compile and encrypt the song list. Returns the encrypted DTB's path."""
    dtab = settings.tool("dtab")
    dta_path, plain, enc = outputs(settings)
    if not os.path.exists(dta_path):
        raise BuildError("The song list has not been written yet.")

    r = proc.run([dtab, "-b", dta_path, plain], capture_output=True,
                 text=True)
    if not os.path.exists(plain):
        raise BuildError("The song list would not compile: %s"
                         % (r.stderr or r.stdout))
    r = proc.run([dtab, "-e", plain, enc], capture_output=True, text=True)
    if not os.path.exists(enc):
        raise BuildError("The song list would not encrypt: %s"
                         % (r.stderr or r.stdout))
    if log:
        log("compiled -> %s (%d bytes, encrypted)" % (enc, os.path.getsize(enc)))

    # Round-trip: decrypt and decompile, then compare entry ids and channel counts.
    back_dtb = os.path.join(settings.dta_dir, "_verify.dtb")
    back_dta = os.path.join(settings.dta_dir, "_verify.dta")
    proc.run([dtab, "-d", enc, back_dtb], capture_output=True, text=True)
    proc.run([dtab, "-a", back_dtb, back_dta], capture_output=True,
             text=True)
    if not os.path.exists(back_dta) or os.path.getsize(back_dta) == 0:
        raise BuildError("The compiled song list could not be read back, so it "
                         "is not safe to put on a disc.")

    with open(back_dta, encoding="latin-1", errors="replace") as fp:
        rt = fp.read()
    missing = [s for s in sids if not re.search(r"\(\s*'?%s'?[\s)]" % s, rt)]
    if missing:
        raise BuildError("The compiled song list lost these songs: %s. The disc "
                         "would not show them." % ", ".join(missing))
    if log:
        log("round trip OK: all %d entries survive encrypt -> decrypt -> "
            "decompile" % len(sids))
    return enc
