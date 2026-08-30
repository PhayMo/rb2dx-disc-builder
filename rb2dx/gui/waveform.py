"""Two waveforms, the video's draggable against the song's.

The song is the fixed thing and sits along the top; the video's own audio sits under
it and is what moves, since moving the video is the whole point. Dragging it is the
only way to line up a clip whose audio nobody can hear from a number in a box.
Dragging the song does not move the song - there is nowhere for it to go - so it
scrolls the view instead, the way grabbing the background does anywhere else.

A clip shorter than the song is drawn as it will play, round and round, with a mark
at every point it starts over. Each track has its name and its own mute down the left,
so one can be silenced while the other is listened to. The strip below the two shows
where the video's picture begins and where playing starts from, and a click there
moves that.
"""

import tkinter as tk
from tkinter import ttk

from .common import PAD, mmss

RULER = 18
# Tall enough to stand beside the frame the disc will show, which is 304 across.
TRACK = 120
GAP = 4
MARGIN = 2
# The strip down the left, outside the waves, holding each track's name and mute.
GUTTER = 78
# How far in the two can be dragged, past which nothing is being lined up any more.
GRAB = 600.0

SONG_FILL = "#3f6f9f"
CLIP_FILL = "#3f8f4f"
QUIET = "#c8cdd2"
SEAM = "#b03a3a"
HEAD = "#d08000"
NOW = "#111111"
STOPPED = "#5a3fa0"
MID = "#8a9099"


