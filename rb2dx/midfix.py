"""Rewrite a Rock Band chart so it only uses what RB2 on PS2 understands.

Onyx targets RB2 on Xbox 360, which tolerates more than the PS2 build does.
Comparing our chart against retail 'afterlife' turns up three things retail
never contains:

  * notes on the EVENTS track (retail carries text events only)
  * pitches 101 and 102 on instrument tracks, which fall between Expert's
    top gem (100) and the solo marker (103) and mean nothing to RB2
  * '[lighting ()]' with an empty preset name in VENUE

Each transform can be applied on its own so a build can ship several variants
side by side and let one play session identify which one matters.
"""

import re
import struct

INSTRUMENT_TRACKS = ("PART DRUMS", "PART GUITAR", "PART BASS", "PART VOCALS")
# Every track that belongs to a part, including the harmony tracks that go with
# vocals, and which part the game plays it as.
PART_FOR_TRACK = {"PART DRUMS": "drum", "PART GUITAR": "guitar",
                  "PART BASS": "bass", "PART RHYTHM": "bass",
                  "PART VOCALS": "vocals", "HARM1": "vocals",
                  "HARM2": "vocals", "HARM3": "vocals"}
BAD_PITCHES = (101, 102)
RETAIL_ORDER = ("PART DRUMS", "PART GUITAR", "PART BASS", "PART VOCALS",
                "VENUE", "EVENTS", "BEAT")
# A preset retail uses, substituted wherever the chart asks for no lighting.
LIGHTING_FALLBACK = b"[lighting (loop_warm)]"

# '[mix <difficulty> drums<mode>]' tells the mixer how wide the drum submix is.
# An official chart's mode describes the stem layout it was authored against,
# not the one we build from Clone Hero stems, and the two disagree often: our
# 4-channel songs arrive carrying drums2 and drums3, and one RB3 chart asks for
# drums4. Pointing the mixer at drum channels the mix does not have hangs the
# load forever, so the mode is rewritten to match what we actually ship. Only
# these two widths are ever emitted, and both are confirmed on this disc.
DRUM_MODE_FOR_WIDTH = {2: 0, 4: 3}
MIX_EVENT = re.compile(rb"\[mix (\d+) drums(\d+)[a-z]*\]")


def read_varlen(data, i):
    val = 0
    while True:
        b = data[i]
        i += 1
        val = (val << 7) | (b & 0x7F)
        if not b & 0x80:
            return val, i


def write_varlen(val):
    out = bytearray([val & 0x7F])
    val >>= 7
    while val:
        out.insert(0, 0x80 | (val & 0x7F))
        val >>= 7
    return bytes(out)


def parse_track(body):
    """Flatten a track to (abs_tick, event_bytes) with running status expanded."""
    events = []
    tick = 0
    i = 0
    running = None
    while i < len(body):
        delta, i = read_varlen(body, i)
        tick += delta
        if i >= len(body):
            break
        status = body[i]
        if status == 0xFF:
            start = i
            i += 2
            length, i = read_varlen(body, i)
            i += length
            events.append((tick, body[start:i]))
            continue
        if status in (0xF0, 0xF7):
            start = i
            i += 1
            length, i = read_varlen(body, i)
            i += length
            events.append((tick, body[start:i]))
            continue
        if status & 0x80:
            running = status
            i += 1
        elif running is None:
            break
        nbytes = 1 if (running & 0xF0) in (0xC0, 0xD0) else 2
        events.append((tick, bytes([running]) + body[i:i + nbytes]))
        i += nbytes
    return events


def build_track(events):
    out = bytearray()
    prev = 0
    for tick, ev in events:
        out += write_varlen(tick - prev)
        out += ev
        prev = tick
    return bytes(out)


