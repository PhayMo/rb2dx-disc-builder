"""End-to-end verification of the built ISO.

Two stages:
  1. Every file inside the ISO is hashed and compared to the source it came
     from, proving the image was written intact.
  2. The packed ark is unpacked again and the injected song's files are
     compared byte-for-byte against the staged originals, proving the song
     survived the dir2ark round trip.
"""

import hashlib
import os
import shutil

import pycdlib

from . import proc
from .errors import BuildError


def sha(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        while True:
            b = fp.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha_stream(fp, length, chunk=1 << 20):
    h = hashlib.sha256()
    left = length
    while left:
        b = fp.read(min(chunk, left))
        if not b:
            break
        left -= len(b)
        h.update(b)
    return h.hexdigest()


def find_source(settings, name):
    for base in (settings.ark_out, settings.base_game):
        for dirpath, _, files in os.walk(base):
            if name in files:
                return os.path.join(dirpath, name)
    return None


def verify_iso(settings, say):
    say("=== stage 1: ISO contents vs sources ===")
    iso = pycdlib.PyCdlib()
    iso.open(settings.out_iso)
    ok = bad = 0
    for dirpath, _, files in iso.walk(iso_path="/"):
        for f in files:
            iso_path = (dirpath.rstrip("/") + "/" + f)
            name = f.split(";")[0]
            src = find_source(settings, name)
            if not src:
                say("  ?? %-28s no source found" % name)
                bad += 1
                continue
            size = os.path.getsize(src)
            with iso.open_file_from_iso(iso_path=iso_path) as fp:
                got = sha_stream(fp, size)
            want = sha(src)
            if got == want:
                say("  OK %-28s %12d bytes" % (name, size))
                ok += 1
            else:
                say("  !! %-28s HASH MISMATCH" % name)
                bad += 1
    iso.close()
    say("  %d matched, %d failed" % (ok, bad))
    return bad == 0


def verify_song(settings, sid, check_dir, say):
    """Compare one song's staged files against what came back out of the ark."""
    stage = os.path.join(settings.stage, sid)
    unpacked = os.path.join(check_dir, "songs", sid)
    if not os.path.isdir(unpacked):
        say("  !! songs/%s is missing from the packed ark" % sid)
        return 0, 1

    # Only the files we deliberately inject; out.vgs and the json/dta helpers
    # are build scratch and are not meant to ship. The .m2v is scratch too: it
    # gets muxed into the .pss, which is what the game reads, as does the
    # multichannel main .vgs - retail keeps the main mix only inside the .pss.
    # The _onyx.mid is Onyx's chart before midfix.py fixes it up, kept for
    # diffing; the conformed <sid>.mid is what ships.
    skip = {"out.vgs", "onyx_songs.dta", "%s.vgs" % sid, "%s_onyx.mid" % sid}
    ok = bad = 0
    for dirpath, _, files in os.walk(stage):
        for f in sorted(files):
            # The .json files are notes to the build about what it made and from
            # what, so that a rebuild can tell what still stands: layout.json,
            # and one beside each of the clip and the encoded mix.
            if f in skip or f.endswith((".ogg", ".m2v", ".json")):
                continue
            src = os.path.join(dirpath, f)
            rel = os.path.relpath(src, stage)
            dst = os.path.join(unpacked, rel)
            if not os.path.exists(dst):
                say("  !! %-46s missing in ark" % rel)
                bad += 1
                continue
            if sha(src) == sha(dst):
                say("  OK %-46s %12d bytes" % (rel, os.path.getsize(src)))
                ok += 1
            else:
                say("  !! %-46s CONTENT DIFFERS" % rel)
                bad += 1
    return ok, bad


def verify_ark(settings, sids, say):
    # Every song the build ships gets checked, not just one: a song whose files
    # never reached the ark is what made the song list crash before, and the DTA
    # describes all of them.
    say("=== stage 2: ark round trip for %d song(s) ===" % len(sids))
    check_dir = settings.work_dir("verify_ark")
    shutil.rmtree(check_dir, ignore_errors=True)
    hdr = os.path.join(settings.ark_out, "MAIN.HDR")
    if not os.path.exists(hdr):
        raise BuildError("There is no packed archive at %s to check." % hdr)
    r = proc.run([settings.tool("arkhelper"), "ark2dir", hdr,
                  check_dir, "-l", "error"],
                 capture_output=True, text=True)
    if r.returncode != 0:
        say("  unpack failed: %s" % (r.stderr or r.stdout).strip())
        return False

    ok = bad = 0
    for sid in sids:
        say("  %s" % sid)
        song_ok, song_bad = verify_song(settings, sid, check_dir, say)
        ok += song_ok
        bad += song_bad

    dtb = os.path.join(check_dir, "songs", "gen", "songs_customs.dtb")
    want = os.path.join(settings.dta_dir, "songs_customs.dtb")
    if os.path.exists(dtb) and os.path.exists(want) and sha(dtb) == sha(want):
        say("  OK %-46s %12d bytes" % ("songs/gen/songs_customs.dtb",
                                       os.path.getsize(dtb)))
        ok += 1
    else:
        say("  !! songs/gen/songs_customs.dtb missing or differs")
        bad += 1

    say("  %d matched, %d failed" % (ok, bad))
    return bad == 0


def run(settings, sids, log=None):
    """Check the finished disc against what was staged.

    Returns (ok, report), where report is the lines of the check as the user
    should see them.
    """
    if not settings.out_iso or not os.path.exists(settings.out_iso):
        raise BuildError("There is no disc image at %s to check yet."
                         % (settings.out_iso or "the chosen output path"))

    report = []

    def say(line):
        report.append(line)
        if log:
            log(line)

    a = verify_iso(settings, say)
    b = verify_ark(settings, sids, say)
    say("ALL CHECKS PASSED" if a and b else "THERE ARE FAILURES ABOVE")
    return (a and b), report
