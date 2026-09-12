"""Reading and writing a PlayStation 2 milo scene, object by object.

superfreq is fine for textures but it does not know what a TrackWidget is, and what it
writes out for one is the body of some other object entirely - 25 widgets came out of the
drum scene claiming six different class versions between them. Anything that adds a gem
has to put objects into a scene, so scenes are parsed here instead.

The container: a four byte magic saying how the rest is packed, the offset the packed part
starts at, then a block count and the size of each block. Inflating them in order gives the
scene. Inside it is a directory - a class, a name, two counts the console sizes its string
table with, a count of objects, then a class and a name per object - followed by the
directory's own body and one body per object, each ending at the same four byte marker.

A directory can hold its subdirectories inside itself rather than naming a file, and the
track panel does exactly that: the whole drum track sits inside trackpanel.milo_ps2 as a
directory called drum, and the tracksystem_drum.milo_ps2 of the same content beside it is
never loaded. So a directory has to be readable wherever in a file it starts, which is what
Dir is for, and what goes in goes in as a splice at a known offset - nothing in a milo
points at another part of it by position, so bytes can be put in without fixing anything up
beyond the block they land in.
"""

import struct
import zlib

UNCOMPRESSED = 0xCABEDEAF
DEFLATED = 0xCBBEDEAF
GZIPPED = 0xCCBEDEAF
BLOCKED = 0xCDBEDEAF
# What sits between one object's body and the next.
MARKER = bytes.fromhex("addeadde")


def _blocks(data):
    """The scene and the size each block came to, out of whichever wrapper it arrived in."""
    magic, start, count, _biggest = struct.unpack_from("<4I", data, 0)
    if magic == UNCOMPRESSED:
        return data[start:], [len(data) - start]
    sizes = struct.unpack_from("<%dI" % count, data, 16)
    out = bytearray()
    grew = []
    at = start
    for size in sizes:
        block = data[at:at + size]
        at += size
        if magic == BLOCKED:
            # These carry a four byte length of their own in front of the stream.
            block = block[4:]
        try:
            block = zlib.decompress(block, -15 if magic != GZIPPED else 47)
        except zlib.error:
            pass
        out += block
        grew.append(len(block))
    return bytes(out), grew


def _string(raw, at):
    size, = struct.unpack_from("<I", raw, at)
    at += 4
    return raw[at:at + size].decode("latin-1"), at + size