def read_mid(path):
    """(format, division, [track events]) for a standard MIDI file."""
    with open(path, "rb") as fp:
        data = fp.read()
    hlen = struct.unpack_from(">I", data, 4)[0]
    fmt, _, div = struct.unpack_from(">HHH", data, 8)
    i = 8 + hlen
    tracks = []
    while i < len(data) - 8 and data[i:i + 4] == b"MTrk":
        tlen = struct.unpack_from(">I", data, i + 4)[0]
        tracks.append(parse_track(data[i + 8:i + 8 + tlen]))
        i += 8 + tlen
    return fmt, div, tracks


def tempo_changes(tracks):
    """[(tick, microseconds per quarter note)], starting at tick 0."""
    out = []
    for events in tracks:
        for tick, ev in events:
            if ev[0] == 0xFF and ev[1] == 0x51:
                length, j = read_varlen(ev, 2)
                out.append((tick, int.from_bytes(ev[j:j + length], "big")))
    out.sort()
    # 120 bpm is the MIDI default, for a chart that never states one.
    if not out or out[0][0]:
        out.insert(0, (0, 500000))
    return out


def seconds_at(tempos, div, tick):
    """Where a tick falls in seconds, following the tempo map."""
    total = 0.0
    last_tick, last_usec = 0, tempos[0][1]
    for at_tick, usec in tempos:
        if at_tick >= tick:
            break
        total += (at_tick - last_tick) * last_usec / 1e6 / div
        last_tick, last_usec = at_tick, usec
    return total + (tick - last_tick) * last_usec / 1e6 / div


def first_note_seconds(path):
    """When this chart's first playable note lands, or None if it has none.

    Comparing this between two versions of the same chart is how the pipeline
    measures the silence Onyx adds ahead of a song.
    """
    _, div, tracks = read_mid(path)
    ticks = [tick for events in tracks
             if track_name(events) in INSTRUMENT_TRACKS
             for tick, ev in events if note_on(ev)]
    if not ticks:
        return None
    return seconds_at(tempo_changes(tracks), div, min(ticks))


def instrument_parts(path):
    """The parts this chart has notes on, as songs.dta names them."""
    _, _, tracks = read_mid(path)
    out = set()
    for events in tracks:
        part = PART_FOR_TRACK.get(track_name(events).upper())
        if part and any(note_on(ev) for _, ev in events):
            out.add(part)
    return out


def track_name(events):
    for _, ev in events:
        if ev[0] == 0xFF and ev[1] == 0x03:
            length, j = read_varlen(ev, 2)
            return ev[j:j + length].decode("latin-1", "replace")
    return ""


def is_note(ev):
    return (ev[0] & 0xF0) in (0x80, 0x90)


def is_end_of_track(ev):
    return ev[0] == 0xFF and ev[1] == 0x2F


def event_text(ev):
    """The text a meta event carries, or None if it carries none."""
    if ev[0] == 0xFF and ev[1] in (0x01, 0x05):
        length, j = read_varlen(ev, 2)
        return ev[j:j + length]
    return None


def text_event(text):
    return b"\xFF\x01" + write_varlen(len(text)) + text


DIFF_BASES = {"easy": 60, "medium": 72, "hard": 84, "expert": 96}
REDUCED_TRACKS = ("PART GUITAR", "PART BASS")


def note_on(ev):
    return (ev[0] & 0xF0) == 0x90 and ev[2] > 0


def lanes_used(events, base):
    return {e[1] - base for _, e in events
            if note_on(e) and base <= e[1] <= base + 4}


