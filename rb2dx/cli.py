"""Command line front end.

  python -m rb2dx setup --show
  python -m rb2dx setup --base-game "D:\\RB2DXCE-PS2" --work D:\\rb2dx\\work
  python -m rb2dx setup --add-library "D:\\Charts\\Rock Band 3"
  python -m rb2dx setup --download
  python -m rb2dx scan
  python -m rb2dx plan
  python -m rb2dx build
  python -m rb2dx gui
"""

import argparse
import os
import sys

from . import iso, library, plan as planner, settings as settings_mod, tools
from .errors import BuildError
from .settings import Settings, SettingsError


def human(n):
    return "%.2f GB" % (n / 1e9)


def load():
    return Settings.load()


def cmd_setup(args):
    s = load()
    changed = False

    if args.base_game:
        s.base_game = os.path.abspath(args.base_game)
        changed = True
    if args.work:
        s.work = os.path.abspath(args.work)
        changed = True
    if args.tmp:
        s.tmp = os.path.abspath(args.tmp)
        changed = True
    if args.out:
        s.out_iso = os.path.abspath(args.out)
        changed = True
    if args.venue:
        s.venue_dir = os.path.abspath(args.venue)
        changed = True
    if args.video:
        s.video_kbps = args.video
        changed = True
    if args.background:
        s.background = args.background
        changed = True
    if args.jobs:
        s.jobs = args.jobs
        changed = True
    if args.ceiling:
        s.ceiling_bytes = int(args.ceiling * 1e9)
        changed = True
    if args.demo_songs:
        s.drop_demos = args.demo_songs == "drop"
        changed = True
    if args.disc_folder:
        s.disc_folder = args.disc_folder == "yes"
        changed = True
    if args.disc_folder_path:
        s.disc_folder_path = os.path.abspath(args.disc_folder_path)
        changed = True
    if args.add_library:
        path = os.path.abspath(args.add_library)
        s.libraries = [lib for lib in s.libraries if lib.path != path]
        s.libraries.append(settings_mod.Library(path))
        changed = True
    if args.remove_library:
        path = os.path.abspath(args.remove_library)
        s.libraries = [lib for lib in s.libraries if lib.path != path]
        changed = True
    if args.set_tool:
        key, path = args.set_tool
        if key not in tools.BY_KEY:
            sys.exit("Unknown tool %r. Known: %s"
                     % (key, ", ".join(sorted(tools.BY_KEY))))
        ok, why = tools.validate(tools.BY_KEY[key], path)
        if not ok:
            sys.exit("%s: %s" % (path, why))
        s.tools[key] = os.path.abspath(path)
        changed = True

    if args.defaults:
        suggested = settings_mod.suggest()
        s.work = s.work or suggested.work
        s.tmp = s.tmp or suggested.tmp
        s.out_iso = s.out_iso or suggested.out_iso
        changed = True

    if changed:
        print("Settings saved to %s" % s.save())

    if args.download:
        missing = [k for k in tools.missing(s) if tools.BY_KEY[k].downloadable]
        if not missing:
            print("Every downloadable tool is already in place.")
        for key in missing:
            try:
                tools.install([key], s, status=lambda t: print("  " + t))
            except tools.ToolError as exc:
                print("  %s: %s" % (key, exc))

    if args.show or not changed:
        print("\nSettings file: %s" % settings_mod.settings_path())
        print("  base game    %s" % (s.base_game or "-"))
        print("  work         %s" % (s.work or "-"))
        print("  temp         %s" % (s.tmp or "-"))
        print("  output ISO   %s" % (s.out_iso or "-"))
        print("  background   %s" % ("black" if s.black_background
                                     else "venue videos"))
        print("  videos       %s%s" % (s.venue_dir or "-",
                                       "" if s._venue_dir else " (bundled)"))
        print("  video        %d kbps" % s.encode_kbps)
        print("  disc ceiling %s" % human(s.ceiling_bytes))
        print("  parallel     %d songs at a time" % s.jobs)
        print("  base songs   %s" % ("left out" if s.drop_demos else "kept"))
        print("  disc folder  %s" % (iso.folder_for(s) if s.disc_folder
                                     else "no"))
        print("  libraries")
        for lib in s.libraries:
            print("    %-40s%s" % (lib.name,
                                   "" if lib.enabled else " (off)"))
        print("  tools")
        for tool, path, ok, detail in tools.check_all(s):
            print("    %-10s %-8s %s" % (tool.key, detail, path or tool.source))
        for line in s.problems():
            print("  todo: %s" % line)
    return 0


def cmd_scan(args):
    s = load()
    songs, skipped = library.scan(s, rescan=args.rescan, log=print)
    for name, (count, seconds) in sorted(library.summarise(songs).items()):
        print("%-30s %4d songs, %.1f hours" % (name, count, seconds / 3600.0))
    if args.verbose:
        for song in songs:
            print("  %-8s %-11s %5.1f min  %d ch  %s"
                  % (song.sid, library.tier_name(song.tier), song.minutes,
                     song.channels, song.label))
    for folder, why in skipped:
        print("  skipped %-44s %s" % (folder[:44], why))
    print("\n%d songs usable" % len(songs))
    return 0


