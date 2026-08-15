"""The game archive: inject the staged songs and repack it.

The base game's archive is unpacked once, then each build copies that tree, drops
every finished song into songs/<id>/, replaces the empty songs_customs.dtb with
the generated song list, and repacks with arkhelper using the same parameters the
Deluxe project uses.
"""

import json
import os
import shutil
import time

from . import charts, proc
from .errors import BuildError

SIZE_LIMIT = 4073741823  # matches the Deluxe project's dir2ark invocation

# Preview clips live here as well as in each song folder. arkhelper packs in
# directory order, so a leading digit puts them at the very start of the songs
# section instead of scattered through several gigabytes of .pss files.
PREVIEW_DIR = "0previews"

# What the Deluxe Custom Edition ships in its songs folder before anything is
# added. Afterlife is its one complete playable song, about 178 MB. The other
# three back '(placeholder)' entries in songs_placeholders.dtb, are absent from
# the songs.dtb the game reads, and carry .mogg audio this build cannot decode -
# roughly 86 MB that can never be heard.
DEMO_SONGS = ("afterlife", "entersandman", "paranoid", "runtothehills")

STAMP = ".unpacked.json"


def fresh_dir(path, tries=12):
    """Empty a directory and wait until Windows agrees it is gone.

    Deleting a directory tree on Windows is not always finished when rmtree
    returns - a virus scanner or Explorer holding a handle leaves the entry
    behind for a moment, and creating it again then fails.
    """
    for _ in range(tries):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(0.5)
    raise BuildError("Could not clear %s. Something else has files in there "
                     "open - close any Explorer window on it and try again."
                     % path)


def drop_demo_songs(settings, ark_build, log=None):
    """Take the base game's own songs out of the tree about to be packed.

    Their entry has to go from songs.dtb as well: a song still listed with no
    files behind it shows in the setlist and hangs the game when picked.
    """
    from . import dta

    songs_root = os.path.join(ark_build, "songs")
    gen = os.path.join(songs_root, "gen")
    freed = 0
    gone = []
    for name in DEMO_SONGS:
        folder = os.path.join(songs_root, name)
        if os.path.isdir(folder):
            freed += sum(os.path.getsize(os.path.join(d, f))
                         for d, _, fs in os.walk(folder) for f in fs)
            shutil.rmtree(folder, ignore_errors=True)
            gone.append(name)
        # Art and weights sit in the shared gen folder rather than the song's.
        if os.path.isdir(gen):
            for f in os.listdir(gen):
                if f.lower().startswith(name):
                    path = os.path.join(gen, f)
                    freed += os.path.getsize(path)
                    os.remove(path)
    if not gone:
        return 0
    if log:
        log("removed the base game's %s, freeing %.0f MB"
            % (", ".join(gone), freed / 1e6))
    dta.strip_from_dtb(settings, os.path.join(gen, "songs.dtb"), gone, log=log)
    return freed


def prepare(settings, log=None):
    """Unpack the base game's archive, once, so songs can be injected into it.

    Scripts are left as the compiled DTBs they are: converting them to text would
    change what gets packed back in. The unpacked tree is kept between builds and
    only redone when the game folder changes, since it takes a few minutes.
    """
    hdr = settings.base_ark()
    root = settings.ark_source
    st = os.stat(hdr)
    stamp = os.path.join(root, STAMP)
    signature = {"hdr": hdr, "size": st.st_size, "mtime": int(st.st_mtime)}

    if os.path.exists(stamp):
        try:
            with open(stamp, encoding="utf-8") as fp:
                if json.load(fp) == signature and os.path.isdir(
                        os.path.join(root, "songs")):
                    return root
        except ValueError:
            pass

    if log:
        log("unpacking the base game's archive (a few minutes, once) ...")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    r = proc.run([settings.tool("arkhelper"), "ark2dir", hdr, root,
                  "-a", "-l", "error"], capture_output=True, text=True)
    if not os.path.isdir(os.path.join(root, "songs")):
        raise BuildError("Could not unpack %s. Check that folder really is an "
                         "unpacked Rock Band 2 Deluxe PS2 release.\n%s"
                         % (hdr, (r.stderr or r.stdout)[-400:]))
    with open(stamp, "w", encoding="utf-8") as fp:
        json.dump(signature, fp)
    if log:
        log("  unpacked to %s" % root)
    return root


def required_files(sid):
    """Files a finished PS2 song folder must contain, mirroring retail layout.

    A retail song folder holds only the .mid, .pan, .pss, prev_*.vgs and the
    three gen/ files. The main mix exists solely inside the .pss, so the bare
    multichannel .vgs stays in staging as mux input and is never shipped -
    it would waste 14 MB of disc per song. The intermediate .m2v likewise
    ships inside the .pss rather than beside it.
    """
    return {
        "%s.mid" % sid: "",
        "%s.pan" % sid: "",
        "%s.pss" % sid: "",
        "prev_%s.vgs" % sid: "",
        "%s_keep.png_ps2" % sid: "gen",
        "%s_nomip_keep.bmp_ps2" % sid: "gen",
        "%s_weights.bin" % sid: "gen",
    }


def check(settings, sids):
    """Which of the given songs can ship, and what the others still need.

    Returns (ready_sids, problems_by_sid), where each problem is a list of
    human-readable lines: the missing files, or why the song cannot ship.
    """
    ready = []
    problems = {}
    for sid in sids:
        d = os.path.join(settings.stage, sid)
        missing = []
        for fname, sub in required_files(sid).items():
            if not os.path.exists(os.path.join(d, sub, fname) if sub
                                  else os.path.join(d, fname)):
                missing.append(os.path.join(sub, fname) if sub else fname)
        if missing:
            problems[sid] = missing
            continue
        # A song whose files are all present can still be unshippable: a chart
        # asking for drum channels its mix does not have hangs the load with no
        # way to tell from the disc that anything is wrong.
        ok, why = charts.check_mix(settings, sid)
        if not ok:
            problems[sid] = [why]
            continue
        ready.append(sid)
    return ready, problems