def fix_reductions(path):
    """Keep the easier difficulties inside the lanes Expert actually plays.

    Magma rejects a chart whose Easy, Medium or Hard uses a lane missing from
    Expert. Rather than delete those gems, each moves to the nearest lane Expert
    does use so the rhythm survives; one that would land on top of an existing
    gem is dropped instead. Expert is never touched.
    """
    with open(path, "rb") as fp:
        data = fp.read()
    hlen = struct.unpack_from(">I", data, 4)[0]
    fmt, _, div = struct.unpack_from(">HHH", data, 8)
    i = 8 + hlen

    tracks = []
    while i < len(data) - 8 and data[i:i + 4] == b"MTrk":
        tlen = struct.unpack_from(">I", data, i + 4)[0]
        tracks.append(parse_track(data[i + 8:i + 8 + tlen]))
        i += 8 + tlen

    report = []
    for idx, events in enumerate(tracks):
        if track_name(events) not in REDUCED_TRACKS:
            continue
        allowed = lanes_used(events, DIFF_BASES["expert"])
        if not allowed:
            continue

        moves = {}
        for diff, base in DIFF_BASES.items():
            if diff == "expert":
                continue
            for lane in sorted(lanes_used(events, base) - allowed):
                near = min(allowed, key=lambda a: (abs(a - lane), a))
                moves[base + lane] = base + near
        if not moves:
            continue

        taken = {(t, e[1]) for t, e in events if note_on(e)}
        state = {}
        out = []
        moved = dropped = 0
        for tick, e in events:
            pitch = e[1] if is_note(e) else None
            if note_on(e) and pitch in moves:
                target = moves[pitch]
                if (tick, target) in taken:
                    state[pitch] = None
                    dropped += 1
                    continue
                taken.add((tick, target))
                state[pitch] = target
                moved += 1
                e = bytes([e[0], target, e[2]])
            elif is_note(e) and pitch in moves:
                target = state.pop(pitch, None)
                if target is None:
                    continue
                e = bytes([e[0], target, e[2]])
            out.append((tick, e))
        tracks[idx] = out
        report.append("%s: moved %d gems into Expert's lanes, dropped %d that "
                      "collided" % (track_name(out), moved, dropped))

    if not report:
        return report

    out = bytearray(b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), div))
    for events in tracks:
        body = build_track(events)
        out += b"MTrk" + struct.pack(">I", len(body)) + body
    with open(path, "wb") as fp:
        fp.write(bytes(out))
    return report


def fix_lyrics(path):
    """Leave the pitch slide marker standing alone in every lyric.

    Magma refuses a lyric like '+/': the '+' that marks a slide onto the next
    note has to be the whole syllable. The trailing character is a line break
    hint from whatever editor wrote the source chart, and Rock Band 2 has no
    concept of it, so it goes. Rewrites the file and returns what changed.
    """
    with open(path, "rb") as fp:
        data = fp.read()
    hlen = struct.unpack_from(">I", data, 4)[0]
    fmt, _, div = struct.unpack_from(">HHH", data, 8)
    i = 8 + hlen

    tracks = []
    while i < len(data) - 8 and data[i:i + 4] == b"MTrk":
        tlen = struct.unpack_from(">I", data, i + 4)[0]
        tracks.append(parse_track(data[i + 8:i + 8 + tlen]))
        i += 8 + tlen

    fixed = []
    for idx, events in enumerate(tracks):
        name = track_name(events).upper()
        if "VOCALS" not in name and not name.startswith("HARM"):
            continue
        out = []
        for t, e in events:
            if e[0] == 0xFF and e[1] in (0x01, 0x05):
                length, j = read_varlen(e, 2)
                text = e[j:j + length]
                if b"+" in text and text != b"+":
                    fixed.append("%s: %r -> '+'" % (name, text))
                    e = bytes(e[:2]) + write_varlen(1) + b"+"
            out.append((t, e))
        tracks[idx] = out

    if not fixed:
        return fixed

    out = bytearray(b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), div))
    for events in tracks:
        body = build_track(events)
        out += b"MTrk" + struct.pack(">I", len(body)) + body
    with open(path, "wb") as fp:
        fp.write(bytes(out))
    return fixed


