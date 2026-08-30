"""Open every page, exercise the interactive bits, then close. No build.

Run from anywhere:  python tests\\gui_smoke.py
Needs settings that already point at a song folder, since it scans for real.
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rb2dx.gui.app import App

app = App()
app.update()

problems = []
try:
    # Every tab has to lay out and draw.
    for index in range(4):
        app.select_tab(index)
        app.update()

    # The song table needs songs before the rest means anything. The scan runs
    # on its own thread and reports back through the event loop, so pump it.
    import time
    for _ in range(200):
        app.update()
        if app.songs_tab.songs:
            break
        time.sleep(0.15)
    songs = app.songs_tab.songs
    print("songs scanned: %d" % len(songs))

    if songs:
        app.songs_tab.set_all(True)
        app.update()
        print("all selected : %d, usage says %r"
              % (len(app.songs_tab.selected_songs()),
                 app.songs_tab.usage_note.cget("text")))

        app.songs_tab.set_all(False)
        app.update()
        print("none selected: %r" % app.songs_tab.usage_note.cget("text"))

        # Sorting by every column, and filtering.
        for column in ("song", "library", "tier", "length", "size", "video",
                       "built"):
            app.songs_tab.sort(column)
            app.update()
        app.songs_tab.filter_var.set("beatles")
        app.update()
        print("filtered rows: %d" % len(app.songs_tab.tree.get_children()))
        app.songs_tab.filter_var.set("")
        app.update()

        # Pricing against the disc, which drives the usage bar.
        from rb2dx import plan as planner
        print("room left    : %.2f GB with everything on"
              % (planner.room_left(app.settings, list(songs)) / 1e9))
        print("folder counts: %s"
              % [app.songs_tab.lib_tree.set(iid, "songs")
                 for iid in app.songs_tab.lib_tree.get_children()])

        # Toggling one row, the way a click does.
        first = songs[0].path
        app.songs_tab.chosen[first] = True
        app.songs_tab.update_usage()
        app.update()
        print("build page   : %r" % app.build_tab.summary.cget("text"))

        # Moving the video a song brought with it, if any song here brought one.
        own = [s for s in songs if s.video]
        print("own videos   : %d of %d songs" % (len(own), len(songs)))
        if own:
            from rb2dx.gui.video_dialog import VideoDialog
            song = own[0]
            was = app.settings.nudge(song.path)
            dialog = VideoDialog(app.songs_tab, app, song)
            app.update()
            print("video dialog : %r" % dialog._describe())
            dialog.move(1.0)
            dialog.accept()
            app.update()
            print("nudged to    : %+.2f s, column says %r"
                  % (app.settings.nudge(song.path),
                     app.songs_tab.video_cell(song)))
            app.settings.set_nudge(song.path, was)
            app.settings.save()
            app.songs_tab.redraw()
            app.update()

    # The results page with a finished-looking result.
    from rb2dx.pipeline import Result
    result = Result()
    result.shipped = ["one", "two"]
    result.problems = [(songs[0], "converting chart", "Magma said no")] \
        if songs else []
    result.report = ["archive: 2 songs checked", "iso: boots"]
    result.seconds = 640.0
    app.results_tab.show(result)
    app.select_tab(3)
    app.update()
    print("results page : %r" % app.results_tab.headline.cget("text"))

    # Tool table refresh, and the status line.
    app.setup_tab.refresh_tools()
    app.update()
    print("status line  : %r" % app.status.cget("text"))
except Exception:
    problems.append(traceback.format_exc())

app.destroy()
print("\nFAILURES:\n%s" % "\n".join(problems) if problems else "\nno errors")
