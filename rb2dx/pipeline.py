"""Running a build from a list of songs to a finished ISO.

Each song goes through its stages in order - mix the audio, convert the chart,
convert the art, encode the PS2 audio, encode and mux the video - and songs run
in parallel with each other. A song that fails any stage is set aside with the
reason rather than stopping the disc: the build ships what works and reports the
rest.

Finished work is remembered. Every staged song carries a stamp of what it was
built from, so adding one song to a disc of a hundred only builds that one.
"""

import concurrent.futures
import json
import os
import threading
import time
import traceback

from . import art, ark, charts, dta, iso, plan, verify, video, vgs
from . import audio as audio_stage
from .errors import BuildError, Cancelled

# Order matters: the chart needs the audio's channel layout, and the video is
# muxed with the encoded audio.
SONG_STAGES = [
    ("audio", "mixing audio"),
    ("charts", "converting chart"),
    ("art", "converting album art"),
    ("vgs", "encoding PS2 audio"),
    ("video", "encoding video"),
]

# ps2str takes short plain filenames in a fixed scratch folder, so only one song
# can be muxed at a time.
_MUX_LOCK = threading.Lock()

# Bumped whenever a stage's output changes shape, so songs staged by an older
# version are rebuilt instead of being shipped as they are. 2: every part now
# gets audio channels of its own, silent where there is no stem for it. 3: the
# audio is lined up with the silence Onyx put in front of the chart.
FORMAT = 3


class Result:
    def __init__(self):
        self.shipped = []       # sids that made the disc
        self.problems = []      # (song, stage, reason)
        self.iso = ""
        self.iso_bytes = 0
        self.report = []
        self.seconds = 0.0
        self.cancelled = False