def keep_only_parts(path, keep):
    """Drop the tracks for parts the song does not offer. Returns what changed.

    Run before Magma, which checks everything it is handed. A chart holding its
    lyrics in the events track imports as a vocals part whether the song has
    vocals or not, and Magma is strict about vocals, so this keeps a song from
    failing over notes the disc was never going to carry.
    """
    fmt, div, tracks = read_mid(path)

    dropped = []
    kept = []
    for events in tracks:
        name = track_name(events)
        part = PART_FOR_TRACK.get(name.upper())
        if part and part not in keep:
            dropped.append((name, part))
            continue
        kept.append(events)
    if not dropped:
        return []

    out = bytearray(b"MThd" + struct.pack(">IHHH", 6, fmt, len(kept), div))
    for events in kept:
        body = build_track(events)
        out += b"MTrk" + struct.pack(">I", len(body)) + body
    with open(path, "wb") as fp:
        fp.write(bytes(out))
    return ["dropped %s before the chart is built: this song offers no %s"
            % (", ".join(name for name, _ in dropped),
               ", ".join(sorted({part for _, part in dropped})))]


# A phrase marker, and the second player's alongside it.
PHRASE_PITCHES = (105, 106)


def fix_vocal_phrases(path):
    """Fold a vocal phrase opened inside another into it.

    A phrase is a note at pitch 105, or 106 for the second player's, spanning the
    lyrics it holds, and Magma refuses a chart with two open at once: 'Vocal
    phrase overlap', followed by a double note-on. The inner phrase's ends are
    dropped, leaving one phrase over the same lyrics.
    """
    fmt, div, tracks = read_mid(path)

    fixed = []
    for idx, events in enumerate(tracks):
        name = track_name(events).upper()
        if "VOCALS" not in name and not name.startswith("HARM"):
            continue

        depth = dict.fromkeys(PHRASE_PITCHES, 0)
        out = []
        merged = 0
        for tick, ev in events:
            pitch = ev[1] if is_note(ev) else None
            if pitch in depth:
                if note_on(ev):
                    depth[pitch] += 1
                    if depth[pitch] > 1:
                        merged += 1
                        continue
                else:
                    depth[pitch] = max(0, depth[pitch] - 1)
                    if depth[pitch] > 0:
                        continue
            out.append((tick, ev))
        if merged:
            tracks[idx] = out
            fixed.append("%s: folded %d phrase%s into the one it was nested in"
                         % (name, merged, "" if merged == 1 else "s"))

    if not fixed:
        return fixed

    out = bytearray(b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), div))
    for events in tracks:
        body = build_track(events)
        out += b"MTrk" + struct.pack(">I", len(body)) + body
    with open(path, "wb") as fp:
        fp.write(bytes(out))
    return fixed


# Rock Band draws three gems at once at most, and Magma stops at 'Chord at ...
# has 4 slots; max is 3'.
MAX_CHORD = 3
CHORD_TRACKS = ("PART GUITAR", "PART BASS", "PART RHYTHM")


def fix_wide_chords(path):
    """Trim chords to the three gems Rock Band 2 can play.

    The gems above the lowest three are dropped, so the chord keeps its root.
    """
    fmt, div, tracks = read_mid(path)

    fixed = []
    for idx, events in enumerate(tracks):
        name = track_name(events).upper()
        if name not in CHORD_TRACKS:
            continue

        drop = set()
        for base in DIFF_BASES.values():
            chords = {}
            for tick, ev in events:
                if note_on(ev) and base <= ev[1] <= base + 4:
                    chords.setdefault(tick, []).append(ev[1])
            for tick, pitches in chords.items():
                for pitch in sorted(pitches)[MAX_CHORD:]:
                    drop.add((tick, pitch))
        if not drop:
            continue

        out = []
        dropping = set()
        for tick, ev in events:
            if note_on(ev) and (tick, ev[1]) in drop:
                dropping.add(ev[1])
                continue
            if is_note(ev) and not note_on(ev) and ev[1] in dropping:
                dropping.discard(ev[1])
                continue
            out.append((tick, ev))
        tracks[idx] = out
        count = len({tick for tick, _ in drop})
        fixed.append("%s: trimmed %d chord%s to the three gems RB2 can play"
                     % (name, count, "" if count == 1 else "s"))

    if not fixed:
        return fixed

    out = bytearray(b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), div))
    for events in tracks:
        body = build_track(events)
        out += b"MTrk" + struct.pack(">I", len(body)) + body
    with open(path, "wb") as fp:
        fp.write(bytes(out))
    return fixed