class Dir(object):
    """One directory inside an inflated scene, wherever in it it starts.

    Beyond what it holds, this knows where everything sits, so that an object can be put
    in by splicing two runs of bytes into the file: one more pair of names into the table,
    and one more body in step with it.

    The bodies of the last objects come out wrong, because a TrackWidget carries
    sub-objects of its own and each of those ends at the same marker as a body does, so the
    walk runs ahead of the truth once it reaches the first one. Everything before that is
    right, and in a track scene that is the textures, materials, meshes, groups and
    transforms - the meshes being all this needs.
    """

    def __init__(self, raw, at=0):
        self.raw = raw
        self.at = at
        self.version, = struct.unpack_from("<I", raw, at)
        self.kind, spot = _string(raw, at + 4)
        self.name, spot = _string(raw, spot)
        self.counts_at = spot
        self.counts = struct.unpack_from("<2I", raw, spot)
        self.total_at = spot + 8
        total, = struct.unpack_from("<I", raw, self.total_at)
        spot = self.total_at + 4

        # A class name reads the same wherever it turns up, and every object body in a track
        # scene names the directory it belongs to, so what looks like the start of one can
        # be something else entirely. A real one lists a sane number of objects and gives
        # every one of them a printable class and name.
        if not 1 <= total <= 8192:
            raise ValueError("%d objects is not a directory" % total)
        self.entries = []
        self.after = {}
        for _ in range(total):
            kind, spot = _string(raw, spot)
            name, spot = _string(raw, spot)
            for text in (kind, name):
                if not text or not all(32 <= ord(c) < 127 for c in text):
                    raise ValueError("%r is not the name of an object" % text)
            self.entries.append((kind, name))
            # Where one more pair of names would go to follow this one.
            self.after[name] = spot
        self.table_end = spot

        self.bodies = {}
        spot = raw.find(MARKER, spot) + len(MARKER)
        for _kind, name in self.entries:
            end = raw.find(MARKER, spot)
            if end < 0:
                break
            self.bodies[name] = (spot, end + len(MARKER))
            spot = end + len(MARKER)
        self.added = []

    def holds(self, name):
        return name in self.bodies

    def body(self, name):
        start, end = self.bodies[name]
        return self.raw[start:end - len(MARKER)]

    def insert(self, after, kind, name, body):
        """Add one object, its body sitting right after another object's.

        Order matters: the bodies are read in the order the objects are listed, so the pair
        of names and the body have to go in in step. Going in beside something of the same
        class keeps the new object in the part of the file that reads back correctly.
        """
        self.added.append((after, kind, name, body))

    def changes(self):
        """What to write over and what to put in, for everything added to this directory.

        Both counts in the header move: the first by two for every object, the second by
        the room the console leaves for their names. Two objects added after the same one
        keep the order they were added in.
        """
        names = 0
        pairs = {}
        bodies = {}
        for after, kind, name, body in self.added:
            names += len(kind) + len(name) + 2
            pairs.setdefault(self.after[after], bytearray())
            pairs[self.after[after]] += (
                struct.pack("<I", len(kind)) + kind.encode("latin-1")
                + struct.pack("<I", len(name)) + name.encode("latin-1"))
            bodies.setdefault(self.bodies[after][1], bytearray())
            bodies[self.bodies[after][1]] += body + MARKER
        pokes = [(self.counts_at,
                  struct.pack("<2I", self.counts[0] + 2 * len(self.added),
                              self.counts[1] + names)),
                 (self.total_at,
                  struct.pack("<I", len(self.entries) + len(self.added)))]
        splices = [(at, bytes(text))
                   for at, text in list(pairs.items()) + list(bodies.items())]
        return pokes, splices


def dirs(raw, kind):
    """Every directory of one class in an inflated scene, outermost last."""
    tag = struct.pack("<I", len(kind)) + kind.encode("latin-1")
    out = []
    at = raw.find(tag)
    while at >= 0:
        if at >= 4 and struct.unpack_from("<I", raw, at - 4)[0] == 25:
            try:
                out.append(Dir(raw, at - 4))
            except (struct.error, UnicodeDecodeError, ValueError):
                pass
        at = raw.find(tag, at + 1)
    return out


def repack(raw, splits, pokes=(), splices=()):
    """A scene back in its wrapper, with bytes written over and bytes put in.

    The console inflates one block at a time, and the ones the game shipped stop at the end
    of an object rather than at a round number, so every block keeps the bytes it had. Only
    the block a splice lands in grows, which leaves no object straddling a boundary that it
    did not straddle before.
    """
    out = bytearray(raw)
    for at, over in pokes:
        out[at:at + len(over)] = over

    sizes = list(splits)
    for at, added in sorted(splices, key=lambda s: -s[0]):
        out[at:at] = added
        edge = 0
        for i, size in enumerate(sizes):
            edge += size
            if at < edge or i == len(sizes) - 1:
                sizes[i] += len(added)
                break

    if sum(sizes) != len(out):
        raise ValueError("the scene does not fit the blocks it came in")
    body = bytearray()
    packed = []
    at = 0
    for size in sizes:
        fit = zlib.compressobj(9, zlib.DEFLATED, -15)
        block = fit.compress(bytes(out[at:at + size])) + fit.flush()
        packed.append(len(block))
        body += block
        at += size
    head = struct.pack("<4I", DEFLATED, 2064, len(sizes), max(sizes))
    head += struct.pack("<%dI" % len(packed), *packed)
    return head + b"\x00" * (2064 - len(head)) + bytes(body)


def inflate(path):
    """One scene out of its wrapper, and the size each block came to."""
    with open(path, "rb") as fp:
        return _blocks(fp.read())