class WaveView(ttk.Frame):
    """The song and the clip, drawn against each other and draggable."""

    def __init__(self, parent, on_change=None, on_seek=None, on_mute=None,
                 width=640, track=TRACK):
        super().__init__(parent)
        self.on_change = on_change
        self.on_seek = on_seek
        self.track = track
        self.mute_song = tk.BooleanVar(value=False)
        self.mute_clip = tk.BooleanVar(value=False)
        self.song = None          # loudness per point, 0..1
        self.clip = None
        self.hz = 50.0
        self.song_secs = 0.0
        self.clip_secs = 0.0
        self.offset = 0.0         # the nudge, in seconds
        self.wraps = False        # whether the clip plays round and round
        self.begins = 0.0         # song time the clip's own start lands on
        self.head = 0.0           # where a preview would start
        self.now = None           # where the sound has got to, while playing
        self.stop_mark = None     # where it was last stopped, to snap to
        self.zoom = 1.0
        self.left = 0.0           # song time at the left edge
        self._drag = None

        self.canvas = tk.Canvas(self, height=self.height(), width=width,
                                background="white", highlightthickness=1,
                                highlightbackground="#b0b4b8")
        self.canvas.grid(row=0, column=0, sticky="ew")
        self.columnconfigure(0, weight=1)

        # The mutes live in the gutter beside the track they silence, so which one
        # is being switched off is never in doubt. The widgets are made once; the
        # items that carry them are put back every time the canvas is cleared.
        self.mutes = [ttk.Checkbutton(self.canvas, text="mute", variable=var,
                                      command=on_mute or (lambda: None))
                      for var in (self.mute_song, self.mute_clip)]

        bar = ttk.Frame(self)
        bar.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Label(bar, text="Drag the video's wave to move it. Dragging the song "
                            "scrolls; ctrl and the wheel zooms.",
                  foreground="#666").pack(side="left")
        ttk.Button(bar, text="Zoom out", width=10,
                   command=lambda: self.rezoom(0.5)).pack(side="right")
        ttk.Button(bar, text="Zoom in", width=9,
                   command=lambda: self.rezoom(2.0)).pack(side="right", padx=4)
        self.scale_note = ttk.Label(bar, text="", foreground="#666")
        self.scale_note.pack(side="right", padx=PAD)

        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<MouseWheel>", self._wheel)

    # ---- how much room it takes ---------------------------------------------

    def height(self):
        return RULER + 2 * self.track + GAP + RULER

    def clip_top(self):
        return RULER + self.track + GAP

    def set_track(self, deep):
        """Give each wave a different depth, for a screen with less room."""
        self.track = max(int(deep), 28)
        self.canvas.config(height=self.height())
        self.draw()

    # ---- what it is showing -------------------------------------------------

    def show(self, song, clip, hz, song_secs, clip_secs):
        self.song, self.clip, self.hz = song, clip, float(hz)
        self.song_secs, self.clip_secs = song_secs, clip_secs
        self.draw()

    def place(self, offset, wraps, begins, head=None):
        """Where the clip sits now: the nudge, and what that works out to."""
        self.offset, self.wraps, self.begins = offset, wraps, begins
        if head is not None:
            self.head = head
        self.draw()

    def stopped(self, when):
        """Where the sound was last stopped, kept in view to be snapped to."""
        self.stop_mark = when
        self.canvas.delete("stopped")
        if when is None:
            return
        x = self.x_of(when)
        if GUTTER < x < self.canvas.winfo_width() - MARGIN:
            self.canvas.create_line(x, RULER, x, self.clip_top() + self.track,
                                    fill=STOPPED, dash=(2, 3), tags=("stopped",))
            self.canvas.create_text(x + 3, RULER + 1, anchor="nw", fill=STOPPED,
                                    font="-size 7", text="stopped",
                                    tags=("stopped",))

    def playing(self, when):
        """Where the sound has got to, or None for nothing playing.

        Only the one line moves: redrawing both waveforms sixteen times a second
        would cost more than it shows.
        """
        self.now = when
        self.canvas.delete("now")
        if when is None:
            return
        wide = self.canvas.winfo_width()
        if not MARGIN <= self.x_of(when) <= wide - MARGIN and self.zoom > 1.0:
            # Zoomed in far enough that the sound has run off the edge: bring the
            # window along with it rather than leaving it playing out of sight.
            self.left = when - self.seconds_across() * 0.2
            self.draw()
            return
        x = self.x_of(when)
        if MARGIN < x < wide - MARGIN:
            self.canvas.create_line(x, RULER, x, self.clip_top() + self.track,
                                    fill=NOW, width=1, tags=("now",))

    # ---- geometry -----------------------------------------------------------

    def seconds_across(self):
        return max(self.song_secs, 1.0) / self.zoom

    def across(self):
        """How many pixels the waves themselves have, outside the gutter."""
        return max(self.canvas.winfo_width() - GUTTER - MARGIN, 10)

    def x_of(self, when):
        return GUTTER + (when - self.left) / self.seconds_across() * self.across()

    def time_at(self, x):
        return self.left + (x - GUTTER) / float(self.across()) * \
            self.seconds_across()

    def rezoom(self, by):
        # Zoom towards the marker, which is where the video starts until it is
        # moved, and so the moment anyone zooming in wants a closer look at.
        middle = self.time_at(self.canvas.winfo_width() / 2.0)
        if self.left <= self.head <= self.left + self.seconds_across():
            middle = self.head
        self.zoom = max(1.0, min(self.zoom * by, 240.0))
        self.left = middle - self.seconds_across() / 2.0
        self._settle()
        self.draw()

    def _settle(self):
        """Keep the view over the song rather than off either end of it."""
        self.left = max(0.0, min(self.left,
                                 max(self.song_secs - self.seconds_across(), 0.0)))

    def _wheel(self, event):
        if event.state & 0x0004:      # control held: zoom instead of scroll
            self.rezoom(2.0 if event.delta > 0 else 0.5)
            return
        self.left -= (1 if event.delta > 0 else -1) * self.seconds_across() * 0.15
        self._settle()
        self.draw()

    # ---- drawing ------------------------------------------------------------

    def draw(self):
        self.canvas.delete("all")
        wide = self.canvas.winfo_width()
        if wide <= 1:
            return
        self._settle()
        self.scale_note.config(
            text="%s across" % mmss(self.seconds_across())
            if self.seconds_across() >= 60 else
            "%.1f s across" % self.seconds_across())
        self._ruler()
        # A muted track is drawn in grey: what you are hearing should be plain
        # from the picture, without reading the boxes.
        self._wave(self.song, RULER, lambda t: t if 0 <= t <= self.song_secs
                   else None, QUIET if self.mute_song.get() else SONG_FILL,
                   "song")
        self._wave(self.clip, self.clip_top(), self.clip_at,
                   QUIET if self.mute_clip.get() else CLIP_FILL, "clip")
        self._marks()
        self._gutter()

    def _gutter(self):
        """The names and mutes down the left, put back after a clear."""
        height = self.height()
        self.canvas.create_rectangle(0, 0, GUTTER, height, fill="#f4f5f6",
                                     outline="")
        self.canvas.create_line(GUTTER, 0, GUTTER, height, fill="#d8dadd")
        # A shallow track has room for the name and the mute and nothing else.
        roomy = self.track >= 76
        for row, (name, box) in enumerate((("the song", self.mutes[0]),
                                           ("the video", self.mutes[1]))):
            top = RULER + row * (self.track + GAP)
            middle = top + self.track / 2.0
            self.canvas.create_text(8, middle - (16 if roomy else 11), anchor="w",
                                    text=name, fill="#444")
            self.canvas.create_window(6, middle + (4 if roomy else 8), window=box,
                                      anchor="w")
            if row and roomy:
                self.canvas.create_text(9, middle + 26, anchor="w",
                                        text="drag to move", fill="#8a9099",
                                        font="-size 7")

    def _ruler(self):
        wide = self.canvas.winfo_width()
        across = self.seconds_across()
        step = next(s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300)
                    if across / s <= 12) if across > 1 else 1
        at = int(self.left / step) * step
        while at <= self.left + across:
            x = self.x_of(at)
            if x > GUTTER:
                self.canvas.create_line(x, RULER - 4, x, RULER, fill=MID)
                self.canvas.create_text(x + 2, 1, anchor="nw", fill="#666",
                                        text=mmss(at), font="-size 7")
            at += step
        for top in (RULER, self.clip_top()):
            self.canvas.create_line(GUTTER, top + self.track / 2.0, wide - MARGIN,
                                    top + self.track / 2.0, fill="#e4e6e9")

    def clip_at(self, when):
        """Where in the clip the disc is at that moment, or None for black."""
        if not self.clip_secs:
            return None
        start = self.offset % self.clip_secs if self.wraps else max(self.offset,
                                                                   0.0)
        delay = 0.0 if self.wraps else max(-self.offset, 0.0)
        if when < delay or when > self.song_secs:
            return None
        where = (start + when - delay)
        if self.wraps:
            return where % self.clip_secs
        return where if where <= self.clip_secs else None

    def _wave(self, data, top, where, fill, tag):
        """One track: for every column, how loud whatever is playing there is."""
        if data is None:
            self.canvas.create_text(GUTTER + 8, top + self.track / 2.0, anchor="w",
                                    fill="#999", text="(reading the audio ...)")
            return
        wide = self.canvas.winfo_width()
        mid = top + self.track / 2.0
        room = self.track / 2.0 - 3
        # How much of the curve falls in one column. The average of it rather than
        # the loudest part, or a whole song at once is a solid block: what there is
        # to see from across a song is which stretches are loud, and zooming in
        # brings the beats themselves back a column each.
        step = max(int(self.seconds_across() / max(wide, 1) * self.hz), 1)
        tops, bottoms = [], []
        for x in range(GUTTER + 1, wide - MARGIN):
            when = where(self.time_at(x))
            if when is None:
                continue
            i = int(when * self.hz)
            if not 0 <= i < len(data):
                continue
            level = float(data[i:i + step].mean())
            tops.append((x, mid - level * room))
            bottoms.append((x, mid + level * room))
        if len(tops) >= 2:
            pts = [v for point in tops + bottoms[::-1] for v in point]
            self.canvas.create_polygon(*pts, fill=fill, outline=fill, tags=(tag,))

    def _marks(self):
        """The seams where the clip starts over, and where a preview would begin."""
        height = self.clip_top() + self.track
        if self.clip_secs and self.wraps:
            at = self.begins
            while at > self.left:
                at -= self.clip_secs
            at += self.clip_secs
            while at < self.left + self.seconds_across():
                x = self.x_of(at)
                self.canvas.create_line(x, self.clip_top(), x, height,
                                        fill=SEAM, dash=(3, 2))
                at += self.clip_secs
        if 0 <= self.begins <= self.song_secs:
            x = self.x_of(self.begins)
            self.canvas.create_text(min(x + 3, self.canvas.winfo_width() - 90),
                                    height + 2, anchor="nw", fill=SEAM,
                                    font="-size 7",
                                    text="video starts %s" % mmss(self.begins))
        x = self.x_of(self.head)
        self.canvas.create_line(x, RULER - 6, x, height, fill=HEAD, width=2)
        self.canvas.create_polygon(x - 4, RULER - 10, x + 4, RULER - 10, x,
                                   RULER - 4, fill=HEAD, outline=HEAD)
        if self.stop_mark is not None:
            self.stopped(self.stop_mark)
        if self.now is not None:
            self.playing(self.now)

    # ---- dragging -----------------------------------------------------------

    def _at(self, event):
        """Which part of the canvas a press landed on."""
        if event.x <= GUTTER:
            return "gutter"
        clip_top = self.clip_top()
        if event.y < RULER - 2 or event.y > clip_top + self.track:
            return "when"
        return "clip" if event.y >= clip_top else "song"

    def _press(self, event):
        where = self._at(event)
        if where == "gutter":
            return
        if where == "when":
            # The strips above and below the waves are for choosing where to start.
            self.head = max(0.0, min(self.time_at(event.x), self.song_secs))
            self.draw()
            if self.on_seek:
                self.on_seek(self.head)
            return
        self._drag = (event.x, self.offset, self.left, where)
        self.canvas.configure(cursor="sb_h_double_arrow" if where == "clip"
                              else "fleur")

    def _motion(self, event):
        if not self._drag:
            return
        x0, was, was_left, which = self._drag
        moved = self.time_at(event.x) - self.time_at(x0)
        if which == "song":
            # The song cannot move - it is what everything else is measured
            # against - so a hand on it slides the window along instead.
            self.left = was_left - moved
            self._settle()
            self.draw()
            return
        # Dragging the video to the right puts its picture later against the song,
        # which is the same as taking the number that starts it backwards.
        self.offset = max(-GRAB, min(was - moved, GRAB))
        if self.on_change:
            self.on_change(self.offset)
        else:
            self.draw()

    def _release(self, _event):
        self._drag = None
        self.canvas.configure(cursor="")

    def _hover(self, event):
        """Say what a press would do here, before it is pressed."""
        if self._drag:
            return
        where = self._at(event)
        self.canvas.configure(
            cursor={"clip": "hand2", "song": "fleur", "when": "sb_left_arrow"}
            .get(where, ""))
