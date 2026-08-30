"""The Songs page: what is available, what is chosen, and whether it fits."""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import library, plan as planner, settings as settings_mod
from . import video_dialog
from .common import PAD, Section, UsageBar, human, mmss, reveal

ON, OFF = "\u2611", "\u2610"


class SongsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=PAD)
        self.app = app
        self.songs = []
        self.chosen = {}          # song path -> bool
        self.known_bad = {}       # song path -> reason
        self.sort_by = "library"
        self.sort_desc = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ---- libraries -----------------------------------------------------
        libs = Section(self, "Song folders")
        libs.grid(row=0, column=0, sticky="ew")
        libs.columnconfigure(0, weight=1)
        self.lib_tree = ttk.Treeview(libs, columns=("songs", "path"),
                                     show="tree headings", height=3,
                                     selectmode="browse")
        self.lib_tree.heading("#0", text="Collection")
        self.lib_tree.heading("songs", text="Chosen")
        self.lib_tree.heading("path", text="Folder")
        self.lib_tree.column("#0", width=200, stretch=False)
        self.lib_tree.column("songs", width=110, stretch=False, anchor="e")
        self.lib_tree.column("path", width=420)
        self.lib_tree.grid(row=0, column=0, columnspan=4, sticky="ew")

        row = ttk.Frame(libs)
        row.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Button(row, text="Add a folder...",
                   command=self.add_library).pack(side="left")
        ttk.Button(row, text="Remove",
                   command=self.remove_library).pack(side="left", padx=6)
        self.scan_btn = ttk.Button(row, text="Scan for songs",
                                   command=lambda: self.scan(True))
        self.scan_btn.pack(side="left", padx=6)
        self.scan_note = ttk.Label(row, text="", foreground="#666")
        self.scan_note.pack(side="left", padx=6)

        # ---- song table ----------------------------------------------------
        table = Section(self, "Songs")
        table.grid(row=1, column=0, sticky="nsew", pady=(PAD, 0))
        table.rowconfigure(1, weight=1)
        table.columnconfigure(0, weight=1)

        bar = ttk.Frame(table)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(bar, text="Find").pack(side="left")
        self.filter_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.filter_var, width=26)
        entry.pack(side="left", padx=(4, PAD))
        self.filter_var.trace_add("write", lambda *_: self.redraw())
        self.hide_bad = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Hide songs that failed before",
                        variable=self.hide_bad,
                        command=self.redraw).pack(side="left")
        ttk.Button(bar, text="Line up a video...",
                   command=self.line_up_video).pack(side="left", padx=PAD)
        ttk.Button(bar, text="All", width=5,
                   command=lambda: self.set_all(True)).pack(side="right")
        ttk.Button(bar, text="None", width=6,
                   command=lambda: self.set_all(False)).pack(side="right",
                                                             padx=4)

        cols = ("song", "library", "tier", "length", "size", "video", "built")
        # A modest requested height, so the usage bar below always has room and
        # the table simply grows with the window.
        self.tree = ttk.Treeview(table, columns=cols, show="tree headings",
                                 height=8, selectmode="extended")
        headings = [("#0", "", 34), ("song", "Song", 300),
                    ("library", "Collection", 140), ("tier", "Difficulty", 90),
                    ("length", "Length", 70), ("size", "On disc", 80),
                    ("video", "Video", 90), ("built", "State", 140)]
        for key, title, width in headings:
            self.tree.heading(key, text=title,
                              command=(lambda k=key: self.sort(k))
                              if key != "#0" else self.toggle_visible)
            self.tree.column(key, width=width,
                             stretch=(key == "song"),
                             anchor="w" if key in ("#0", "song", "library",
                                                   "video", "built") else "e")
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical",
                               command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.tag_configure("bad", foreground="#b03a3a")
        self.tree.tag_configure("done", foreground="#2c6e3f")
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<space>", lambda e: self.toggle_selected())
        self.tree.bind("<Double-1>", lambda e: self.toggle_selected())
        self.tree.bind("<Button-3>", self._context)

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Line up its video...",
                              command=self.line_up_video)
        self.menu.add_command(label="Open the song folder",
                              command=self.open_folder)

        # ---- disc usage ----------------------------------------------------
        usage = ttk.Frame(self)
        usage.grid(row=2, column=0, sticky="ew", pady=(PAD, 0))
        usage.columnconfigure(0, weight=1)
        self.usage = UsageBar(usage)
        self.usage.grid(row=0, column=0, sticky="ew")
        self.usage_note = ttk.Label(usage, text="No songs scanned yet.")
        self.usage_note.grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.refresh_libraries()

    # ---- libraries ---------------------------------------------------------

    def refresh_libraries(self):
        self.lib_tree.delete(*self.lib_tree.get_children())
        chosen = {}
        for song in self.selected_songs():
            chosen[song.library] = chosen.get(song.library, 0) + 1
        found = {}
        for song in self.songs:
            found[song.library] = found.get(song.library, 0) + 1
        for lib in self.app.settings.libraries:
            total = found.get(lib.name)
            self.lib_tree.insert(
                "", "end", iid=lib.path, text=lib.name,
                values=("%d of %d" % (chosen.get(lib.name, 0), total)
                        if total else "not scanned", lib.path))

    def add_library(self):
        picked = filedialog.askdirectory(title="Choose a folder of songs")
        if not picked:
            return
        picked = os.path.normpath(picked)
        s = self.app.settings
        if any(lib.path == picked for lib in s.libraries):
            return
        s.libraries.append(settings_mod.Library(picked))
        s.save()
        self.refresh_libraries()
        self.app.settings_changed()
        self.scan(True)

    def remove_library(self):
        path = self.lib_tree.focus()
        if not path:
            return
        s = self.app.settings
        s.libraries = [lib for lib in s.libraries if lib.path != path]
        s.save()
        self.refresh_libraries()
        self.songs = [x for x in self.songs if not x.path.startswith(path)]
        self.redraw()
        self.app.settings_changed()

    # ---- scanning ----------------------------------------------------------

    def scan(self, rescan=False):
        if not self.app.settings.libraries:
            messagebox.showinfo("No song folders",
                                "Add at least one folder of songs first.")
            return
        try:
            self.app.settings.tool("ffprobe")
        except Exception as exc:
            messagebox.showwarning("Tools not ready", str(exc))
            return
        self.scan_btn.state(["disabled"])
        self.scan_note.config(text="Scanning...")

        # The scan runs on its own thread and reports back through the window's
        # event queue: Tk objects belong to the main thread only.
        post = self.app.post

        def work():
            try:
                songs, skipped = library.scan(
                    self.app.settings, rescan=rescan,
                    progress=lambda d, t: post("scan_note",
                                               "Reading lengths %d/%d" % (d, t)),
                    log=lambda text: post("scan_note", text))
                error = None
            except Exception as exc:
                songs, skipped, error = [], [], str(exc)
            post("scan_done", (songs, skipped, error))

        threading.Thread(target=work, daemon=True).start()

    def scanned(self, songs, skipped, error):
        self.scan_btn.state(["!disabled"])
        if error:
            self.scan_note.config(text="")
            messagebox.showerror("Could not scan", error)
            return
        self.songs = songs
        planner.price(self.app.settings, self.songs)
        bad = planner.load_problems(self.app.settings)
        self.known_bad = {p: info["reason"] for p, info in bad.items()}
        for song in songs:
            self.chosen.setdefault(song.path, song.path not in self.known_bad)
        self.scan_note.config(
            text="%d songs, %d folders skipped" % (len(songs), len(skipped)))
        self.redraw()

    # ---- the table ---------------------------------------------------------

    def visible(self):
        needle = self.filter_var.get().strip().lower()
        out = []
        for song in self.songs:
            if self.hide_bad.get() and song.path in self.known_bad:
                continue
            if needle and needle not in (song.label + song.library).lower():
                continue
            out.append(song)
        keys = {
            "song": lambda s: s.label.lower(),
            "library": lambda s: (s.library.lower(), s.label.lower()),
            "tier": lambda s: (-1 if s.tier is None else s.tier),
            "length": lambda s: s.seconds,
            "size": lambda s: getattr(s, "bytes", 0),
            "video": lambda s: (bool(s.video),
                                self.app.settings.nudge(s.path)),
            "built": lambda s: s.path in self.known_bad,
        }
        out.sort(key=keys.get(self.sort_by, keys["library"]),
                 reverse=self.sort_desc)
        return out

    def redraw(self):
        self.tree.delete(*self.tree.get_children())
        state = self.app.built_state
        for song in self.visible():
            tags = []
            if song.path in self.known_bad:
                built = "failed: %s" % self.known_bad[song.path]
                tags.append("bad")
            else:
                stages = state.get(song.path, {})
                if stages and all(stages.values()):
                    built = "ready to ship"
                    tags.append("done")
                elif any(stages.values()):
                    built = "%d of %d stages done" % (
                        sum(1 for v in stages.values() if v), len(stages))
                else:
                    built = "not built yet"
            self.tree.insert(
                "", "end", iid=song.path,
                text=ON if self.chosen.get(song.path) else OFF,
                values=(song.label, song.library, library.tier_name(song.tier),
                        mmss(song.seconds), human(getattr(song, "bytes", 0)),
                        self.video_cell(song), built),
                tags=tuple(tags))
        self.update_usage()

    def video_cell(self, song):
        """What the Video column says: whose video plays, and how far it is moved.

        Blank means a background clip from the venues folder, which is most songs
        and not worth a word in every row.
        """
        if not song.video or self.app.settings.black_background:
            return ""
        nudge = self.app.settings.nudge(song.path)
        return "own %+.2fs" % nudge if nudge else "own"

    def sort(self, key):
        self.sort_desc = not self.sort_desc if self.sort_by == key else False
        self.sort_by = key
        self.redraw()

    # ---- a song's own video ------------------------------------------------

    def focused_song(self):
        """The song the row commands act on, or None with a word about why not."""
        paths = list(self.tree.selection()) or [self.tree.focus()]
        found = [s for s in self.songs if s.path in paths]
        if len(found) != 1:
            messagebox.showinfo(
                "Which song?",
                "Click the song in the list first, then try again."
                if not found else
                "Pick one song at a time for this.")
            return None
        return found[0]

    def line_up_video(self):
        song = self.focused_song()
        if song is None:
            return
        if self.app.settings.black_background:
            messagebox.showinfo(
                "Nothing is playing",
                "The background is set to black on the Setup page, so no video "
                "plays behind any song. Set it back to the background clips "
                "first.")
            return
        if not song.video:
            messagebox.showinfo(
                "No video in this folder",
                "%s has no video of its own, so a background clip plays behind "
                "it and there is nothing to line up.\n\nTo give it one, put a "
                "file named video.mp4 - or any of %s - in its folder and scan "
                "again."
                % (song.label,
                   ", ".join("video%s" % e
                             for e in settings_mod.SONG_VIDEO_EXTS[:4])))
            return
        video_dialog.VideoDialog(self, self.app, song)

    def open_folder(self):
        song = self.focused_song()
        if song is not None:
            reveal(song.path)

    def _context(self, event):
        path = self.tree.identify_row(event.y)
        if path:
            self.tree.selection_set(path)
            self.tree.focus(path)
        self.menu.tk_popup(event.x_root, event.y_root)

    def _click(self, event):
        if self.tree.identify_region(event.x, event.y) != "tree":
            return
        path = self.tree.identify_row(event.y)
        if path:
            self.chosen[path] = not self.chosen.get(path)
            self.tree.item(path, text=ON if self.chosen[path] else OFF)
            self.update_usage()
            return "break"

    def toggle_selected(self):
        for path in self.tree.selection():
            self.chosen[path] = not self.chosen.get(path)
            self.tree.item(path, text=ON if self.chosen[path] else OFF)
        self.update_usage()

    def toggle_visible(self):
        rows = self.visible()
        target = not all(self.chosen.get(s.path) for s in rows)
        for song in rows:
            self.chosen[song.path] = target
        self.redraw()

    def set_all(self, value):
        for song in self.visible():
            self.chosen[song.path] = value
        self.redraw()

    # ---- how full the disc is ----------------------------------------------

    def selected_songs(self):
        return [s for s in self.songs if self.chosen.get(s.path)
                and s.path not in self.known_bad]

    def update_usage(self):
        chosen = self.selected_songs()
        s = self.app.settings
        try:
            base = s.base_ark_bytes()
        except Exception:
            base = int(0.82e9)
        songs_bytes = sum(getattr(x, "bytes", 0) for x in chosen)
        self.usage.show(base, songs_bytes, s.ceiling_bytes)
        room = s.ceiling_bytes - planner.MARGIN_BYTES - base - songs_bytes
        if not self.songs:
            note = "No songs scanned yet."
        elif not chosen:
            note = "Nothing chosen yet - there is room for %s of songs." \
                % human(room)
        elif room >= 0:
            note = ("%d %s chosen, %s of songs, %s to spare."
                    % (len(chosen), "song" if len(chosen) == 1 else "songs",
                       human(songs_bytes), human(room)))
        else:
            note = ("%d songs chosen - %s too much for this disc. Turn some "
                    "off, or lower the video quality on the Setup page."
                    % (len(chosen), human(-room)))
        self.usage_note.config(text=note)
        self.refresh_libraries()
        self.app.selection_changed(len(chosen), room >= 0)
