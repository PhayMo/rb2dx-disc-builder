"""Build the bootable PS2 ISO for Rock Band 2 Deluxe Custom Edition.

Takes the boot files from the unpacked base game and the freshly repacked ark
from the ark output folder, then writes an ISO9660 image. Volume metadata and
interchange level 1 (8.3, uppercase, ";1" versions) mirror the retail disc.
"""

import os
import shutil
import time

import pycdlib

from .errors import BuildError

IDENT = dict(
    interchange_level=1,
    sys_ident="PLAYSTATION",
    vol_ident="BAND_S",
    pub_ident_str="ELECTRONIC ARTS",
    preparer_ident_str="PI STUDIOS",
    app_ident_str="PLAYSTATION",
)

# Once an image is big enough that the console sees a DVD rather than a CD, a
# plain ISO9660 filesystem is not enough: retail PS2 DVDs (and images made by
# ImgBurn/mkisofs) are ISO9660+UDF bridge discs, and the game hangs on a black
# screen without the UDF side. Small images boot either way.
UDF_VERSION = "2.60"


def collect(settings):
    """Return [(source_path, iso_path)] ordered boot files, GEN, then IOP."""
    items = []
    # The executable keeps the name it has in the base game: SYSTEM.CNF's BOOT2
    # line names it, and the console looks for exactly that name in the root.
    for path in (settings.boot_elf(),
                 os.path.join(settings.base_game, "SYSTEM.CNF")):
        if not os.path.exists(path):
            raise BuildError("Missing boot file: %s. Check that your base game "
                             "folder is the unpacked PS2 release." % path)
        items.append((path, "/%s;1" % os.path.basename(path).upper()))

    gen = settings.ark_out
    if not os.path.isdir(gen):
        raise BuildError("Missing the repacked archive folder: %s." % gen)
    gen_files = sorted(os.listdir(gen))
    if not any(f.upper().endswith(".HDR") for f in gen_files):
        raise BuildError("No MAIN.HDR in %s - the archive was not packed." % gen)
    for f in gen_files:
        items.append((os.path.join(gen, f), "/GEN/%s;1" % f.upper()))

    iop = os.path.join(settings.base_game, "IOP")
    if not os.path.isdir(iop):
        raise BuildError("No IOP folder in %s - the disc needs the base game's "
                         "IOP modules to boot." % settings.base_game)
    for f in sorted(os.listdir(iop)):
        items.append((os.path.join(iop, f), "/IOP/%s;1" % f.upper()))
    return items


def check_ceiling(settings, nbytes):
    """Refuse a disc bigger than the user said their media can hold."""
    if nbytes > settings.ceiling_bytes:
        over = nbytes - settings.ceiling_bytes
        raise BuildError(
            "The disc would come out %.2f GB, which is %.0f MB over your %.2f "
            "GB limit. Drop some songs from the list and build again."
            % (nbytes / 1073741824.0, over / 1048576.0,
               settings.ceiling_bytes / 1073741824.0))


def folder_beside(iso_path):
    """The default home for the loose disc files: next to the ISO."""
    return os.path.splitext(iso_path)[0] + " disc" if iso_path else ""


def folder_for(settings):
    """Where the loose disc files go: wherever asked, or beside the ISO."""
    return settings.disc_folder_path or folder_beside(settings.out_iso)


def export_folder(settings, log=None):
    """Lay the disc's files out in a folder. Returns its path.

    PCSX2 will not boot an image written here, so this gives anyone playing in
    an emulator the disc's contents to hand to ImgBurn instead. Files are linked
    rather than copied where the filesystem allows it, so a second copy of the
    archive costs no space.
    """
    if not settings.out_iso:
        raise BuildError("Choose where to write the finished ISO first; the "
                         "disc folder goes beside it.")
    items = collect(settings)
    dest = folder_for(settings)
    if os.path.isdir(dest):
        shutil.rmtree(dest)

    linked = copied = 0
    for src, iso_path in items:
        # "/GEN/MAIN_0.ARK;1" is a file named MAIN_0.ARK in a folder named GEN.
        parts = iso_path.strip("/").split("/")
        parts[-1] = parts[-1].rsplit(";", 1)[0]
        target = os.path.join(dest, *parts)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            os.link(src, target)
            linked += 1
        except OSError:
            # A different drive, or a filesystem without hard links.
            shutil.copy2(src, target)
            copied += 1

    if log:
        log("Disc folder: %s" % dest)
        log("  %d files linked, %d copied" % (linked, copied))
        log("  Build the image from this folder with ImgBurn: Build mode, "
            "ISO9660 + UDF 1.02, everything at the root.")
    return dest


def build(settings, log=None):
    """Write the finished image. Returns its path."""
    items = collect(settings)
    out = settings.out_iso
    if not out:
        raise BuildError("Choose where to write the finished ISO first.")

    total = sum(os.path.getsize(src) for src, _ in items)
    check_ceiling(settings, total)

    iso = pycdlib.PyCdlib()
    kwargs = dict(IDENT)
    kwargs["udf"] = UDF_VERSION
    try:
        iso.new(copyright_file="HARMONIX MUSIC SYSTEMS", **kwargs)
    except Exception:
        iso = pycdlib.PyCdlib()
        iso.new(**kwargs)
    if log:
        log("  filesystem: ISO9660 + UDF %s" % UDF_VERSION)

    def udf_of(iso_path):
        """UDF names carry no ';1' version suffix."""
        return iso_path.rsplit(";", 1)[0]

    for d in ("/GEN", "/IOP"):
        iso.add_directory(d, udf_path=udf_of(d))

    handles = []
    for src, iso_path in items:
        size = os.path.getsize(src)
        fp = open(src, "rb")
        handles.append(fp)
        iso.add_fp(fp, size, iso_path, udf_path=udf_of(iso_path))
        if log:
            log("  %-40s %13d -> %s" % (os.path.basename(src), size, iso_path))

    if log:
        log("%d files, %.2f GB payload" % (len(items), total / 1073741824.0))
        log("writing %s ..." % out)

    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)

    state = {"pct": -1, "start": time.time()}

    def progress(done, outsize, _opaque):
        pct = int(done * 100 / outsize) if outsize else 0
        if pct != state["pct"] and pct % 10 == 0:
            if log:
                log("  %3d%%  %8.1f / %.1f MiB  (%.0fs)"
                    % (pct, done / 1048576.0, outsize / 1048576.0,
                       time.time() - state["start"]))
            state["pct"] = pct

    iso.write(out, progress_cb=progress)
    iso.close()
    for fp in handles:
        fp.close()

    size = os.path.getsize(out)
    check_ceiling(settings, size)
    if log:
        log("Done: %s" % out)
        log("  %d bytes (%.2f GB)" % (size, size / 1073741824.0))
        if size > 4700372992:
            log("  NOTE: exceeds DVD-5; needs DVD-9 media, or split for "
                "FAT32/OPL USB.")
    return out