def write_mid(path, fmt, div, tracks):
    out = bytearray(b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), div))
    for events in tracks:
        body = build_track(events)
        out += b"MTrk" + struct.pack(">I", len(body)) + body
    with open(path, "wb") as fp:
        fp.write(bytes(out))


END_TEXT = b"[end]"
# How much room the marker gets past the last event, in beats. Onyx holds a
# venue note to a minimum length of an eighth of a beat, so this only has to be
# more than that.
END_ROOM_BEATS = 1


def fix_end_marker(path):
    """Leave one [end] marker, clear of everything else in the chart.

    Magma stops at 'Found event(s) after the [end] event'. Two things cause it:
    a chart carrying a second [end] with events between the two, and a venue
    note struck on the last beat, which Onyx lengthens on the way through so its
    note-off lands past the marker. One marker a beat clear of the last event
    answers both.
    """
    fmt, div, tracks = read_mid(path)

    def marker(ev):
        return (event_text(ev) or b"").strip() == END_TEXT

    ends = [tick for events in tracks for tick, ev in events if marker(ev)]
    if not ends:
        # Onyx places its own, and puts it clear of the chart by itself.
        return []
    last = max((tick for events in tracks for tick, ev in events
                if not is_end_of_track(ev) and not marker(ev)), default=0)
    target = last + div * END_ROOM_BEATS
    if len(ends) == 1 and ends[0] >= target:
        return []

    home = next(idx for idx, events in enumerate(tracks)
                if any(marker(ev) for _, ev in events))
    for idx, events in enumerate(tracks):
        out = [(tick, ev) for tick, ev in events
               if not marker(ev) and not is_end_of_track(ev)]
        if idx == home:
            out.append((target, text_event(END_TEXT)))
        out.sort(key=lambda pair: pair[0])
        stop = max([target if idx == home else 0]
                   + [tick for tick, _ in out])
        out.append((stop, b"\xFF\x2F\x00"))
        tracks[idx] = out

    write_mid(path, fmt, div, tracks)
    if len(ends) > 1:
        return ["kept one [end] marker of %d and moved it past the last event, "
                "which Magma insists nothing follows" % len(ends)]
    return ["moved [end] a beat later, to %.1f beats past the last event, which "
            "Magma insists nothing follows" % ((target - last) / float(div))]


# What a sung note can be. Below this are the phrase and range markers, above it
# the overdrive and percussion pitches, and none of those carry a lyric.
VOCAL_LOW, VOCAL_HIGH = 36, 84
# How far from its note a lyric can sit and still be meant for it, as a share of
# the beat. A quarter covers what editors leave behind - the charts this was
# written for are out by a sixteenth note - while staying well short of the gap
# between one syllable and the next.
LYRIC_SNAP_BEAT = 4


def _vocal_notes(events):
    """[(start, end, index of note-on, index of note-off)] for the sung notes."""
    notes, open_at = [], {}
    for idx, (tick, ev) in enumerate(events):
        if not is_note(ev) or not VOCAL_LOW <= ev[1] <= VOCAL_HIGH:
            continue
        if note_on(ev):
            open_at.setdefault(ev[1], []).append((tick, idx))
        elif open_at.get(ev[1]):
            start, start_idx = open_at[ev[1]].pop(0)
            notes.append([start, tick, start_idx, idx])
    notes.sort()
    return notes


