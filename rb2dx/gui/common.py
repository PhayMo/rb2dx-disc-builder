"""Small shared pieces of the interface."""

import os
import subprocess
import tkinter as tk
from tkinter import filedialog, ttk

PAD = 10


def later(widget, fn):
    """Run something on the main thread, quietly giving up if the window is gone.

    Background work outlives a closed window, and a callback arriving after that
    would otherwise print a traceback the user can do nothing about.
    """
    try:
        widget.after(0, fn)
    except (tk.TclError, RuntimeError):
        pass


def human(n):
    """Bytes as the user thinks of them: GB for discs, MB for songs."""
    if n >= 1e9:
        return "%.2f GB" % (n / 1e9)
    if n >= 1e6:
        return "%.0f MB" % (n / 1e6)
    return "%.0f KB" % (n / 1e3)


def mmss(seconds):
    return "%d:%02d" % (int(seconds) // 60, int(seconds) % 60)


class ScrollFrame(ttk.Frame):
    """A frame that scrolls when its contents are taller than the window.

    Needed because display scaling varies wildly: at 300% a page that fits
    comfortably on one machine has no room for its last section on another.
    Put content into .body.
    """

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bar = ttk.Scrollbar(self, orient="vertical",
                                 command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)

        self.body = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window(0, 0, anchor="nw",
                                                 window=self.body)
        self.body.bind("<Configure>", self._contents_changed)
        self.canvas.bind("<Configure>", self._widen)
        for widget in (self.canvas, self.body):
            widget.bind("<MouseWheel>", self._wheel)

    def _contents_changed(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        needed = self.body.winfo_reqheight() > self.canvas.winfo_height()
        if needed:
            self.bar.grid(row=0, column=1, sticky="ns")
        else:
            self.bar.grid_remove()

    def _widen(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)
        self._contents_changed()

    def _wheel(self, event):
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")


class Section(ttk.LabelFrame):
    """A titled group of settings, with a two-column grid inside."""

    def __init__(self, parent, title, **kw):
        super().__init__(parent, text=" %s " % title, padding=PAD, **kw)
        self.columnconfigure(1, weight=1)
        self._row = 0

    def add_row(self, label, widget, extra=None, hint=None):
        ttk.Label(self, text=label).grid(row=self._row, column=0, sticky="w",
                                         padx=(0, PAD), pady=3)
        widget.grid(row=self._row, column=1, sticky="ew", pady=3)
        if extra is not None:
            extra.grid(row=self._row, column=2, sticky="w", padx=(6, 0), pady=3)
        self._row += 1
        if hint:
            ttk.Label(self, text=hint, foreground="#666").grid(
                row=self._row, column=1, columnspan=2, sticky="w",
                pady=(0, 4))
            self._row += 1
        return widget


class PathRow:
    """An entry with a Browse button, bound to a Tk variable.

    The row is built inside the section it belongs to: a widget gridded into a
    container that is not its parent gets positioned in the wrong coordinate
    space and drifts outside the group box.
    """

    def __init__(self, section, label, kind="dir", hint=None,
                 on_change=None, filetypes=None):
        self.var = tk.StringVar()
        self.kind = kind
        self.filetypes = filetypes or [("Disc image", "*.iso"),
                                       ("All files", "*.*")]
        self.on_change = on_change
        frame = ttk.Frame(section)
        frame.columnconfigure(0, weight=1)
        self.entry = ttk.Entry(frame, textvariable=self.var)
        self.entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(frame, text="Browse...", width=10,
                   command=self.browse).grid(row=0, column=1, padx=(6, 0))
        section.add_row(label, frame, hint=hint)
        self.var.trace_add("write", lambda *_: self.on_change and self.on_change())

    def browse(self):
        current = self.var.get()
        start = current if os.path.isdir(current) else os.path.dirname(current)
        if self.kind == "dir":
            picked = filedialog.askdirectory(initialdir=start or None,
                                             mustexist=False)
        else:
            picked = filedialog.asksaveasfilename(
                initialdir=start or None, defaultextension=".iso",
                initialfile=os.path.basename(current) or "custom.iso",
                filetypes=self.filetypes)
        if picked:
            self.var.set(os.path.normpath(picked))

    def get(self):
        return self.var.get().strip()

    def set(self, value):
        self.var.set(value or "")


def reveal(path):
    """Show a file or folder in Explorer."""
    if not path:
        return
    if os.path.isfile(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif os.path.isdir(path):
        subprocess.Popen(["explorer", os.path.normpath(path)])


class UsageBar(tk.Canvas):
    """How full the disc is: the base game, the chosen songs, and the limit."""

    BASE = "#7c8ea0"
    SONGS = "#3f8f4f"
    OVER = "#b03a3a"
    EMPTY = "#e4e6e9"

    def __init__(self, parent, height=26, **kw):
        super().__init__(parent, height=height, highlightthickness=0,
                         background=self.EMPTY, **kw)
        self.base = 0
        self.songs = 0
        self.limit = 1
        self.bind("<Configure>", lambda e: self._draw())

    def show(self, base, songs, limit):
        self.base, self.songs, self.limit = base, songs, max(limit, 1)
        self._draw()

    def _draw(self):
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1:
            return
        scale = width / float(max(self.limit, self.base + self.songs))
        base_w = self.base * scale
        song_w = self.songs * scale
        over = self.base + self.songs > self.limit
        self.create_rectangle(0, 0, base_w, height, fill=self.BASE, width=0)
        self.create_rectangle(base_w, 0, base_w + song_w, height,
                              fill=self.OVER if over else self.SONGS, width=0)
        limit_x = self.limit * scale
        if limit_x < width - 1:
            self.create_line(limit_x, 0, limit_x, height, fill="#333", width=2)
        self.create_text(8, height / 2, anchor="w", fill="white" if base_w > 90
                         else "#333", text="base game %s" % human(self.base))
        self.create_text(width - 8, height / 2, anchor="e", fill="#333",
                         text="%s of %s" % (human(self.base + self.songs),
                                            human(self.limit)))
