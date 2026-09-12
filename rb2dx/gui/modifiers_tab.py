"""The Modifiers page: which of them the disc starts with switched on."""

import tkinter as tk
from tkinter import ttk

from .. import modifiers as modifiers_mod
from .common import PAD, ScrollFrame, Section

# The two lines above the list, which are the whole reason the page exists.
LEAD = ("A PlayStation 2 forgets which modifiers were on as soon as it is switched "
        "off, so anything ticked here is switched on again every time this disc "
        "starts.")
ALSO = ("They are still ordinary modifiers: the game's own Modifiers screen can turn "
        "any of them off for the rest of the session.")


class ModifiersTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=PAD)
        self.app = app
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        scroll = ScrollFrame(self)
        scroll.grid(row=0, column=0, sticky="nsew")
        page = scroll.body
        page.columnconfigure(0, weight=1)

        about = ttk.Label(page, text="%s %s" % (LEAD, ALSO), foreground="#444",
                          justify="left")
        about.grid(row=0, column=0, sticky="ew", pady=(0, PAD))
        # The same trick the settings notes use: a label wraps only where it is told
        # to, so it is told again whenever the window changes width.
        about.bind("<Configure>",
                   lambda e, w=about: w.cget("wraplength") != e.width - 2 * PAD
                   and w.configure(wraplength=max(e.width - 2 * PAD, 200)))

        self.vars = {}
        row = 1
        for whose in (modifiers_mod.GAME, modifiers_mod.DELUXE):
            group = Section(page, whose[0].upper() + whose[1:])
            group.grid(row=row, column=0, sticky="ew", pady=(0, PAD))
            row += 1
            for mod in modifiers_mod.CATALOGUE:
                if mod.whose != whose:
                    continue
                var = tk.BooleanVar(value=False)
                self.vars[mod.name] = var
                group.add_row("", ttk.Checkbutton(group, text=mod.label,
                                                  variable=var),
                              hint=_note(mod))
                var.trace_add("write", lambda *_: self.push())

        self.note = ttk.Label(page, text="", foreground="#8a5a00",
                              justify="left")
        self.note.grid(row=row, column=0, sticky="ew")

        self.load()

    # ---- settings <-> widgets ---------------------------------------------

    def load(self):
        self._loading = True
        chosen = set(self.app.settings.modifiers)
        for name, var in self.vars.items():
            var.set(name in chosen)
        self._loading = False
        self.retell()

    def push(self):
        if getattr(self, "_loading", False):
            return
        s = self.app.settings
        s.modifiers = modifiers_mod.known([name for name, var in self.vars.items()
                                           if var.get()])
        s.save()
        self.retell()
        self.app.settings_changed()

    def retell(self):
        """What the ticks add up to, where that is not simply what was ticked."""
        ends_on, notes = modifiers_mod.outcome(self.app.settings.modifiers)
        lines = ["%s%s." % (line[0].upper(), line[1:]) for line in notes]
        unsaved = [modifiers_mod.label(n) for n in ends_on
                   if modifiers_mod.SAVING in modifiers_mod.BY_NAME[n].costs]
        if unsaved:
            lines.append("Nothing is saved while %s on, so this disc will not keep a "
                         "score until %s turned off."
                         % ("%s is" % _list(unsaved) if len(unsaved) == 1
                            else "%s are" % _list(unsaved),
                            "it is" if len(unsaved) == 1 else "they are"))
        self.note.config(text="\n".join(lines))


def _note(mod):
    """What is written under one tick: the game's own words, then what it costs."""
    # Some of the game's own notes end in nothing at all, and what follows them here
    # would run straight on from the last word.
    parts = [mod.note if mod.note[-1] in ".!?" else mod.note + "."]
    if mod.costs:
        parts.append("Turns %s off while it is on." % _list(list(mod.costs)))
    if mod.on_deactivates:
        parts.append("Switches %s off."
                     % _list([modifiers_mod.label(n) for n in mod.on_deactivates]))
    if mod.on_activates:
        parts.append("Switches %s on as well."
                     % _list([modifiers_mod.label(n) for n in mod.on_activates]))
    return " ".join(parts)


def _list(words):
    if len(words) < 2:
        return "".join(words)
    return "%s and %s" % (", ".join(words[:-1]), words[-1])