class Scene(object):
    """One milo directory: its class and name, its own body, and the objects in it.

    The bodies of the last classes in the file come out wrong, because a TrackWidget
    carries sub-objects of its own and each of those ends at the same marker, so the
    split runs ahead of the truth and the leftovers pile up in the tail. Everything up
    to the first widget is right, which is where the meshes are, and the tail carries
    the rest through untouched, so what is built is the scene byte for byte.
    """

    def __init__(self, data):
        self.raw, self.splits = _blocks(data)
        at = 0
        self.version, = struct.unpack_from("<I", self.raw, at)
        at += 4
        self.kind, at = self._string(at)
        self.name, at = self._string(at)
        # Two counts the game uses to size its string tables before it reads.
        self.counts = struct.unpack_from("<2I", self.raw, at)
        at += 8
        total, = struct.unpack_from("<I", self.raw, at)
        at += 4
        self.entries = []
        for _ in range(total):
            kind, at = self._string(at)
            name, at = self._string(at)
            self.entries.append([kind, name, b""])
        # The directory's own body comes first, then one body per object, and every one
        # of them ends at the marker.
        self.body, at = self._body(at)
        for entry in self.entries:
            entry[2], at = self._body(at)
        self.tail = self.raw[at:]

    def _string(self, at):
        size, = struct.unpack_from("<I", self.raw, at)
        at += 4
        return self.raw[at:at + size].decode("latin-1"), at + size

    def _body(self, at):
        end = self.raw.find(MARKER, at)
        if end < 0:
            raise ValueError("an object body at %d never ends" % at)
        return self.raw[at:end], end + len(MARKER)

    def find(self, name):
        for entry in self.entries:
            if entry[1] == name:
                return entry
        return None

    def add(self, after, kind, name, body):
        """Put one more object in, its body sitting right after another object's.

        Order matters: the bodies are read in the order the objects are listed, so the
        two have to be kept in step. Going in next to something of the same class keeps
        the new object inside the part of the file that is read correctly.
        """
        for at, entry in enumerate(self.entries):
            if entry[1] == after:
                self.entries.insert(at + 1, [kind, name, body])
                self.counts = (self.counts[0] + 2,
                               self.counts[1] + len(kind) + len(name) + 2)
                return
        raise ValueError("%s is not in this scene" % after)

    def build(self):
        """The scene as the game reads it, uncompressed."""
        out = bytearray(struct.pack("<I", self.version))
        for text in (self.kind, self.name):
            out += struct.pack("<I", len(text)) + text.encode("latin-1")
        out += struct.pack("<2I", *self.counts)
        out += struct.pack("<I", len(self.entries))
        for kind, name, _body in self.entries:
            for text in (kind, name):
                out += struct.pack("<I", len(text)) + text.encode("latin-1")
        out += self.body + MARKER
        for _kind, _name, body in self.entries:
            out += body + MARKER
        return bytes(out) + self.tail

    def pack(self):
        """The scene back in its wrapper, in the same blocks it came out of.

        The game inflates one block at a time, and the ones it shipped stop at the end
        of an object rather than at a round number, so the same stopping points are kept
        and only the block holding what was added grows. Nothing lands in a different
        block than it was in, and no object is left straddling two of them.
        """
        inside = self.build()
        sizes = list(self.splits)
        sizes[0] += len(inside) - len(self.raw)
        if sizes[0] < 1 or sum(sizes) != len(inside):
            raise ValueError("the scene does not fit the blocks it came in")
        out = bytearray()
        at = 0
        packed = []
        for size in sizes:
            fit = zlib.compressobj(9, zlib.DEFLATED, -15)
            block = fit.compress(inside[at:at + size]) + fit.flush()
            packed.append(len(block))
            out += block
            at += size
        head = struct.pack("<4I", DEFLATED, 2064, len(sizes), max(sizes))
        head += struct.pack("<%dI" % len(packed), *packed)
        return head + b"\x00" * (2064 - len(head)) + bytes(out)


def read(path):
    with open(path, "rb") as fp:
        return Scene(fp.read())
