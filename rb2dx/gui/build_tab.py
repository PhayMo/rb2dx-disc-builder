"""The Build page: start the run, watch it, stop it."""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ..pipeline import Pipeline
from .common import PAD, human


class BuildTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=PAD)
        self.app = app
        self.pipeline = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        self.start = ttk.Button(top, text="Build the disc", command=self.go)
        self.start.grid(row=0, column=0)
        self.stop = ttk.Button(top, text="Stop", command=self.halt,
                               state="disabled")
        self.stop.grid(row=0, column=1, sticky="w", padx=6)
        self.write_iso = tk.BooleanVar(value=True)
        self.do_verify = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Write the ISO",
                        variable=self.write_iso).grid(row=0, column=2)
        ttk.Checkbutton(top, text="Check it afterwards",
                        variable=self.do_verify).grid(row=0, column=3,
                                                      padx=(PAD, 0))

        self.summary = ttk.Label(self, text="Nothing selected yet.")
        self.summary.grid(row=1, column=0, sticky="w", pady=(PAD, 2))

        progress = ttk.Frame(self)
        progress.grid(row=2, column=0, sticky="ew")
        progress.columnconfigure(0, weight=1)
        self.bar = ttk.Progressbar(progress, mode="determinate")
        self.bar.grid(row=0, column=0, sticky="ew")
        self.count = ttk.Label(progress, text="", width=12, anchor="e")
        self.count.grid(row=0, column=1, padx=(6, 0))
        self.stage = ttk.Label(progress, text="", foreground="#444")
        self.stage.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        box = ttk.LabelFrame(self, text=" Log ", padding=6)
        box.grid(row=3, column=0, sticky="nsew", pady=(PAD, 0))
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.log = tk.Text(box, wrap="none", height=18, font=("Consolas", 9),
                           background="#1d1f21", foreground="#d8d8d8",
                           insertbackground="#d8d8d8", relief="flat")
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set, state="disabled")
        self.log.tag_configure("bad", foreground="#ff8a80")
        self.log.tag_configure("good", foreground="#9fd68a")
        self.log.tag_configure("step", foreground="#8ab4f8")

    # ---- log ---------------------------------------------------------------

    def write(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def describe(self, count, fits, disc_bytes):
        if not count:
            self.summary.config(text="No songs selected. Choose some on the "
                                     "Songs page.")
        else:
            self.summary.config(
                text="%d %s, about %s on the disc.%s"
                % (count, "song" if count == 1 else "songs", human(disc_bytes),
                   "" if fits else "  This will not fit as chosen."))
        self.start.state(["!disabled"] if count and self.pipeline is None
                         else ["disabled"])

    # ---- running -----------------------------------------------------------

    def go(self):
        songs = self.app.songs_tab.selected_songs()
        if not songs:
            messagebox.showinfo("Nothing to build",
                                "Select some songs on the Songs page first.")
            return
        problems = self.app.settings.problems()
        if problems:
            messagebox.showwarning("Setup is not finished",
                                   "\n".join("- " + p for p in problems))
            return

        self.clear()
        self.write("Building %d songs" % len(songs), "step")
        self.bar.config(maximum=len(songs), value=0)
        self.count.config(text="0/%d" % len(songs))
        self.start.state(["disabled"])
        self.stop.state(["!disabled"])

        post = self.app.post
        self.pipeline = Pipeline(
            self.app.settings,
            on_log=lambda text: post("log", text),
            on_progress=lambda done, total: post("progress", (done, total)),
            on_song=lambda label, stage, ok, msg: post("song",
                                                       (label, stage, ok, msg)),
            on_stage=lambda text: post("stage", text))

        def work():
            try:
                result = self.pipeline.build(
                    songs, venue_dir=self.app.settings.venue_dir,
                    make_iso=self.write_iso.get(),
                    do_verify=self.do_verify.get())
                post("done", result)
            except Exception as exc:
                post("failed", str(exc))

        threading.Thread(target=work, daemon=True).start()

    def halt(self):
        if self.pipeline:
            self.pipeline.cancel()
            self.write("Stopping after the songs already running finish...",
                       "bad")
            self.stop.state(["disabled"])

    def finished(self):
        self.pipeline = None
        self.stop.state(["disabled"])
        self.stage.config(text="")
        self.app.selection_refresh()