def cmd_plan(args):
    s = load()
    songs, _ = library.scan(s, log=print)
    songs, known_bad = planner.usable(s, songs)
    for song, info in known_bad:
        print("holding back %-40s %s" % (song.label[:40], info["reason"]))

    room = planner.room_left(s, songs)
    print()
    for name, (count, _) in sorted(library.summarise(songs).items()):
        print("  %-30s %3d songs, %s"
              % (name, count,
                 human(sum(x.bytes for x in songs if x.library == name))))
    print("\n%d songs, %s of songs, %s disc against a %s ceiling"
          % (len(songs), human(sum(x.bytes for x in songs)),
             human(planner.disc_bytes(s, songs)), human(s.ceiling_bytes)))
    if room < 0:
        print("That is %s too much: leave some songs out of the build, or "
              "lower --video." % human(-room))
    else:
        print("Room for another %s of songs." % human(room))
    return 0


def cmd_build(args):
    from .pipeline import Pipeline

    s = load()
    if args.video:
        s.video_kbps = args.video
    if args.background:
        s.background = args.background
    if args.jobs:
        s.jobs = args.jobs

    songs, _ = library.scan(s, log=print)
    songs, _ = planner.usable(s, songs)
    if args.limit:
        songs = songs[:args.limit]
    room = planner.room_left(s, songs)

    print("Building %d songs, about %s of disc"
          % (len(songs), human(planner.disc_bytes(s, songs))))
    if room < 0:
        print("Warning: that is %s over the size limit, so writing the ISO "
              "will fail. Use --limit, drop some song folders, or lower "
              "--video." % human(-room))
    pipe = Pipeline(s, on_log=print,
                    on_progress=lambda d, t: print("  [%d/%d]" % (d, t)),
                    on_stage=lambda text: print("\n== %s" % text))
    result = pipe.build(songs, venue_dir=getattr(s, "venue_dir", ""),
                        make_iso=not args.no_iso, do_verify=not args.no_verify)

    print("\n%d songs shipped in %.1f minutes"
          % (len(result.shipped), result.seconds / 60.0))
    for song, stage, reason in result.problems:
        print("  failed %-36s %s (%s)" % (song.label[:36], reason, stage))
    for line in result.report:
        print("  %s" % line)
    if result.iso:
        print("\nISO: %s (%s)" % (result.iso, human(result.iso_bytes)))
    if result.folder:
        print("Disc folder: %s" % result.folder)
    return 1 if result.cancelled else 0


def cmd_gui(args):
    from .gui import app
    app.main()
    return 0


def main(argv=None):
    # A build prints as it goes and takes hours, so keep the log live even when
    # it is being written to a file.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    ap = argparse.ArgumentParser(
        prog="rb2dx", description="Build a custom Rock Band 2 Deluxe disc for "
                                  "the PlayStation 2.")
    sub = ap.add_subparsers(dest="command")

    p = sub.add_parser("setup", help="show or change settings, fetch tools")
    p.add_argument("--show", action="store_true")
    p.add_argument("--defaults", action="store_true",
                   help="fill in unset paths with sensible defaults")
    p.add_argument("--base-game", metavar="DIR")
    p.add_argument("--work", metavar="DIR")
    p.add_argument("--tmp", metavar="DIR")
    p.add_argument("--out", metavar="ISO")
    p.add_argument("--venue", metavar="DIR", help="folder of background videos")
    p.add_argument("--background", choices=settings_mod.BACKGROUNDS,
                   help="what plays behind the songs; black needs no videos "
                        "and fits about two and a half times as many songs")
    p.add_argument("--video", type=int, metavar="KBPS")
    p.add_argument("--jobs", type=int, metavar="N")
    p.add_argument("--ceiling", type=float, metavar="GB")
    p.add_argument("--demo-songs", choices=("keep", "drop"),
                   help="the four songs the base game ships with")
    p.add_argument("--disc-folder", choices=("yes", "no"),
                   help="also write the disc's files to a folder, to make the "
                        "image with ImgBurn for PCSX2")
    p.add_argument("--disc-folder-path", metavar="DIR",
                   help="where that folder goes; beside the ISO by default")
    p.add_argument("--add-library", metavar="DIR")
    p.add_argument("--remove-library", metavar="DIR")
    p.add_argument("--set-tool", nargs=2, metavar=("NAME", "PATH"))
    p.add_argument("--download", action="store_true",
                   help="fetch every tool that can be downloaded")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("scan", help="list the songs found in your libraries")
    p.add_argument("--rescan", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("plan", help="price your songs against the disc's size")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("build", help="build the disc")
    p.add_argument("--limit", type=int, metavar="N",
                   help="only the first N songs, for a quick test")
    p.add_argument("--background", choices=settings_mod.BACKGROUNDS)
    p.add_argument("--video", type=int, metavar="KBPS")
    p.add_argument("--jobs", type=int, metavar="N")
    p.add_argument("--no-iso", action="store_true")
    p.add_argument("--no-verify", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("gui", help="open the graphical version")
    p.set_defaults(func=cmd_gui)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    try:
        return args.func(args)
    except (BuildError, SettingsError) as exc:
        print("\n%s" % exc)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 1