def fix_vocal_notes(path):
    """Give every sung note its own lyric and its own stretch of time.

    Rock Band sings one syllable per note and one note at a time, and Magma
    reports a chart that strays from either as a misaligned or missing lyric, a
    misaligned note, or a double note-on. Three things put it right: a lyric
    near a note moves onto it, a note left with no lyric at all is dropped, and
    a note running into the next one is shortened to meet it.
    """
    fmt, div, tracks = read_mid(path)
    snap = max(1, div // LYRIC_SNAP_BEAT)

    fixed = []
    for idx, events in enumerate(tracks):
        name = track_name(events).upper()
        if "VOCALS" not in name and not name.startswith("HARM"):
            continue

        notes = _vocal_notes(events)
        if not notes:
            continue
        # Bracketed text is an instruction to the venue, not something sung.
        lyrics = [(tick, i) for i, (tick, ev) in enumerate(events)
                  if (event_text(ev) or b"[").lstrip()[:1] != b"["]
        starts = {n[0] for n in notes}
        # A note already sung on cannot take a second syllable.
        taken = starts & {tick for tick, _ in lyrics}

        moved = {}
        for tick, i in lyrics:
            if tick in starts:
                continue
            near = [n[0] for n in notes
                    if abs(n[0] - tick) <= snap and n[0] not in taken]
            if not near:
                continue
            to = min(near, key=lambda start: (abs(start - tick), start))
            moved[i] = to
            taken.add(to)
        sung = {tick for tick, i in lyrics} | set(moved.values())

        drop = set()
        for note in notes:
            if note[0] not in sung:
                drop.add(note[2])
                drop.add(note[3])
        kept = [n for n in notes if n[2] not in drop]

        shortened = {}
        for note, after in zip(kept, kept[1:]):
            if note[1] > after[0]:
                shortened[note[3]] = after[0]

        if not (moved or drop or shortened):
            continue

        out = []
        for i, (tick, ev) in enumerate(events):
            if i in drop:
                continue
            out.append((moved.get(i, shortened.get(i, tick)), ev))
        # A note that now ends where the next begins has to say so before the
        # next one starts, or a parser reads the two as one note held twice.
        out.sort(key=lambda pair: (pair[0], _vocal_order(pair[1])))
        tracks[idx] = out

        said = []
        if moved:
            said.append("moved %d lyric%s onto the note it belongs to"
                        % (len(moved), "" if len(moved) == 1 else "s"))
        if drop:
            said.append("dropped %d note%s with nothing to sing on it"
                        % (len(drop) // 2, "" if len(drop) == 2 else "s"))
        if shortened:
            said.append("shortened %d note%s that ran into the next"
                        % (len(shortened), "" if len(shortened) == 1 else "s"))
        fixed.append("%s: %s" % (name, ", ".join(said)))

    if fixed:
        write_mid(path, fmt, div, tracks)
    return fixed


def _vocal_order(ev):
    """Where an event sits among others on the same tick."""
    if is_note(ev) and not note_on(ev):
        return 0
    if is_end_of_track(ev):
        return 3
    return 1 if not note_on(ev) else 2


def conform(src, dst, do_events=False, do_pitches=False, do_lighting=False,
            do_order=False, one_tempo=False, rename=None, drum_width=None,
            keep_parts=None):
    """Apply the selected transforms, writing a new chart. Returns a report.

    drum_width is the number of drum channels the song's mix actually has; when
    given, drum mix events are rewritten to the mode that matches it.

    keep_parts, when given, is the set of parts the song offers; tracks for any
    other part are dropped so that the chart holds exactly what the song list
    says can be played.
    """
    fmt, div, tracks = read_mid(src)

    report = []
    for idx, events in enumerate(tracks):
        name = track_name(events)

        if do_events and name == "EVENTS":
            before = len(events)
            events = [(t, e) for t, e in events if not is_note(e)]
            report.append("EVENTS: dropped %d note events" % (before - len(events)))

        if do_pitches and name in INSTRUMENT_TRACKS:
            before = len(events)
            events = [(t, e) for t, e in events
                      if not (is_note(e) and e[1] in BAD_PITCHES)]
            if before != len(events):
                report.append("%s: dropped %d events on pitches %s"
                              % (name, before - len(events),
                                 ",".join(str(p) for p in BAD_PITCHES)))

        if do_lighting and name == "VENUE":
            fixed = 0
            out = []
            for t, e in events:
                if e[0] == 0xFF and e[1] == 0x01:
                    length, j = read_varlen(e, 2)
                    text = e[j:j + length]
                    if text.strip() == b"[lighting ()]":
                        e = b"\xFF\x01" + write_varlen(len(LIGHTING_FALLBACK)) \
                            + LIGHTING_FALLBACK
                        fixed += 1
                out.append((t, e))
            events = out
            if fixed:
                report.append("VENUE: replaced %d empty [lighting ()] events"
                              % fixed)

        if drum_width and name == "PART DRUMS":
            mode = DRUM_MODE_FOR_WIDTH.get(drum_width)
            if mode is None:
                report.append("PART DRUMS: no known mix mode for %d drum "
                              "channels, left alone" % drum_width)
            else:
                want = b"drums%d]" % mode
                changed = 0
                out = []
                for t, e in events:
                    if e[0] == 0xFF and e[1] == 0x01:
                        length, j = read_varlen(e, 2)
                        text = e[j:j + length]
                        m = MIX_EVENT.fullmatch(text.strip())
                        if m and not text.strip().endswith(want):
                            fixed = b"[mix %s drums%d]" % (m.group(1), mode)
                            e = b"\xFF\x01" + write_varlen(len(fixed)) + fixed
                            changed += 1
                    out.append((t, e))
                events = out
                if changed:
                    report.append("PART DRUMS: retargeted %d drum mix events to "
                                  "drums%d for a %d-channel submix"
                                  % (changed, mode, drum_width))

        if one_tempo and idx == 0:
            seen = False
            out = []
            for t, e in events:
                if e[0] == 0xFF and e[1] == 0x51:
                    if seen:
                        continue
                    seen = True
                out.append((t, e))
            if len(out) != len(events):
                report.append("tempo track: kept only the first tempo (%d dropped)"
                              % (len(events) - len(out)))
            events = out

        if rename and idx == 0:
            payload = rename.encode("latin-1")
            out = [(t, e) for t, e in events
                   if not (e[0] == 0xFF and e[1] == 0x03)]
            out.insert(0, (0, b"\xFF\x03" + write_varlen(len(payload)) + payload))
            events = out
            report.append("tempo track: renamed to %r" % rename)

        tracks[idx] = events

    if keep_parts is not None:
        dropped, kept = [], []
        for events in tracks:
            name = track_name(events)
            part = PART_FOR_TRACK.get(name.upper())
            if part and part not in keep_parts:
                dropped.append((name, part))
                continue
            kept.append(events)
        tracks = kept
        if dropped:
            report.append(
                "dropped %s: the song does not offer %s"
                % (", ".join(name for name, _ in dropped),
                   ", ".join(sorted({part for _, part in dropped}))))

    if do_order:
        first = tracks[0]
        rest = tracks[1:]
        by_name = {track_name(t): t for t in rest}
        ordered = [by_name.pop(n) for n in RETAIL_ORDER if n in by_name]
        ordered += list(by_name.values())
        if [track_name(t) for t in rest] != [track_name(t) for t in ordered]:
            report.append("reordered tracks to retail order")
        tracks = [first] + ordered

    out = bytearray(b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), div))
    for events in tracks:
        body = build_track(events)
        out += b"MTrk" + struct.pack(">I", len(body)) + body

    with open(dst, "wb") as fp:
        fp.write(bytes(out))
    return report
