"""The Setup page: where the game files are and which tools to use."""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import iso, settings as settings_mod, tools
from .common import PAD, PathRow, ScrollFrame, Section

DISC_PRESETS = [
    ("Match the stock Deluxe disc (7.62 GiB)", settings_mod.RETAIL_ISO_BYTES),
    ("Fill a DVD-9 (7.96 GiB)", settings_mod.DVD9_BYTES),
    ("Fit a DVD-5 (4.38 GiB)", settings_mod.DVD5_BYTES),
]

VIDEO_PRESETS = [
    ("Good, 1500 kbps - about 11 MB a minute", 1500),
    ("Better, 2500 kbps", 2500),
    ("Smaller, 900 kbps", 900),
]


class SetupTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=PAD)
        self.app = app
        self.suggested_folder = ""
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        scroll = ScrollFrame(self)
        scroll.grid(row=0, column=0, sticky="nsew")
        page = scroll.body
        page.columnconfigure(0, weight=1)

        files = Section(page, "Files")
        files.grid(row=0, column=0, sticky="ew")
        self.base = PathRow(
            files, "Rock Band 2 Deluxe", hint="The unpacked PS2 release, "
            "the folder holding SLUS_218.00 and gen\\MAIN_0.ARK.",
            on_change=self.push)
        self.venue = PathRow(
            files, "Background videos", hint="Short clips, looped behind "
            "each song. Starts out using the ones bundled with this tool - "
            "point somewhere else to use your own. A song folder holding its "
            "own video plays that instead.", on_change=self.push)
        self.work = PathRow(
            files, "Work folder", hint="Scratch space. Allow about twice "
            "the size of the finished disc.", on_change=self.push)
        self.tmp = PathRow(
            files, "Short temp folder", hint="Something like C:\\rb2dxtmp. "
            "One of the tools is from 1999 and cannot handle spaces in paths.",
            on_change=self.push)
        self.iso = PathRow(files, "Save the ISO as", kind="file",
                           on_change=self.push)
        self.folder_var = tk.BooleanVar(value=False)
        files.add_row("For emulators", ttk.Checkbutton(
            files, text="Also save the disc's files in a folder",
            variable=self.folder_var),
            hint="PCSX2 will not boot the ISO written here, though a real PS2 "
                 "will. Tick this and build the image from the folder with "
                 "ImgBurn instead: Build mode, ISO9660 + UDF 1.02.")
        self.folder = PathRow(files, "Save the folder as", on_change=self.push)

        disc = Section(page, "Disc")
        disc.grid(row=1, column=0, sticky="ew", pady=(PAD, 0))
        self.disc_var = tk.StringVar()
        disc.add_row("Size limit", ttk.Combobox(
            disc, textvariable=self.disc_var, state="readonly",
            values=[name for name, _ in DISC_PRESETS]),
            hint="Staying at the stock size is safest: that disc is known to "
                 "work on real hardware.")
        self.video_var = tk.StringVar()
        disc.add_row("Video quality", ttk.Combobox(
            disc, textvariable=self.video_var, state="readonly",
            values=[name for name, _ in VIDEO_PRESETS]),
            hint="Lower quality fits more songs, at some cost to how the "
                 "background looks.")
        self.jobs_var = tk.IntVar(value=6)
        disc.add_row("Songs at once", ttk.Spinbox(
            disc, from_=1, to=32, textvariable=self.jobs_var, width=6),
            hint="How many songs to convert in parallel.")
        self.demos_var = tk.BooleanVar(value=True)
        disc.add_row("Bundled songs", ttk.Checkbutton(
            disc, text="Leave out the four songs the base game came with",
            variable=self.demos_var),
            hint="Frees about 264 MB, six or so of your own songs. Three of "
                 "them never appear in the setlist anyway; the fourth is "
                 "Afterlife.")
        self.disc_var.trace_add("write", lambda *_: self.push())
        self.video_var.trace_add("write", lambda *_: self.push())
        self.jobs_var.trace_add("write", lambda *_: self.push())
        self.demos_var.trace_add("write", lambda *_: self.push())
        self.folder_var.trace_add("write", lambda *_: self.push())

        tools_box = Section(page, "Tools")
        tools_box.grid(row=2, column=0, sticky="ew", pady=(PAD, 0))
        tools_box.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tools_box, columns=("state", "what", "path"),
                                 show="tree headings", height=8,
                                 selectmode="browse")
        self.tree.heading("#0", text="Tool")
        self.tree.heading("state", text="Status")
        self.tree.heading("what", text="Used for")
        self.tree.heading("path", text="Location")
        self.tree.column("#0", width=90, stretch=False)
        self.tree.column("state", width=80, stretch=False)
        self.tree.column("what", width=330)
        self.tree.column("path", width=260)
        self.tree.grid(row=0, column=0, columnspan=3, sticky="nsew")
        self.tree.tag_configure("bad", foreground="#b03a3a")

        buttons = ttk.Frame(tools_box)
        buttons.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(PAD, 0))
        self.download_btn = ttk.Button(buttons, text="Download what's missing",
                                       command=self.download)
        self.download_btn.pack(side="left")
        ttk.Button(buttons, text="Locate the selected tool...",
                   command=self.locate).pack(side="left", padx=6)
        self.tool_note = ttk.Label(buttons, text="", foreground="#666")
        self.tool_note.pack(side="left", padx=6)

        self.load()

    # ---- settings <-> widgets ---------------------------------------------

    def load(self):
        s = self.app.settings
        self._loading = True
        self.base.set(s.base_game)
        self.venue.set(s.venue_dir)
        self.work.set(s.work)
        self.tmp.set(s.tmp)
        self.iso.set(s.out_iso)
        self.jobs_var.set(s.jobs)
        self.demos_var.set(s.drop_demos)
        self.folder_var.set(s.disc_folder)
        self.suggested_folder = iso.folder_beside(s.out_iso)
        self.folder.set(s.disc_folder_path or self.suggested_folder)
        self.folder.enable(s.disc_folder)
        self.disc_var.set(_closest(DISC_PRESETS, s.ceiling_bytes))
        self.video_var.set(_closest(VIDEO_PRESETS, s.video_kbps))
        self._loading = False
        self.refresh_tools()

    def push(self):
        """Copy what the user typed into the settings and save."""
        if getattr(self, "_loading", False):
            return
        s = self.app.settings
        s.base_game = self.base.get()
        s.venue_dir = self.venue.get()
        s.work = self.work.get()
        s.tmp = self.tmp.get()
        s.out_iso = self.iso.get()
        try:
            s.jobs = max(1, int(self.jobs_var.get()))
        except (tk.TclError, ValueError):
            pass
        s.drop_demos = bool(self.demos_var.get())
        s.disc_folder = bool(self.folder_var.get())
        self.folder.enable(s.disc_folder)
        # The folder follows the ISO's name until the user names it themselves,
        # so choosing a different ISO moves it too.
        suggested = iso.folder_beside(self.iso.get())
        typed = self.folder.get()
        if typed != suggested and (not typed or typed == self.suggested_folder):
            self.folder.set(suggested)
            typed = suggested
        self.suggested_folder = suggested
        s.disc_folder_path = "" if typed == suggested else typed
        for name, value in DISC_PRESETS:
            if name == self.disc_var.get():
                s.ceiling_bytes = value
        for name, value in VIDEO_PRESETS:
            if name == self.video_var.get():
                s.video_kbps = value
        s.save()
        self.app.settings_changed()

    # ---- tools -------------------------------------------------------------

    def refresh_tools(self):
        self.tree.delete(*self.tree.get_children())
        pending = 0
        for tool, path, ok, detail in tools.check_all(self.app.settings):
            if not ok:
                pending += 1
            self.tree.insert("", "end", iid=tool.key, text=tool.key,
                             values=(detail if ok else detail,
                                     tool.purpose,
                                     path or tool.source),
                             tags=() if ok else ("bad",))
        missing_downloadable = [k for k in tools.missing(self.app.settings)
                                if tools.BY_KEY[k].downloadable]
        self.download_btn.state(["!disabled"] if missing_downloadable
                                else ["disabled"])
        manual = [k for k in tools.missing(self.app.settings)
                  if not tools.BY_KEY[k].downloadable]
        self.tool_note.config(
            text="" if not manual else
            "%s must be supplied by hand - select it and choose Locate."
            % ", ".join(manual))
        self.app.settings_changed()

    def locate(self):
        key = self.tree.focus()
        if not key:
            messagebox.showinfo("Locate a tool",
                                "Select a tool in the list first.")
            return
        tool = tools.BY_KEY[key]
        picked = filedialog.askopenfilename(
            title="Find %s" % tool.exe,
            filetypes=[(tool.exe, tool.exe), ("Programs", "*.exe")])
        if not picked:
            return
        ok, why = tools.validate(tool, picked)
        if not ok:
            messagebox.showerror("That does not look right",
                                 "%s: %s" % (os.path.basename(picked), why))
            return
        self.app.settings.tools[key] = os.path.normpath(picked)
        self.app.settings.save()
        self.refresh_tools()

    def download(self):
        keys = [k for k in tools.missing(self.app.settings)
                if tools.BY_KEY[k].downloadable]
        if not keys:
            return
        self.download_btn.state(["disabled"])
        self.dialog = tk.Toplevel(self)
        self.dialog.title("Downloading tools")
        self.dialog.transient(self.winfo_toplevel())
        self.dialog.resizable(False, False)
        self.dialog_label = ttk.Label(self.dialog, text="Starting...", width=52)
        self.dialog_label.pack(padx=PAD, pady=(PAD, 4), anchor="w")
        self.dialog_bar = ttk.Progressbar(self.dialog, length=380,
                                          mode="indeterminate")
        self.dialog_bar.pack(padx=PAD, pady=(0, PAD))
        self.dialog_bar.start(20)

        # The download runs on its own thread and reports through the window's
        # event queue, because Tk objects belong to the main thread only.
        post = self.app.post

        def work():
            try:
                tools.install(
                    keys, self.app.settings,
                    progress=lambda done, total: post(
                        "tool_status",
                        "Downloading... %s" % _size(done, total)),
                    status=lambda text: post("tool_status", text))
                post("tool_done", None)
            except Exception as exc:
                post("tool_done", str(exc))

        threading.Thread(target=work, daemon=True).start()

    def download_status(self, text):
        if getattr(self, "dialog_label", None):
            self.dialog_label.config(text=text)

    def download_finished(self, error):
        if getattr(self, "dialog", None):
            self.dialog.destroy()
            self.dialog = None
        self.refresh_tools()
        if error:
            messagebox.showerror("Could not download everything",
                                 "%s\n\nYou can still fetch it yourself and "
                                 "point at it with Locate." % error)


def _size(done, total):
    if total:
        return "%.0f%% of %.0f MB" % (100.0 * done / total, total / 1e6)
    return "%.0f MB so far" % (done / 1e6)


def _closest(presets, value):
    """The preset label matching a stored value, or the first as a fallback."""
    for name, preset in presets:
        if preset == value:
            return name
    return presets[0][0]
