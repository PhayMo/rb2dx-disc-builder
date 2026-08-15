"""The main window.

The build runs on a worker thread and reports through a queue, which the main
thread drains on a timer. Tk is not thread safe, so nothing here touches a widget
from anywhere but the main thread.
"""

import queue
import tkinter as tk
from tkinter import messagebox, ttk

from .. import plan as planner, pipeline, settings as settings_mod, tools
from ..settings import Settings
from .build_tab import BuildTab
from .common import PAD
from .results_tab import ResultsTab
from .setup_tab import SetupTab
from .songs_tab import SongsTab

TITLE = "Rock Band 2 Deluxe Disc Builder - by PhayMo"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(TITLE)
        # Fit the screen rather than assuming one: on a scaled display the window
        # is stretched, and a fixed size can run off the bottom.
        width = min(1180, self.winfo_screenwidth() - 60)
        height = min(880, self.winfo_screenheight() - 120)
        self.geometry("%dx%d" % (width, height))
        self.minsize(min(940, width), min(600, height))
        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass

        self.settings = Settings.load()
        if not self.settings.work:
            # First run: fill in paths that have obvious defaults so the Setup
            # page starts out mostly complete.
            suggested = settings_mod.suggest()
            self.settings.work = suggested.work
            self.settings.tmp = suggested.tmp
            self.settings.out_iso = suggested.out_iso
        self.events = queue.Queue()
        self.built_state = {}

        # The status line exists before the pages do, because building a page
        # reports what still needs setting up.
        self.status = ttk.Label(self, text="", anchor="w", foreground="#444")
        self.status.pack(side="bottom", fill="x", padx=PAD, pady=(4, 6))

        self.book = ttk.Notebook(self)
        self.book.pack(fill="both", expand=True, padx=PAD, pady=(PAD, 0))
        self.setup_tab = SetupTab(self.book, self)
        self.songs_tab = SongsTab(self.book, self)
        self.build_tab = BuildTab(self.book, self)
        self.results_tab = ResultsTab(self.book, self)
        self.book.add(self.setup_tab, text="  Setup  ")
        self.book.add(self.songs_tab, text="  Songs  ")
        self.book.add(self.build_tab, text="  Build  ")
        self.book.add(self.results_tab, text="  Results  ")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(120, self._drain)
        self.settings_changed()
        if self.settings.libraries:
            self.songs_tab.scan(False)

    # ---- cross-tab plumbing ------------------------------------------------

    def post(self, kind, payload=None):
        """Called from the build thread."""
        self.events.put((kind, payload))

    def select_tab(self, index):
        self.book.select(index)

    def settings_changed(self):
        todo = self.settings.problems()
        pending = tools.missing(self.settings)
        if pending:
            todo.insert(0, "Set up these tools: %s" % ", ".join(pending))
        self.status.config(
            text="Ready to build." if not todo
            else "Before building: %s" % todo[0],
            foreground="#2c6e3f" if not todo else "#8a5a00")
        if getattr(self, "songs_tab", None):
            self.songs_tab.update_usage()

    def selection_changed(self, count, fits):
        if not getattr(self, "build_tab", None):
            return
        songs = self.songs_tab.selected_songs()
        self.build_tab.describe(count, fits, planner.disc_bytes(self.settings,
                                                                songs))

    def selection_refresh(self):
        """Re-read which songs are already built, after a build finishes."""
        try:
            self.built_state = pipeline.song_state(self.settings,
                                                  self.songs_tab.songs)
        except Exception:
            self.built_state = {}
        self.songs_tab.redraw()

    # ---- events from the build thread --------------------------------------

    def _drain(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def _handle(self, kind, payload):
        tab = self.build_tab
        if kind == "scan_note":
            self.songs_tab.scan_note.config(text=payload)
            return
        if kind == "scan_done":
            self.songs_tab.scanned(*payload)
            return
        if kind == "tool_status":
            self.setup_tab.download_status(payload)
            return
        if kind == "tool_done":
            self.setup_tab.download_finished(payload)
            return
        if kind == "log":
            tab.write(payload)
        elif kind == "stage":
            tab.stage.config(text=payload)
            tab.write("== %s" % payload, "step")
        elif kind == "progress":
            done, total = payload
            tab.bar.config(maximum=total, value=done)
            tab.count.config(text="%d/%d" % (done, total))
        elif kind == "song":
            label, stage, ok, message = payload
            if not ok:
                tab.write("%s: %s - %s" % (label, stage, message), "bad")
            elif message != "already built":
                tab.write("%s: %s %s" % (label, stage, message))
        elif kind == "done":
            tab.write("Finished.", "good")
            tab.finished()
            self.results_tab.show(payload)
            self.select_tab(3)
        elif kind == "failed":
            tab.write(payload, "bad")
            tab.finished()
            messagebox.showerror("The build stopped", payload)

    def close(self):
        if self.build_tab.pipeline is not None:
            if not messagebox.askyesno(
                    "Stop the build?",
                    "A build is running. Close anyway and abandon it?"):
                return
            self.build_tab.pipeline.cancel()
        self.settings.save()
        self.destroy()


def main():
    App().mainloop()
