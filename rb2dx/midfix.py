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
