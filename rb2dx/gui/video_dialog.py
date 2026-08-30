"""Moving the video a song folder brought with it, from the Songs page.

A clip shorter than the song plays round and round behind it, and where it starts
is a matter of taste, so it is set by hand here and remembered per folder. Where
the folder holds the song's actual music video there is a right answer instead, and
listening for it is a button: see align.

Either way the number means nothing on its own, so both sides are drawn as waveforms
to pull against each other, the frame the song opens on is shown beside them, and the
pair can be played out loud from where the video starts - the clip in one ear against
the song in the other, going round until the two agree, which is how anyone tells
whether a picture belongs where it has been put. A few seconds of the finished thing
can be watched before any of it is built.
"""

import os
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from .. import align, audio, sound, video
from . import waveform
from .common import PAD, later, mmss

# What the small buttons either side of the number move it by.
STEPS = (-1.0, -0.25, 0.25, 1.0)


def _sentence(text):
    return text[:1].upper() + text[1:] + "."


class VideoDialog(tk.Toplevel):
    def __init__(self, parent, app, song):
        super().__init__(parent)
        self.app = app
        self.song = song
        self.working = False
        self.title("Line up the video")
        self.transient(parent)
        # Wider is allowed, and useful: the waves stretch with it. Taller buys
        # nothing, as everything in here has the height it needs.
        self.resizable(True, False)

        settings = app.settings
        self.clip = video.song_video(song.path)
        self.clip_secs = video.clip_seconds(settings, self.clip)
        self.loops = bool(self.clip_secs and self.clip_secs < song.seconds)
        self.value = tk.StringVar(value="%.2f" % settings.nudge(song.path))
        self.at = tk.StringVar(value="0.00")
        self.frame_shown = None
        self.pending = None
        self.follow = True        # keep the preview at the video's own start
        self.song_pcm = None      # both sides in memory, to play any stretch of
        self.clip_pcm = None
        self.player = sound.Player(settings.tmp_dir("watch"))
        self.since = 0.0          # when the stretch now playing was started
        self.play_from = 0.0      # the song second that stretch started at
        self.ticking = None
        self.looping = tk.BooleanVar(value=True)
        # The disc's frame is 400 across. A screen with less height than this
        # window wants gets a smaller one, and the waves get the width it saves.
        self.frame_wide = video.WIDTH if self.room()[1] >= 760 else 260

        body = ttk.Frame(self, padding=PAD)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        ttk.Label(body, text=song.label, font="-weight bold").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text=self._describe(), foreground="#666",
                  wraplength=700, justify="left").grid(row=1, column=0,
                                                       columnspan=2, sticky="w",
                                                       pady=(2, PAD))

        # The waves and the frame side by side: the waves are what you drag, the
        # frame is what the drag does.
        middle = ttk.Frame(body)
        middle.grid(row=2, column=0, columnspan=2, sticky="nsew")
        middle.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        waves = ttk.LabelFrame(middle, text=" The song, and the video's audio ",
                               padding=6)
        waves.grid(row=0, column=0, sticky="nsew")
        waves.columnconfigure(0, weight=1)
        # Whatever is left beside the frame the disc will show, within reason: the
        # wider the waves, the finer a drag lands.
        self.waves = waveform.WaveView(
            waves, on_change=self.dragged, on_seek=self.seeked,
            on_mute=self.remix, track=waveform.TRACK,
            width=max(320, min(900, self.room()[0] - self.frame_wide - 200)))
        self.waves.grid(row=0, column=0, sticky="nsew")

        hear = ttk.Frame(waves)
        hear.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.play_btn = ttk.Button(hear, text="Play", width=8,
                                   command=self.play)
        self.play_btn.grid(row=0, column=0)
        # Nothing to play until both sides have been read, which takes a moment.
        self.play_btn.state(["disabled"])
        self.stop_btn = ttk.Button(hear, text="Stop", width=6, command=self.hush)
        self.stop_btn.grid(row=0, column=1, padx=4)
        self.stop_btn.state(["disabled"])
        ttk.Checkbutton(hear, text="round and round", variable=self.looping,
                        command=self.remix).grid(row=0, column=2, padx=(PAD, 0))
        self.hear_note = ttk.Label(hear, text="", foreground="#666")
        self.hear_note.grid(row=0, column=3, padx=(PAD, 0), sticky="w")
        hear.columnconfigure(3, weight=1)

        # Where the sound was stopped is the whole answer when the stopping was
        # done on hearing something, so it is kept and can be snapped to.
        snap = ttk.Frame(waves)
        snap.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self.snap_note = ttk.Label(snap, text="", foreground="#666")
        self.snap_note.grid(row=0, column=0, sticky="w")
        self.mark_btn = ttk.Button(snap, text="Snap the marker there",
                                   command=self.snap_marker)
        self.mark_btn.grid(row=0, column=1, padx=(PAD, 4))
        self.start_btn = ttk.Button(snap, text="Start the video there",
                                    command=self.snap_video)
        self.start_btn.grid(row=0, column=2)
        snap.columnconfigure(0, weight=1)
        self.stopped_at = None
        self._offer_snap()

        seen = ttk.Frame(middle)
        seen.grid(row=0, column=1, sticky="n", padx=(PAD, 0))
        self.picture = ttk.Label(seen, relief="sunken", anchor="center",
                                 width=30, text="\n\n\n\n")
        self.picture.pack()
        self.caption = ttk.Label(seen, text="", foreground="#666",
                                 wraplength=self.frame_wide, justify="left")
        self.caption.pack(pady=(3, 0))

        row = ttk.Frame(body)
        row.grid(row=3, column=0, columnspan=2, sticky="w", pady=(PAD, 0))
        ttk.Label(row, text="Start this far in").pack(side="left",
                                                      padx=(0, 6))
        for step in STEPS[:2]:
            ttk.Button(row, text="%+g" % step, width=5,
                       command=lambda d=step: self.move(d)).pack(side="left")
        self.spin = ttk.Spinbox(row, textvariable=self.value, width=9,
                                from_=-max(self.clip_secs, 600.0),
                                to=max(self.clip_secs, 600.0), increment=0.25,
                                format="%.2f")
        self.spin.pack(side="left", padx=4)
        for step in STEPS[2:]:
            ttk.Button(row, text="%+g" % step, width=5,
                       command=lambda d=step: self.move(d)).pack(side="left")
        ttk.Label(row, text="seconds").pack(side="left", padx=(6, 0))

        ttk.Label(body, text=self._explain(), foreground="#666", wraplength=700,
                  justify="left").grid(row=4, column=0, columnspan=2, sticky="w",
                                       pady=(6, PAD))

        ear = ttk.Frame(body)
        ear.grid(row=5, column=0, columnspan=2, sticky="ew")
        ear.columnconfigure(1, weight=1)
        self.ear_btn = ttk.Button(ear, text="Find it automatically",
                                  command=self.detect)
        self.ear_btn.grid(row=0, column=0, sticky="w")
        self.ear_note = ttk.Label(ear, text="", foreground="#666",
                                  wraplength=560, justify="left")
        self.ear_note.grid(row=0, column=1, sticky="w", padx=(PAD, 0))

        show = ttk.Frame(body)
        show.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.watch_btn = ttk.Button(show, text="Watch %d seconds"
                                    % video.WATCH_SECS, command=self.watch)
        self.watch_btn.grid(row=0, column=0, sticky="w")
        ttk.Label(show, text="from").grid(row=0, column=1, padx=(PAD, 4))
        self.from_spin = ttk.Spinbox(show, textvariable=self.at, width=8,
                                     from_=0, to=max(song.seconds, 1),
                                     increment=5, format="%.2f")
        self.from_spin.grid(row=0, column=2)
        ttk.Label(show, text="seconds into the song, which follows the video's "
                            "own start until you click the timeline",
                  foreground="#666").grid(row=0, column=3, padx=(4, 0))
        self.watch_note = ttk.Label(body, text="", foreground="#666",
                                    wraplength=700, justify="left")
        self.watch_note.grid(row=7, column=0, columnspan=2, sticky="w",
                             pady=(6, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=8, column=0, columnspan=2, sticky="e", pady=(PAD, 0))
        ttk.Button(buttons, text="Leave it alone", width=13,
                   command=self.destroy).pack(side="right")
        self.ok_btn = ttk.Button(buttons, text="Use this", width=10,
                                 command=self.accept)
        self.ok_btn.pack(side="right", padx=6)
        ttk.Button(buttons, text="Back to the start", width=15,
                   command=lambda: self.show(0.0)).pack(side="right")

        self.bind("<Return>", lambda e: self.accept())
        self.bind("<Escape>", lambda e: self.destroy())
        # The space bar plays and stops, as it does everywhere else that has a
        # waveform on it. Text boxes keep it for typing.
        self.bind("<space>", self._space)
        self.spin.focus_set()
        self.grab_set()

        for event in ("<Key>", "<<Increment>>", "<<Decrement>>"):
            self.from_spin.bind(event, lambda e: setattr(self, "follow", False))
        self.value.trace_add("write", lambda *_: self.moved())
        self.at.trace_add("write", lambda *_: self.retimed())
        self.moved()
        self.fit()
        self.read_audio()

    # ---- making it fit the screen -------------------------------------------

    def room(self):
        """What the screen leaves for a window, once its frame is allowed for."""
        return (self.winfo_screenwidth() - 60, self.winfo_screenheight() - 120)

    def fit(self):
        """Shrink the waves until the buttons at the bottom are on the screen.

        A window taller than the screen puts *Use this* somewhere nobody can click,
        and the waves are the only part with height to spare, so they give it up
        until the rest fits.
        """
        wide_room, tall_room = self.room()
        self.update_idletasks()
        while self.winfo_reqheight() > tall_room and self.waves.track > 34:
            self.waves.set_track(self.waves.track - 12)
            self.update_idletasks()
        # Sit near the top left of whatever is behind, never off any edge.
        parent = self.master.winfo_toplevel()
        x = max(20, min(parent.winfo_rootx() + 20,
                        wide_room - self.winfo_reqwidth() + 40))
        y = max(10, min(parent.winfo_rooty() + 10,
                        tall_room - self.winfo_reqheight() + 60))
        self.geometry("+%d+%d" % (x, y))

    # ---- what the dialog says ----------------------------------------------

    def _describe(self):
        if not self.clip:
            return "This song's folder has no video in it."
        name = self.clip.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        if not self.clip_secs:
            return "%s, behind a %s song." % (name, mmss(self.song.seconds))
        if self.loops:
            times = self.song.seconds / self.clip_secs
            return ("%s runs %s behind a %s song, so it plays through %.1f times."
                    % (name, mmss(self.clip_secs), mmss(self.song.seconds),
                       times))
        spare = self.clip_secs - self.song.seconds
        if spare < 5.0:
            return ("%s runs %s and the song is %s, so it plays once, near enough "
                    "end to end." % (name, mmss(self.clip_secs),
                                     mmss(self.song.seconds)))
        return ("%s runs %s and the song is %s, so it plays once and the last %s "
                "is never seen." % (name, mmss(self.clip_secs),
                                    mmss(self.song.seconds), mmss(spare)))

    def _explain(self):
        if self.loops:
            return ("The clip is shorter than the song and plays round and round, "
                    "so this moves where it starts without cutting anything out "
                    "of it. Go backwards and it wraps to the end of the clip.")
        return ("Forwards starts that far into the video, and it comes round again "
                "if it runs out before the song ends. Backwards holds it back, "
                "showing black until it begins.")

    # ---- setting the number ------------------------------------------------

    def move(self, delta):
        self.show(self.current() + delta)

    def show(self, seconds):
        self.value.set("%.2f" % round(seconds, 2))

    def current(self):
        try:
            return float(self.value.get())
        except (tk.TclError, ValueError):
            return 0.0

    def watch_from(self):
        """Which second of the song the picture is being judged at."""
        try:
            return max(0.0, min(float(self.at.get()), self.song.seconds))
        except (tk.TclError, ValueError):
            return 0.0

    def video_starts(self):
        """The song time the clip's own first frame lands on."""
        start, delay = video.offsets(self.app.settings, self.clip, self.current(),
                                     self.song.seconds)
        if not self.loops or not self.clip_secs:
            return delay
        return delay + (self.clip_secs - start) % self.clip_secs

    # ---- the waveforms ------------------------------------------------------

    def read_audio(self):
        """Read both sides in the background: once for the eye, once for the ear."""
        settings, song, clip = self.app.settings, self.song, self.clip
        seconds = max(song.seconds, 1.0) + 1.0
        clip_secs = max(self.clip_secs, 1.0) + 1.0
        self.hear_note.config(text="reading both ...")

        def work():
            try:
                theirs = align.song_shape(settings, song.path, seconds)
                mine = align.shape(settings, ["-i", clip], clip_secs)
            except Exception:
                theirs = mine = None
            later(self, lambda: self.drawn(theirs, mine))
            # The samples themselves take longer to read and are only wanted when
            # something is played, so the drawing does not wait on them.
            try:
                song_pcm = sound.read(settings, align.stems_args(song.path) or [],
                                      seconds)
                clip_pcm = sound.read(settings, ["-i", clip], clip_secs)
            except Exception:
                song_pcm = clip_pcm = None
            later(self, lambda: self.listened(song_pcm, clip_pcm))

        threading.Thread(target=work, daemon=True).start()

    def drawn(self, song_shape, clip_shape):
        self.waves.show(song_shape, clip_shape, align.DRAW_HZ,
                        self.song.seconds, self.clip_secs)
        self.moved()

    def listened(self, song_pcm, clip_pcm):
        self.song_pcm, self.clip_pcm = song_pcm, clip_pcm
        if not sound.can_play():
            self.hear_note.config(text="playing out loud needs Windows")
            self.play_btn.state(["disabled"])
            return
        if song_pcm is None and clip_pcm is None:
            self.hear_note.config(text="neither side could be read")
            self.play_btn.state(["disabled"])
            return
        self.play_btn.state(["!disabled"])
        silent = clip_pcm is None or not len(clip_pcm)
        self.hear_note.config(text="this video has no audio of its own" if silent
                              else self._waiting())

    def dragged(self, offset):
        """The waveform was pulled: take its number as the nudge."""
        self.show(offset)

    def seeked(self, when):
        """The timeline was clicked: watch from there instead."""
        self.follow = False
        self.at.set("%.2f" % when)

    def moved(self):
        """Bring everything into line with whatever the number now says."""
        if self.follow:
            # Watching from where the video begins is what anyone wants to check
            # first, so the marker rides along with it until it is moved by hand.
            self.at.set("%.2f" % self.video_starts())
        self.retimed()

    def retimed(self):
        """Redraw for a new nudge or a new place to start from."""
        self.waves.place(self.current(), self.loops, self.video_starts(),
                         self.watch_from())
        if not self.player.playing:
            # Nothing playing: the line waits at the marker, wherever that now is.
            self.play_from = self.watch_from()
            self.waves.playing(self.play_from)
        self.repaint()

    # ---- hearing it ---------------------------------------------------------

    def play(self, from_when=None):
        """Play the two sides, the clip in the left ear and the song in the right.

        From the marker unless told otherwise: a switch moved mid-flight asks for
        where the ear had got to, so muting a side does not lose your place.
        """
        if self.song_pcm is None and self.clip_pcm is None:
            return
        when = self.watch_from() if from_when is None else from_when
        start, delay = video.offsets(self.app.settings, self.clip, self.current(),
                                     self.song.seconds)
        # Where the clip's own audio is by then, read round and round if that is
        # what the disc will do with it.
        played = self.player.play(
            *sound.pair(self.song_pcm, self.clip_pcm, when,
                        start + max(when - delay, 0.0),
                        wrap=self.clip_secs if self.loops else 0.0,
                        hush=max(delay - when, 0.0),
                        want_song=not self.waves.mute_song.get(),
                        want_clip=not self.waves.mute_clip.get()),
            loop=self.looping.get())
        if not played:
            self.hear_note.config(text="this machine would not play it")
            return
        self.since = time.monotonic()
        self.play_from = when
        self.stop_btn.state(["!disabled"])
        self.hear_note.config(text=self._hearing())
        self._tick()

    def _hearing(self):
        """Which sides are coming out of the speakers, in so many words."""
        sides = {(True, True): "both", (True, False): "the song only",
                 (False, True): "the video only",
                 (False, False): "nothing - both are muted"}[
            (not self.waves.mute_song.get(), not self.waves.mute_clip.get())]
        return "playing %s from %s" % (sides, mmss(self.play_from))

    def _waiting(self):
        """What is on offer while nothing is playing."""
        muted = [name for name, var in (("the song", self.waves.mute_song),
                                        ("the video", self.waves.mute_clip))
                 if var.get()]
        return "%g seconds from the marker%s" % (
            sound.PLAY_SECS, ", %s muted" % " and ".join(muted) if muted else "")

    def _space(self, event):
        if isinstance(event.widget, (ttk.Spinbox, ttk.Entry)):
            return None
        self.hush() if self.player.playing else self.play()
        return "break"

    def reached(self):
        """The song second the ear has got to, playing or not."""
        if not self.player.playing:
            return self.play_from
        gone = (time.monotonic() - self.since) % sound.PLAY_SECS
        return self.play_from + gone

    def hush(self):
        """Silence, and the line back to where playing began.

        Where it had got to is remembered first: stopping on hearing something is
        how the moment gets found, and losing it would waste the listening.
        """
        if self.player.playing:
            self.stopped_at = self.reached()
        self.player.stop()
        if self.ticking:
            self.after_cancel(self.ticking)
            self.ticking = None
        self.stop_btn.state(["disabled"])
        self.play_from = self.watch_from()
        self.waves.playing(self.play_from)
        self.waves.stopped(self.stopped_at)
        self.hear_note.config(text=self._waiting())
        self._offer_snap()

    def _offer_snap(self):
        """Show what was stopped on, and let it be used, once there is one."""
        there = self.stopped_at
        self.snap_note.config(
            text="" if there is None else
            "stopped at %s (%.2f s)" % (mmss(there), there))
        for button in (self.mark_btn, self.start_btn):
            button.state(["disabled"] if there is None else ["!disabled"])

    def snap_marker(self):
        """Start playing from where the sound was stopped."""
        if self.stopped_at is None:
            return
        self.seeked(self.stopped_at)

    def snap_video(self):
        """Put the video's own first frame where the sound was stopped.

        A clip that plays round and round has no beginning of its own on the disc,
        only the point each pass starts at, so the number that puts a pass there is
        worked back from where the stopping happened.
        """
        there = self.stopped_at
        if there is None:
            return
        if self.loops and self.clip_secs:
            self.show(-(there % self.clip_secs))
        else:
            self.show(-there)

    def remix(self):
        """A mute or the loop moved: carry on from where the ear had got to."""
        if self.player.playing:
            self.play(self.reached())
        else:
            self.hear_note.config(text=self._waiting())
        self.waves.draw()

    def replay(self):
        """Start the stretch again, for a change that moves where it should be."""
        if self.player.playing:
            self.play()

    def _tick(self):
        """Walk a line along the wave for as long as the sound lasts."""
        if not self.player.playing:
            return
        gone = time.monotonic() - self.since
        if gone >= sound.PLAY_SECS and not self.looping.get():
            self.hush()
            return
        self.waves.playing(self.reached())
        self.ticking = self.after(60, self._tick)

    def clip_at(self, when):
        """Where in the clip the disc is by then, or None while it plays black."""
        start, delay = video.offsets(self.app.settings, self.clip,
                                     self.current(), self.song.seconds)
        if when < delay:
            return None
        if not self.clip_secs:
            return 0.0
        return (start + when - delay) % self.clip_secs

    # ---- seeing and hearing it ---------------------------------------------

    def repaint(self):
        """Fetch the frame on screen at that moment, once the typing settles."""
        if self.pending:
            self.after_cancel(self.pending)
        self.pending = self.after(350, self._repaint_now)

    def _repaint_now(self):
        self.pending = None
        # Whatever moved, what is playing is now the wrong stretch: start it again,
        # once the dragging or typing has settled.
        self.replay()
        when = self.watch_from()
        where = self.clip_at(when)
        self.caption.config(
            text="black at %s" % mmss(when) if where is None else
            "%s into the song, which is %s into the clip"
            % (mmss(when), mmss(where)))
        settings, clip = self.app.settings, self.clip
        shot = os.path.join(settings.tmp_dir("watch"), "frame.png")

        wide = self.frame_wide

        def work():
            try:
                if where is None:
                    ok = video.black_still(settings, shot, wide)
                else:
                    ok = video.still(settings, clip, shot, where, wide)
            except Exception:
                ok = False
            later(self, lambda: self._painted(shot if ok else ""))

        threading.Thread(target=work, daemon=True).start()

    def _painted(self, path):
        if not path:
            self.picture.config(text="(this frame could not be read)", image="")
            return
        try:
            # Held on the dialog: Tk drops an image nothing refers to, and the
            # label would go blank a moment after it was filled.
            self.frame_shown = tk.PhotoImage(file=path)
        except tk.TclError:
            return
        self.picture.config(image=self.frame_shown, text="")

    def watch(self):
        """Render a few seconds as the disc will play it and open it."""
        if self.working:
            return
        self.working = True
        self.watch_btn.state(["disabled"])
        self.watch_note.config(text="Putting a preview together ...",
                               foreground="#666")
        settings, song, clip = self.app.settings, self.song, self.clip
        when = self.watch_from()
        where = self.clip_at(when) or 0.0
        stems = sorted(audio.stems_in(song.path).values())
        out = os.path.join(settings.tmp_dir("watch"),
                           "%s-preview.mp4" % (song.sid or "song"))

        def work():
            try:
                heard = video.watch(settings, clip, out, where, stems, when)
                trouble = ""
            except Exception as exc:
                heard, trouble = False, str(exc)
            later(self, lambda: self.watched(out, heard, trouble))

        threading.Thread(target=work, daemon=True).start()

    def watched(self, path, heard, trouble):
        self.working = False
        self.watch_btn.state(["!disabled"])
        if trouble:
            self.watch_note.config(text=trouble, foreground="#8a5a00")
            return
        self.watch_note.config(
            text="Playing in your video player: the clip's own audio in the left "
                 "ear, the song in the right." if heard else
                 "Playing in your video player. This clip has no audio of its "
                 "own, so you are hearing the song only.",
            foreground="#2c6e3f")
        try:
            os.startfile(path)
        except OSError as exc:
            messagebox.showinfo("Could not open it",
                                "The preview is at %s\n\n%s" % (path, exc))

    def detect(self):
        """Find the offset by matching the video's own audio to the song's."""
        if self.working:
            return
        self.working = True
        self.ear_btn.state(["disabled"])
        self.ear_note.config(text="Comparing the two ...", foreground="#666")
        settings, song, clip = self.app.settings, self.song, self.clip

        def work():
            try:
                found, fit, trouble, note = align.match(settings, song.path, clip)
            except Exception as exc:
                found, fit, trouble, note = 0.0, 0.0, str(exc), ""
            later(self, lambda: self.detected(found, fit, trouble, note))

        threading.Thread(target=work, daemon=True).start()

    def detected(self, found, fit, trouble, note):
        self.working = False
        self.ear_btn.state(["!disabled"])
        if trouble:
            # The number it came back with means nothing in this case, so the one
            # the user has is left as it is.
            self.ear_note.config(text=_sentence(trouble), foreground="#8a5a00")
            return
        self.show(found)
        said = _sentence(align.describe(found, fit))
        # An answer that comes with a caveat is still used, and says so in the
        # colour of a warning rather than of a job done.
        self.ear_note.config(text=said + " " + _sentence(note) if note else said,
                             foreground="#8a5a00" if note else "#2c6e3f")
        # Two sentences take a line the one did not, which can push the buttons off
        # the bottom of a small screen.
        self.fit()

    def accept(self):
        settings = self.app.settings
        settings.set_nudge(self.song.path, self.current())
        settings.save()
        self.destroy()
        self.app.songs_tab.redraw()
        self.app.settings_changed()

    def destroy(self):
        # Windows plays on happily after the window it belongs to has gone.
        self.hush()
        super().destroy()