def song_bytes(settings, sid):
    """What one staged song costs on the disc, counting both preview copies."""
    d = os.path.join(settings.stage, sid)
    total = 0
    for fname, sub in required_files(sid).items():
        path = os.path.join(d, sub, fname) if sub else os.path.join(d, fname)
        if os.path.exists(path):
            total += os.path.getsize(path)
    clip = os.path.join(d, "prev_%s.vgs" % sid)
    if os.path.exists(clip):
        total += os.path.getsize(clip)
    return total


def disc_bytes(settings, sids):
    """Roughly what a disc carrying these songs would weigh.

    The ark is all but a few megabytes of the disc, so the base game's own
    archive plus the song data is close enough to plan a song list against.
    """
    return settings.base_ark_bytes() + sum(song_bytes(settings, s) for s in sids)


def assemble(settings, sids, log=None):
    """Build the tree that gets packed. Returns (shipped_sids, problems_by_sid).

    Songs arrive one at a time, so an incomplete one is skipped rather than
    failing the build. The song list has to describe the same set, which is what
    keeps it from naming songs whose files never shipped.
    """
    custom_dtb = os.path.join(settings.dta_dir, "songs_customs.dtb")
    if not os.path.exists(custom_dtb):
        raise BuildError("The song list has not been compiled yet: %s is "
                         "missing." % custom_dtb)

    shipped, problems = check(settings, sids)
    if problems and log:
        log("skipping %d song(s) that are not ready:" % len(problems))
        for sid, missing in problems.items():
            log("  %s" % sid)
            for m in missing:
                log("      %s" % m)
        vgs = [m for ms in problems.values() for m in ms if m.endswith(".vgs")]
        if vgs:
            log("%d song(s) have no encoded audio yet, so their audio stage "
                "did not finish." % len(vgs))
    if not shipped:
        raise BuildError("None of the chosen songs are finished, so there is "
                         "nothing to put on the disc.")
    if log:
        log("shipping %d song(s): %s" % (len(shipped), ", ".join(shipped)))

    if not os.listdir(settings.ark_source):
        raise BuildError("The base game's archive has not been unpacked yet, so "
                         "there is nothing to inject the songs into.")

    # Fresh copy of the extracted CE tree so repeat runs stay reproducible.
    ark_build = settings.ark_build
    if log:
        log("copying CE tree -> %s ..." % ark_build)
    fresh_dir(ark_build)
    shutil.copytree(settings.ark_source, ark_build)
    if settings.drop_demos:
        drop_demo_songs(settings, ark_build, log=log)

    songs_root = os.path.join(ark_build, "songs")
    os.makedirs(songs_root, exist_ok=True)
    clips_root = os.path.join(songs_root, PREVIEW_DIR)
    os.makedirs(clips_root, exist_ok=True)

    total = 0
    for sid in shipped:
        src = os.path.join(settings.stage, sid)
        dst = os.path.join(songs_root, sid)
        shutil.rmtree(dst, ignore_errors=True)
        os.makedirs(os.path.join(dst, "gen"), exist_ok=True)
        for fname, sub in required_files(sid).items():
            s = os.path.join(src, sub, fname) if sub else os.path.join(src, fname)
            d = os.path.join(dst, sub, fname) if sub else os.path.join(dst, fname)
            shutil.copyfile(s, d)
            total += os.path.getsize(s)

        # A second copy of the preview clip, all of them together in a folder
        # that packs at the front of the songs section. Previews were silent on
        # a cold boot while they sat deep inside each song's folder, and the one
        # that did play was the base game's own, right at the front. The song
        # folder keeps its copy too, so a build is no worse off if the game
        # ignores preview_clip and looks there by convention.
        clip = "prev_%s.vgs" % sid
        shutil.copyfile(os.path.join(src, clip),
                        os.path.join(clips_root, clip))
        total += os.path.getsize(os.path.join(src, clip))
        if log:
            log("  injected songs/%s" % sid)

    dtb_dst = os.path.join(songs_root, "gen", "songs_customs.dtb")
    os.makedirs(os.path.dirname(dtb_dst), exist_ok=True)
    shutil.copyfile(custom_dtb, dtb_dst)
    if log:
        log("  replaced songs/gen/songs_customs.dtb (%d bytes)"
            % os.path.getsize(dtb_dst))
        log("  %d songs, %.1f MB of song data added"
            % (len(shipped), total / 1048576.0))
    return shipped, problems


def pack(settings, log=None):
    """Repack the assembled tree into MAIN.HDR and MAIN_*.ARK. Returns the folder."""
    ark_out = settings.ark_out
    if log:
        log("repacking ark ...")
    shutil.rmtree(ark_out, ignore_errors=True)
    os.makedirs(ark_out, exist_ok=True)
    r = proc.run([settings.tool("arkhelper"), "dir2ark",
                  settings.ark_build, ark_out,
                  "-n", "MAIN", "-e", "-v", "4", "-s", str(SIZE_LIMIT),
                  "-l", "error"], capture_output=True, text=True)
    parts = sorted(os.listdir(ark_out)) if os.path.isdir(ark_out) else []
    if not any(p.upper().endswith(".HDR") for p in parts):
        raise BuildError("The archive could not be packed: %s"
                         % (r.stderr or r.stdout)[-400:])

    if log:
        for p in parts:
            log("  %-14s %13d" % (p, os.path.getsize(os.path.join(ark_out, p))))
    return ark_out
