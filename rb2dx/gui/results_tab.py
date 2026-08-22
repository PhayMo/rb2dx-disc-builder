"""The Results page: what came out, and what did not."""

import os
import tkinter as tk
from tkinter import ttk

from .. import plan as planner
from .common import PAD, human, reveal


class ResultsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=PAD)
        self.app = app
        self.result = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.rowconfigure(3, weight=1)

        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.headline = ttk.Label(head, text="No disc has been built yet.",
                                 font=("Segoe UI", 11, "bold"))
        self.headline.grid(row=0, column=0, sticky="w")
        self.detail = ttk.Label(head, text="", foreground="#444")
        self.detail.grid(row=1, column=0, sticky="w", pady=(2, 0))

        buttons = ttk.Frame(self)
        buttons.grid(row=1, column=0, sticky="ew", pady=(PAD, 0))
        self.show_btn = ttk.Button(buttons, text="Show the ISO",
                                   command=self.show_iso, state="disabled")
        self.show_btn.pack(side="left")
        self.folder_btn = ttk.Button(buttons, text="Show the disc folder",
                                     command=self.show_folder, state="disabled")
        self.folder_btn.pack(side="left", padx=6)
        self.retry_btn = ttk.Button(buttons, text="Try the failed songs again",
                                    command=self.retry, state="disabled")
        self.retry_btn.pack(side="left", padx=6)

        problems = ttk.LabelFrame(self, text=" Songs left off the disc ",
                                  padding=6)
        problems.grid(row=2, column=0, sticky="nsew", pady=(PAD, 0))
        problems.rowconfigure(0, weight=1)
        problems.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(problems, columns=("stage", "why"),
                                 show="tree headings", height=6)
        self.tree.heading("#0", text="Song")
        self.tree.heading("stage", text="Failed while")
        self.tree.heading("why", text="Reason")
        self.tree.column("#0", width=260)
        self.tree.column("stage", width=200, stretch=False)
        self.tree.column("why", width=460)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(problems, orient="vertical",
                               command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        checks = ttk.LabelFrame(self, text=" Checks ", padding=6)
        checks.grid(row=3, column=0, sticky="nsew", pady=(PAD, 0))
        checks.rowconfigure(0, weight=1)
        checks.columnconfigure(0, weight=1)
        self.report = tk.Text(checks, height=8, wrap="word", relief="flat",
                              font=("Consolas", 9), background="#f6f7f8")
        self.report.grid(row=0, column=0, sticky="nsew")
        self.report.configure(state="disabled")

    def show(self, result):
        self.result = result
        if result.cancelled:
            self.headline.config(text="Build stopped before it finished.")
        elif result.iso:
            self.headline.config(text="Disc ready: %d songs."
                                      % len(result.shipped))
        else:
            self.headline.config(text="Songs built: %d. No ISO was written."
                                      % len(result.shipped))
        bits = ["took %.0f minutes" % (result.seconds / 60.0)]
        if result.iso:
            bits.insert(0, "%s, %s" % (os.path.basename(result.iso),
                                       human(result.iso_bytes)))
        if result.folder:
            bits.append("disc folder written for ImgBurn")
        if result.problems:
            bits.append("%d songs left off" % len(result.problems))
        self.detail.config(text="  -  ".join(bits))

        self.show_btn.state(["!disabled"] if result.iso else ["disabled"])
        self.folder_btn.state(["!disabled"] if result.folder else ["disabled"])
        self.retry_btn.state(["!disabled"] if result.problems else ["disabled"])

        self.tree.delete(*self.tree.get_children())
        for song, stage, reason in result.problems:
            self.tree.insert("", "end", iid=song.path, text=song.label,
                             values=(stage, reason))

        self.report.configure(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("end", "\n".join(result.report)
                           or "No checks were run.")
        self.report.configure(state="disabled")

    def show_iso(self):
        if self.result and self.result.iso:
            reveal(self.result.iso)

    def show_folder(self):
        if self.result and self.result.folder:
            reveal(self.result.folder)

    def retry(self):
        """Forget the recorded failures so the next build attempts them again."""
        if not self.result:
            return
        planner.forget_problems(self.app.settings,
                               [s.path for s, _, _ in self.result.problems])
        self.retry_btn.state(["disabled"])
        self.app.songs_tab.scan(False)
        self.app.select_tab(1)