class Pipeline:
    """A build in progress.

    on_log(text)                  a line for the build log
    on_progress(done, total)      songs finished out of the total
    on_song(label, stage, ok, message)   one song's stage finished
    on_stage(text)                the disc-level step now running
    """

    def __init__(self, settings, on_log=None, on_progress=None, on_song=None,
                 on_stage=None):
        self.settings = settings
        self.on_log = on_log or (lambda text: None)
        self.on_progress = on_progress or (lambda done, total: None)
        self.on_song = on_song or (lambda label, stage, ok, message: None)
        self.on_stage = on_stage or (lambda text: None)
        self.stop = threading.Event()
        self._lock = threading.Lock()

    # ---- helpers -----------------------------------------------------------

    def cancel(self):
        self.stop.set()

    def _check(self):
        if self.stop.is_set():
            raise Cancelled()

    def log(self, text):
        self.on_log(text)

    def _stamp_path(self, sid):
        # Kept out of the song's folder: everything in there is shipped, and the
        # disc check compares the folder against the archive file by file.
        return os.path.join(self.settings.work_dir("stamps"), "%s.json" % sid)

    def _signature(self, song):
        """What a staged song was built from, so changes force a rebuild."""
        stems = []
        for name in sorted(song.stems):
            full = os.path.join(song.path, name)
            try:
                st = os.stat(full)
                stems.append([name, st.st_size, int(st.st_mtime)])
            except OSError:
                stems.append([name, 0, 0])
        return {"source": song.path, "stems": stems,
                "video_kbps": self.settings.video_kbps,
                "format": FORMAT}

    def _stale(self, song):
        path = self._stamp_path(song.sid)
        if not os.path.exists(path):
            return True
        try:
            with open(path, encoding="utf-8") as fp:
                return json.load(fp) != self._signature(song)
        except ValueError:
            return True

    def _write_stamp(self, song):
        with open(self._stamp_path(song.sid), "w", encoding="utf-8") as fp:
            json.dump(self._signature(song), fp, indent=1)

    # ---- one song ----------------------------------------------------------

    def _run_stage(self, name, song, force):
        """Run one stage for one song unless its output is already good."""
        sid, source = song.sid, song.path
        module = {"audio": audio_stage, "charts": charts, "art": art,
                  "vgs": vgs, "video": video}[name]

        if not force and module.is_done(self.settings, sid):
            return True, "already built"

        if name == "audio":
            return module.stage(self.settings, sid, source)
        if name == "charts":
            return module.build(self.settings, sid, source)
        if name == "art":
            return module.build(self.settings, sid, source)
        if name == "vgs":
            return module.encode(self.settings, sid)
        with _MUX_LOCK:
            self._check()
            return module.build(self.settings, sid, self.venue_dir)

    def _build_song(self, song):
        """All stages for one song. Returns (song, stage, reason) on failure."""
        self._check()
        force = self._stale(song)
        if force:
            self.log("%s: source changed, rebuilding" % song.label)

        for name, doing in SONG_STAGES:
            self._check()
            try:
                ok, message = self._run_stage(name, song, force)
            except Cancelled:
                raise
            except BuildError as exc:
                return song, doing, str(exc)
            except Exception as exc:
                self.log("%s: %s while %s\n%s"
                         % (song.label, exc, doing, traceback.format_exc()))
                return song, doing, "%s: %s" % (type(exc).__name__, exc)
            self.on_song(song.label, doing, ok, message)
            if not ok:
                return song, doing, message

        ok, why = charts.check_mix(self.settings, song.sid)
        if not ok:
            return song, "checking the chart against the audio", why

        self._write_stamp(song)
        return None

    # ---- the whole disc ----------------------------------------------------

    def build(self, songs, venue_dir="", make_iso=True, do_verify=True):
        started = time.time()
        result = Result()
        self.venue_dir = venue_dir
        settings = self.settings

        problems = settings.problems()
        if problems:
            raise BuildError("Finish setting up first:\n  " +
                             "\n  ".join(problems))
        if not songs:
            raise BuildError("No songs are selected.")

        try:
            self.on_stage("Preparing the base game")
            ark.prepare(settings, log=self.log)

            self.on_stage("Building %d songs" % len(songs))
            done = 0
            failed = []
            with concurrent.futures.ThreadPoolExecutor(settings.jobs) as pool:
                jobs = [pool.submit(self._build_song, s) for s in songs]
                try:
                    for job in concurrent.futures.as_completed(jobs):
                        bad = job.result()
                        if bad:
                            failed.append(bad)
                            song, stage, reason = bad
                            plan.record_problem(settings, song.path, stage,
                                                reason)
                            self.log("%s: could not build - %s (%s)"
                                     % (song.label, reason, stage))
                        with self._lock:
                            done += 1
                        self.on_progress(done, len(songs))
                except Cancelled:
                    for job in jobs:
                        job.cancel()
                    raise

            result.problems = failed
            ready = [s for s in songs if s not in [b[0] for b in failed]]
            if not ready:
                raise BuildError("None of the selected songs could be built. "
                                 "See the log for what each one needed.")

            self._check()
            self.on_stage("Checking what is ready to ship")
            # The song list must name exactly the songs whose files reach the
            # disc, so which songs ship is settled before the list is written:
            # the list draws every cover as it scrolls, and one missing texture
            # crashes it.
            shipped, held = ark.check(settings, [s.sid for s in ready])
            by_sid = dict((s.sid, s) for s in ready)
            for sid, why in sorted(held.items()):
                reason = "; ".join(why)
                self.log("%s: left off the disc - %s" % (sid, reason))
                song = by_sid.get(sid)
                if song:
                    result.problems.append((song, "assembling the disc", reason))
            if not shipped:
                raise BuildError("No song finished every stage, so there is "
                                 "nothing to put on the disc.")

            self._check()
            self.on_stage("Writing the song list")
            dta.write(settings, shipped, log=self.log)
            dta.compile_dta(settings, shipped, log=self.log)

            self._check()
            self.on_stage("Building the game archive")
            ark.assemble(settings, shipped, log=self.log)
            ark.pack(settings, log=self.log)
            result.shipped = shipped

            if make_iso:
                self._check()
                self.on_stage("Writing the disc image")
                result.iso = iso.build(settings, log=self.log)
                result.iso_bytes = os.path.getsize(result.iso)

            if do_verify:
                self._check()
                self.on_stage("Checking the disc")
                ok, report = verify.run(settings, shipped, log=self.log)
                result.report = report
                if not ok:
                    self.log("The disc was built but did not pass every check.")

        except Cancelled:
            result.cancelled = True
            self.log("Build stopped.")
        result.seconds = time.time() - started
        return result


def song_state(settings, songs):
    """Which stages each song has already finished, for the library view."""
    out = {}
    for song in songs:
        stages = {}
        for name, _ in SONG_STAGES:
            module = {"audio": audio_stage, "charts": charts, "art": art,
                      "vgs": vgs, "video": video}[name]
            try:
                stages[name] = module.is_done(settings, song.sid)
            except Exception:
                stages[name] = False
        out[song.path] = stages
    return out
