"""Pro drums: telling the cymbals apart from the pads on the track.

Rock Band 2's charts have always said which drum notes are cymbals - a marker laid over
the note, one per lane, written the day the chart was made - and this console reads it.
Its gem table has a row for a tom beside the row for an ordinary note, and its executable
asks a widget for both, so the two have always been able to look different. They never do,
because both rows name the same gem.

So the cymbals get a gem of their own. One per gem a cymbal lane is ever drawn with, which
comes to four - the three a right-handed kit uses, and the red one a left-handed kit draws the
green lane with, that kit mirroring the track by naming the gem from the far side of it. Each
is a round plate lying flat on the deck with a bell raised out of the middle and a silver rim
round the edge, which is what a cymbal looks like from behind a kit where a pad looks like a
bar. The toms keep the gem they always had, which means overdrive, the fills and the big rock
ending are left exactly as the game shipped them, and no gem sheet is touched, so no other
instrument's gems change in the slightest.

The geometry is written rather than borrowed, because a pad gem is a wedge with corners on
it and no amount of squashing takes the corners off. Everything else about a cymbal comes
from the lane's own pad gem: the same header, the same material, and every vertex reading the
gem sheet where a pad vertex playing the same part reads it - the middle of a gem reads its
lane's colour and the outside reads the chrome beside it on the sheet, which is where the
silver rim comes from. So each cymbal is the colour of its own lane and nothing new is drawn
anywhere.

The four parts of it:

  the drum scene   gains five cymbals, each built on the header of the gem it stands in for
                   and owning its own geometry: one per gem a cymbal lane is drawn with in
                   either kit, and one more built on the white gem an overdrive phrase is
                   drawn with. That scene is not the file of that name: the track panel
                   carries the whole drum track inside it, and the console never opens the
                   tracksystem_drum.milo_ps2 beside it

  the track panel  gains a few lines that run as a song finishes loading. They make a
                   TrackWidget per cymbal lane by copying that lane's pad widget - which
                   carries the transform, the environment and the hit effects that make a
                   gem behave like a gem - and then swap the meshes it draws for the cymbal
                   on its own, leaving off the glow a pad draws behind itself

  the gem table    sends the ordinary row of each cymbal lane to the new widget, leaving
                   the tom row where it was, and its unison row to the white cymbal - in
                   each kit's own rows, and in the left-handed kit's by which gem a row
                   names rather than which lane it is

  the executable   gains one block of rewritten instructions, because the table alone
                   cannot fix an overdrive phrase. In a phrase every note draws the star
                   row whether it is a tom or a cymbal, and there is no star row for a tom
                   and another for a cymbal to be told apart by. What there is is a unison
                   row, which on drums names the very same gem as the star row and so says
                   nothing. So the check that picks between those two rows is rewritten to
                   pick on whether the chart calls the note a cymbal instead, on drum tracks
                   only, leaving every other instrument's unison phrases exactly as they
                   were. The same block asks the chart about every lane a note is played on
                   rather than only the lowest, which is what the game did and what had a
                   crash drawn as a pad whenever a kick or the snare was played under it

Lego Rock Band's Ultimate mod does all of this on the consoles it runs on, and reading it is
what settled the shape of this: a widget's meshes are an array, new widgets are made with new
and copy, the gem table picks between them, and their fix for overdrive was that same rewrite
of the star-or-unison check. None of their work could be carried over - their cymbals are Xbox
meshes, and a PlayStation 2 mesh is tri-stripped where an Xbox one is not, and their patch is
for a different processor - but nothing of theirs was needed beyond the method, because the
geometry here is cloned from geometry already on the disc.

Every word this leans on was checked against SLUS_218.00 first: tom sits in the executable's
list of gem widgets beside normal, star, unison, invisible and bonus; meshes and multi_mesh
are TrackWidget's own properties in track_objects.dtb; and new, copy, insert, remove,
find_obj, iterate and sync_objects are all in there and all used by scripts the game ships.

Nothing here is written to the unpacked game. Every edit lands in the copy each build
makes, which is thrown away and made again next time.
"""

import hashlib
import math
import os
import re
import shutil
import struct

from . import milo, mips, proc
from .errors import BuildError

# Inside the archive: the scenes a drum track is drawn from, the panel every track is played
# on, and the table naming a gem per lane and per kind of note.
#
# The scenes are the panels rather than tracksystem_drum.milo_ps2, because a milo directory
# can hold its subdirectories inside itself and these do: the whole drum track is a
# directory called drum inside each panel, and that is the copy the console loads. One panel
# is for a band playing together and the other for two players against each other.
SCENES = ("track/gen/trackpanel.milo_ps2", "track/gen/trackpanel_hth.milo_ps2")
PANEL = "ui/gen/track_panel.dtb"
TABLE = "config/gen/track_graphics.dtb"

# The lanes that can hold a cymbal. The red lane is the snare and never holds one, and neither
# does the kick, so both are left alone in both kits.
#
# A lane is a lane of the chart, which the console numbers the same way round whichever kit is
# being played: the snare is the snare and the green lane is the green lane. What changes is the
# gem each one is drawn with. A left-handed kit mirrors the track by naming the gem from the far
# side of it - the chart's green notes are drawn with the red gem, at the red end, the track's
# own painted lanes staying where they are - so its rows have to be edited by which gem they
# name rather than by which lane they are, or the cymbals land on the snare.
LANES = ("yellow", "blue", "green")
LEFTY_GEMS = {"yellow": "blue", "blue": "yellow", "green": "red"}
# Every gem a cymbal is built on: the three a right-handed kit draws them with, and the red one a
# left-handed kit draws the green lane with.
COLOURS = ("yellow", "blue", "green", "red")
# A gem widget draws two meshes, the gem and a glow behind it, which is how the game's own
# set_widget_glow can drop a track to one mesh for a distant view. A cymbal draws only the
# first: the glow is a flat bright square of a quad that gets away with it behind a bar,
# because the bar covers it, but round a round plate it shows its corners. The glow is still
# read here, though, because the bright colour on this track comes off the strip it draws
# from rather than off the dull block a gem draws from.
PAD = "prism_gem_drum_%s.mesh"
GLOW = "prism_glow_drum_%s.mesh"
CYMBAL = "cymbal_gem_%s.mesh"
WIDGET = "cymbal_gem_%s.wid"
# The white gem every lane is drawn with inside an overdrive phrase, which the game calls the
# style gem, and the cymbal built on it. One serves all three lanes, since the white gem does.
STYLE = "prism_gem_drum_style.mesh"
STYLE_GLOW = "prism_glow_drum_style.mesh"
STYLE_CYMBAL = "cymbal_gem_style.mesh"
STYLE_WIDGET = "cymbal_gem_style.wid"

# What a cymbal is: a circle 5.0 across, lying flat on the deck the way a cymbal is seen from
# behind a kit, thin at its rim, with its middle raised by the bell. A pad gem arrives 4.86
# across but only 2.17 along, so this reads as a round plate where a pad reads as a bar, which
# is the whole point of it. Points is how many go round each ring: enough that the edge reads
# as a curve rather than a polygon, and no more, since every one of them is another vertex on
# every cymbal on the track. Tilt leans it back, which is left at none: flat is how it reads.
#
# Sits is how far off the deck the lowest point of it stands, and it stands a good way off
# because of what a kick draws. The deck is domed across its width and the kick's bar hugs that
# dome, cresting at 0.164 over the middle of the track against -0.115 at the edges, and the
# flash drawn with it reaches higher still, 0.285 over the same middle. A plate this wide spans
# a whole lane, so with its rim any lower than those the front and back of it went behind the
# bar and the white border round it was swallowed whenever a cymbal and a kick were played
# together - on the yellow and blue lanes, where the dome is at its highest, and not on green,
# where it has fallen away, which is exactly how it read on the console. Standing clear of both
# costs a gap underneath that cannot be told at the angle a track is watched from.
#
# Border is how much of the way in from the edge is drawn in white rather than in the lane's
# colour, measured as a fraction of the half way across. It grows inwards: the edge of the plate
# stays where it is and the white eats into the colour, so widening it leaves the size and the
# profile of the cymbal exactly as they were.
SHAPE = {"across": 2.50, "along": 2.50, "thick": 0.13, "bell": 0.54,
         "tilt": 0.0, "sits": 0.32, "points": 12, "border": 0.13}

# The rings a cymbal is built from, from the middle outwards: how far across it that ring
# sits as a fraction of the whole, how high it stands, and which part of the gem sheet it
# reads. The bell and the body read the core of the bright strip the lane's glow is drawn from
# and the ring where the colour ends reads a shade off it, so a plate is the lane's colour at
# full strength with the last of it falling away - which is how a cymbal reads on the consoles
# that have them. Outside those is the white border, as wide as the shape asks for.
#
# Two rings sit in the same place, one reading the colour and one the white. That is what keeps
# the border crisp: a reading is carried across a triangle from corner to corner, so a triangle
# with the colour at one end and the white at the other would be painted with everything lying
# between the two on the sheet - the whole row of other lanes' strips - smeared round the edge
# of every cymbal. With the two rings apart, no triangle spans the join and the white starts
# where the colour stops.
#
# There are as few of them as the shape can be told by, because a console draws one of these
# for every cymbal on the track at once: they come to 60 vertices and 82 triangles, handed over
# in seven runs of fourteen and under, against a pad gem's 17 and 18 in six runs and the kick
# gem's 68 and 60 in twelve. The underside is not there at all, since a plate lying on the deck
# is only ever seen from above.
def _rings(shape):
    thick, bell = shape["thick"], shape["bell"]
    body, edge = thick + bell * 0.22, thick * 0.45
    starts = 1.0 - shape["border"]
    # The border stands where the slope from the body to the edge has got to by the time it
    # reaches it, so how wide the border is made does not change the shape of the plate.
    seam = body + (edge - body) * (starts - 0.36) / (1.00 - 0.36)
    return ((0.15, thick + bell, "glow middle"),
            (0.36, body, "glow middle"),
            (starts, seam, "glow rim"),
            (starts, seam, "white rim"),
            (1.00, edge, "white rim"))


# A lane can be given a shape of its own, which is how candidates are compared on one disc.
SHAPES = {}

# The longest run of vertices anything in this game hands the hardware at once. Nothing it ships
# goes over fourteen: a bar gem is six runs of nine and under, and the kick's 68 vertices go over
# in twelve runs rather than as one long ribbon, which is what a console's vector unit takes a
# batch at a time. A cymbal was handing over runs of 26, which an emulator draws without
# complaint, and the disc built from the wider border crashed on the console it was made for. So
# a band is cut into runs of this length, each carrying on from the last two vertices of the run
# before it so that no triangle between them is lost.
BATCH = 14


def _batched(strip):
    """One strip cut into the runs a console is handed, longest first.

    Each run starts an even number of steps into the one before it. A strip turns every third
    vertex into another triangle and flips which way round it winds as it goes, so a run that
    started on an odd step would draw its triangles the other way about and be culled - which
    the check on the finished shape would catch, but only after the fact.
    """
    if len(strip) <= BATCH:
        return [strip]
    runs, at = [], 0
    while at < len(strip) - 2:
        runs.append(strip[at:at + BATCH])
        at += BATCH - 2
    return runs

# Every vertex in one of these scenes is painted white, and the four ones that say so are
# what makes a vertex easy to find without knowing the rest of the format. A vertex is its
# position, the way it faces, that white, where it reads the gem sheet, the bones it belongs
# to, and the tangent the light is worked out along.
WHITE = bytes.fromhex("0000803f") * 4
STRIDE = 72
POSITION = 24
SHEET = 40

# The lines that go into the panel. They run once as a song finishes loading, in each track
# scene in turn, and do nothing in a scene without the cymbal meshes in it - which is every
# scene but the drums'. Making the widget by copying the lane's own pad widget is what gives
# it a pad's transform, environment and hit effects; then its meshes are emptied and the
# cymbal put in on its own, one mesh being all it draws.
#
# Every lane is written out longhand, with its objects named rather than worked out from a
# variable, because that is how this console's own scripts do it - a name held in a variable
# has to be turned into an object with object before anything can be said to it, and the
# game's set_widget_glow does exactly that. An array is emptied by removing its first
# element until there is none left, for the same reason: clear_array is not in here.
HOOK = "{$this set_track_out})"
LANE_SCRIPT = """
    {unless
     {exists %(widget)s}
     {do
      ($wid
       {new TrackWidget %(widget)s})
      {$wid copy %(from)s 0}
      {drum_gems.grp add_object $wid}
      {while
       {$wid size (meshes)}
       {$wid remove (meshes 0)}}
      {$wid insert (meshes 0) {object %(mesh)s}}
      {$wid set max_meshes 1}}}"""
SCRIPT = """
  {$this
   iterate
   TrackDir
   $dir
   {with
    $dir
    {if
     {exists %(mesh)s}%(lanes)s
     {$dir sync_objects}}}}"""

# Where each kit's rows live in the table, so that a lane is changed in one kit at a time:
# the same lines are written twice, once for a right-handed kit and once for a left-handed
# one, with the lanes the other way round.
RIGHTY = ("#define DRUM_SET", "#define DRUM_LEFTY")
LEFTY = ("#define DRUM_LEFTY", "(use_char_tex TRUE)")


def _gems():
    """Every cymbal to be built: the gem it is built on, the glow behind that gem it takes its
    colour off, its name, and whose shape it takes."""
    return ([(PAD % colour, GLOW % colour, CYMBAL % colour, colour) for colour in COLOURS]
            + [(STYLE, STYLE_GLOW, STYLE_CYMBAL, "style")])


def _widgets():
    """Every widget the panel makes: its name, the widget it copies, and the mesh it draws."""
    return ([(WIDGET % colour, "drum_%s.wid" % colour, CYMBAL % colour)
             for colour in COLOURS]
            + [(STYLE_WIDGET, "drum_star.wid", STYLE_CYMBAL)])


def _say(log, text):
    if log:
        log(text)


# ---- the meshes -----------------------------------------------------------


def _vertices(body):
    """Where each vertex of one mesh starts.

    The white is what makes the array easy to find, but on its own it is not enough: a vertex
    facing straight up has a 1 of its own right in front of that white, and then the run
    reads as starting a float early. So each candidate is put to the count sitting in front
    of the array, and the one that agrees with it, vertex for vertex, is the array.
    """
    at = body.find(WHITE)
    while at >= 0:
        start = at - POSITION
        if start >= 4:
            count, = struct.unpack_from("<I", body, start - 4)
            if (1 <= count <= 4096 and start + count * STRIDE + 4 <= len(body)
                    and all(body[start + i * STRIDE + POSITION:
                                 start + i * STRIDE + POSITION + len(WHITE)] == WHITE
                            for i in range(count))):
                return [start + i * STRIDE for i in range(count)]
        at = body.find(WHITE, at + 1)
    raise BuildError("A drum gem is not laid out the way this expects, so the cymbals "
                     "cannot be made. Is that folder a Deluxe PS2 release?")


def _read(body):
    """Every vertex of one mesh: where it is, the way it faces, and the rest of it as-is.

    The rest is where it reads the gem sheet, the bones it belongs to and the tangent, none
    of which has to be understood to be carried over to a vertex of my own.
    """
    return [(struct.unpack_from("<3f", body, at),
             struct.unpack_from("<3f", body, at + 12),
             body[at + SHEET:at + STRIDE]) for at in _vertices(body)]


def _unit(vector):
    x, y, z = vector
    long = (x * x + y * y + z * z) ** 0.5 or 1.0
    return x / long, y / long, z / long


def _about(points):
    """The middle of a run of points and how far the furthest of them is from it."""
    mid = tuple((min(p[i] for p in points) + max(p[i] for p in points)) / 2
                for i in range(3))
    far = max(sum((p[i] - mid[i]) ** 2 for i in range(3)) ** 0.5 for p in points)
    return mid, far


# How far down the strip a glow reads the light in it sits. A glow runs from black, up through
# the lane's colour at its brightest, and back to black, so the light is the middle of what it
# reads: the core of the band a shade under half way down, and the shoulder beside the core a
# shade under that. Both are measured off the strip in the sheet on the disc.
CORE, SHOULDER = 0.48, 0.44


def _lit(pad, glow, along):
    """A reading of the sheet taken off the bright strip a lane's glow is drawn from.

    The block a gem reads is dull - the yellow lane's is an olive, three quarters as light as
    the white beside it and nowhere near the yellow seen on the track - because a gem is lit by
    the glow drawn behind it rather than by the block itself. A cymbal has no glow of its own,
    the quad being square where a cymbal is round, so it takes the light the only other way it
    can: by reading the strip the glow would have drawn, which holds the lane's colour at full
    strength. Across, that is the middle of the strip; down, `along` of the way through it.

    Everything but the reading is a pad vertex's, so the bones a cymbal belongs to and the
    tangent its light is worked out along stay the ones that work on a gem.
    """
    ups = [struct.unpack_from("<2f", tail, 0) for _pos, _normal, tail in glow]
    us, vs = [u for u, _v in ups], [v for _u, v in ups]
    tail = max(pad, key=lambda v: v[0][2])[2]
    return struct.pack("<2f", (min(us) + max(us)) / 2,
                       min(vs) + (max(vs) - min(vs)) * along) + tail[8:]


def _anchors(pad, glow, white):
    """The readings of the gem sheet a cymbal is drawn through.

    Three of them come off strips of the sheet the glows are drawn from, which is where colour
    is kept at full strength: two off the strip behind the lane's own gem and one off the white
    the overdrive gem's glow is drawn in, which is the white the border round a cymbal is drawn
    in too. The rest come off the pad vertices playing the same part as the cymbal vertex
    reading them, chosen by where they sit. Nothing new is drawn anywhere either way: every
    reading lands on the sheet the whole track already shares.
    """
    tall = max(v[0][2] for v in pad) / 2
    middle = [v for v in pad if abs(v[0][0]) < 0.1] or pad
    return {"glow middle": _lit(pad, glow, CORE),
            "glow rim": _lit(pad, glow, SHOULDER),
            "white rim": _lit(pad, white, CORE),
            "top middle": max(middle, key=lambda v: v[0][2])[2],
            "bottom middle": min(middle, key=lambda v: v[0][2])[2],
            "top outer": max((v for v in pad if v[0][2] > tall),
                             key=lambda v: abs(v[0][0]))[2],
            "bottom outer": max((v for v in pad if v[0][2] <= tall),
                                key=lambda v: abs(v[0][0]))[2]}


def _round(shape):
    """A cymbal's vertices and the strips that draw them.

    Rings of points round a circle: a bell rising out of the middle, a body sloping gently
    away from it and a rim round the edge. The normals are worked out from how steeply the
    surface runs at each ring rather than averaged off the triangles, so the bell reads as one
    smooth curve and the rim stays a crisp edge instead of being rounded away by its
    neighbours.

    It comes out as strips rather than loose triangles because that is what a console draws:
    a strip walks from one vertex to the next and turns each new one into another triangle, so
    a band between two rings costs a little over two vertices a triangle instead of three, and
    one strip is kicked off rather than dozens. The flat top is a single strip too, crossing
    the ring from side to side.
    """
    across, along = shape["across"], shape["along"]
    points = shape["points"]
    rings = _rings(shape)
    # How steeply the surface runs where each ring sits, from the ring inside it to the ring
    # outside, which is the slope its normal leans back from.
    slopes = []
    for at, ring in enumerate(rings):
        inner, outer = rings[max(0, at - 1)], rings[min(len(rings) - 1, at + 1)]
        run = (outer[0] - inner[0]) * (across + along) / 2
        slopes.append((outer[1] - inner[1]) / run if run else 0.0)

    verts = []
    for at, (out, high, role) in enumerate(rings):
        for turn in range(points):
            angle = 2 * math.pi * turn / points
            cx, cy = math.cos(angle), math.sin(angle)
            radial = _unit((cx / across, cy / along, 0.0))
            normal = _unit((-slopes[at] * radial[0], -slopes[at] * radial[1], 1.0))
            verts.append(((across * out * cx, along * out * cy, high), normal, role))

    # The flat top: crossing from one side of the ring to the other and back turns a ring of
    # points into one strip, two triangles short of the number of points.
    top = list(range(points))
    strips = _batched([top[0]] + [top[(1 + step // 2) if step % 2 == 0 else -(1 + step // 2)]
                                  for step in range(points - 1)])
    for at in range(len(rings) - 1):
        # Two rings in the same place are a join in the readings rather than a surface, so
        # there is nothing to draw between them.
        if rings[at][:2] == rings[at + 1][:2]:
            continue
        above, below = at * points, (at + 1) * points
        strip = []
        for turn in range(points + 1):
            strip += [above + turn % points, below + turn % points]
        strips += _batched(strip)
    return verts, strips


def _triangles(strips):
    """The triangles a run of strips draws, wound the way a console winds them.

    Every third vertex of a strip makes another triangle, and every other one of those comes
    out the other way about, which is what the swap is for.
    """
    faces = []
    for strip in strips:
        for at in range(len(strip) - 2):
            one, two, three = strip[at:at + 3]
            if len({one, two, three}) == 3:
                faces.append((one, two, three) if at % 2 == 0 else (two, one, three))
    return faces


def _facing(verts, faces):
    """Whether every triangle faces the way the vertices on it say it should.

    A triangle drawn the wrong way about is culled, so a strip written backwards would leave
    holes in a cymbal, or the whole of it missing. Since the normals here are worked out from
    the shape rather than from the triangles, the two can be checked against each other.
    """
    for one, two, three in faces:
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = (verts[i][0] for i in (one, two, three))
        edge = ((by - ay) * (cz - az) - (bz - az) * (cy - ay),
                (bz - az) * (cx - ax) - (bx - ax) * (cz - az),
                (bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
        said = [sum(verts[i][1][axis] for i in (one, two, three)) / 3 for axis in range(3)]
        if sum(edge[axis] * said[axis] for axis in range(3)) <= 0:
            return False
    return True


def _place(verts, shape):
    """Lean a shape back the way a cymbal sits on its stand, and sit it clear of the deck.

    How far up it goes is worked out from the cymbal itself whatever is being placed, so the
    light behind a cymbal is moved by the same amount and the two stay together.
    """
    lean = math.radians(shape["tilt"])
    fall, rise = math.cos(lean), math.sin(lean)
    up = shape["sits"] - min(y * rise + z * fall
                             for (_x, y, z), _n, _r in _round(shape)[0])
    return [((x, y * fall - z * rise, y * rise + z * fall + up),
             (nx, ny * fall - nz * rise, ny * rise + nz * fall), role)
            for (x, y, z), (nx, ny, nz), role in verts]


def _geometry(verts, strips):
    """The part of a mesh body from the vertex count on: the vertices, the triangles, and
    the strips a console actually draws.

    The strips are what the console reads, and the face list is written from them rather than
    beside them, so the two cannot fall out of step. Each strip says where it ends counting
    from the start of the run, so the ends are added up as they go.
    """
    faces = _triangles(strips)
    indices = [at for strip in strips for at in strip]
    out = bytearray(struct.pack("<I", len(verts)))
    for pos, normal, sheet in verts:
        out += struct.pack("<3f", *pos) + struct.pack("<3f", *normal) + WHITE + sheet
    out += struct.pack("<I", len(faces))
    for face in faces:
        out += struct.pack("<3H", *face)
    out += struct.pack("<2I", 1, len(faces)) + b"\x00"
    out += struct.pack("<2I", len(strips), len(indices))
    ends = 0
    for strip in strips:
        ends += len(strip)
        out += struct.pack("<I", ends)
    for at in indices:
        out += struct.pack("<H", at)
    return bytes(out)


def _sphere(head, was):
    """Where a header keeps the sphere its shape is culled against.

    Four floats, a centre and a radius round the geometry, at no fixed offset since the names
    in front of them are as long as they are. What is fixed is where they sit relative to the
    material's name, and the material's name is the string ending where the mesh's own name
    begins, so that is what is counted back from.
    """
    tag = struct.pack("<I", len(was)) + was.encode("latin-1")
    owner = head.find(tag)
    for length in range(1, 65):
        start = owner - 4 - length
        if start < 20:
            continue
        size, = struct.unpack_from("<I", head, start)
        if size == length and all(32 <= c < 127 for c in head[start + 4:start + 4 + length]):
            return start - 20
    raise BuildError("Nothing in %s names the material it is drawn with, so where it says "
                     "what it is culled against cannot be found." % was)


def _head(body, was, name, points):
    """A pad gem's header, made over for geometry of a different shape.

    Two things in there have to move with the shape. A mesh says whose geometry it draws by
    name and a gem names itself, so the copy names itself or it draws the pad it came from.
    And the sphere it is culled against has to be the sphere round the new shape, or a cymbal
    would be culled as the size of the pad it stands in for.
    """
    tag = struct.pack("<I", len(was)) + was.encode("latin-1")
    if body.count(tag) != 1:
        raise BuildError("%s does not name itself once, so the cymbal built on it would "
                         "draw the shape it was built on." % was)
    head = bytearray(body[:_vertices(body)[0] - 4].replace(
        tag, struct.pack("<I", len(name)) + name.encode("latin-1")))

    at = _sphere(head, name)
    was_mid, was_far = _about([pos for pos, _n, _s in _read(body)])
    said = struct.unpack_from("<4f", head, at)
    # The game rounds its own sphere a little, so this only has to be the same sphere, not
    # the same numbers. Anything further out than that is not the sphere and nothing is
    # written over on a guess.
    if (any(abs(said[i] - was_mid[i]) > 0.05 for i in range(3))
            or abs(said[3] - was_far) > 0.1):
        raise BuildError("%s does not say what it is culled against where this expects, so "
                         "a cymbal built on it could be culled as the wrong size." % was)
    mid, far = _about([pos for pos, _n, _r in points])
    struct.pack_into("<4f", head, at, mid[0], mid[1], mid[2], far)
    return bytes(head)


def _cymbal(body, glow, white, was, name, shape=SHAPE):
    """A round cymbal gem, on the header, the material and the sheet of one pad gem."""
    verts, strips = _round(shape)
    verts = _place(verts, shape)
    if not _facing(verts, _triangles(strips)):
        raise BuildError("A cymbal came out with its strips the wrong way about, which would "
                         "leave holes in it or nothing at all where it should be.")
    sheet = _anchors(_read(body), _read(glow), _read(white))
    return (_head(body, was, name, verts)
            + _geometry([(pos, normal, sheet[role]) for pos, normal, role in verts], strips))


def _scene(settings, rel, log=None):
    """Put a cymbal gem beside each pad gem in one scene.

    Every drum track in the file is served, since the panel two players share holds one per
    player, and the meshes have to be there whichever track the drummer is on.
    """
    live = os.path.join(settings.ark_build, *rel.split("/"))
    if not os.path.exists(live):
        raise BuildError("%s is not in the unpacked game, so the cymbals cannot be drawn. "
                         "Is that folder a Deluxe PS2 release?" % rel)
    raw, splits = milo.inflate(live)
    before = milo.dirs(raw, "TrackDir")
    drums = [track for track in before if track.holds(PAD % LANES[0])]
    if not drums:
        raise BuildError("No drum track in %s holds %s, so there is nothing to make a "
                         "cymbal out of." % (rel, PAD % LANES[0]))

    wanted = []
    pokes, splices = [], []
    for track in drums:
        for was, glow, name, shape in _gems():
            for held in (was, glow, STYLE_GLOW):
                if not track.holds(held):
                    raise BuildError("A drum track in %s does not hold %s, so there is nothing "
                                     "to make a cymbal out of." % (rel, held))
            if track.holds(name):
                raise BuildError("A drum track in %s already holds %s." % (rel, name))
            body = _cymbal(track.body(was), track.body(glow), track.body(STYLE_GLOW),
                           was, name, SHAPES.get(shape, SHAPE))
            track.insert(was, "Mesh", name, body)
            wanted.append((before.index(track), name, body))
        mine, theirs = track.changes()
        pokes += mine
        splices += theirs

    work = settings.work_dir("prodrums")
    made = os.path.join(work, os.path.basename(rel))
    with open(made, "wb") as fp:
        fp.write(milo.repack(raw, splits, pokes, splices))

    # Read it back the way the console will. A scene it cannot parse takes the track down as
    # the song starts, so both halves are checked: the cymbals arrived in the track they
    # were meant for and read back byte for byte, and every object that was there before
    # came through untouched. The tracks are matched up in the order they sit in the file,
    # since a panel two players share names both of its drum tracks the same.
    over, _splits = milo.inflate(made)
    after = milo.dirs(over, "TrackDir")
    if len(after) != len(before):
        raise BuildError("%s reads back with %d tracks in it rather than %d, so it is not "
                         "safe to put on a disc." % (rel, len(after), len(before)))
    for which, name, body in wanted:
        track = after[which]
        if not track.holds(name) or track.body(name) != body:
            raise BuildError("%s does not read back out of %s, so it is not safe to put "
                             "on a disc." % (name, rel))
    for was, now in zip(before, after):
        for name, (start, end) in was.bodies.items():
            there, to = now.bodies.get(name, (0, 0))
            if raw[start:end] != over[there:to]:
                raise BuildError("%s in %s does not read back as it was, so it is not "
                                 "safe to put on a disc." % (name, rel))
    shutil.copyfile(made, live)
    _say(log, "  %s: %d cymbal gems on each of its %d drum tracks, and it reads back clean"
              % (os.path.basename(rel), len(_gems()), len(drums)))


def _meshes(settings, log=None):
    """Put the cymbal gems into every scene a drum track is drawn from."""
    for rel in SCENES:
        _scene(settings, rel, log=log)


# ---- the scripts ----------------------------------------------------------


def _rows(text):
    """What to look for to find a run of lines, however it happens to be laid out.

    A script read back out of the game is laid out by the tool that read it, indented as deep
    as the thing it sits inside, so an edit that spans more than one line cannot be written out
    with the spacing it will be found with. What can be relied on is the words and the order
    they come in, so any gap between words matches any other.
    """
    return re.compile(r"\s+".join(re.escape(word) for word in text.split()))


def _edit(settings, rel, edits, what, within=None):
    """Change lines in one of the game's compiled scripts, in the tree to be packed.

    Each edit has to match exactly once, and is looked for by its words rather than by its
    spacing. Where within is given the match is looked for inside that block only, because the
    gem table holds a right-handed kit and a left-handed one written the same way. Edits are
    made in the order they are given, so one may look for what an earlier one put in. The
    result is read back the way the game will read it and compared before it replaces
    anything: a script the game cannot parse takes the whole track down as the song starts.
    """
    dtab = settings.tool("dtab")
    live = os.path.join(settings.ark_build, *rel.split("/"))
    if not os.path.exists(live):
        raise BuildError("%s is not in the unpacked game, so the cymbals cannot be "
                         "drawn. Is that folder a Deluxe PS2 release?" % rel)
    work = settings.work_dir("prodrums")
    stem = os.path.join(work, os.path.basename(rel))

    def convert(step, args):
        if os.path.exists(args[-1]):
            os.remove(args[-1])
        r = proc.run([dtab] + args, capture_output=True, text=True)
        if not os.path.exists(args[-1]):
            raise BuildError("Could not %s %s: %s"
                             % (step, what, (r.stderr or r.stdout).strip()[:200]))

    convert("decrypt", ["-d", live, stem + ".plain"])
    convert("read", ["-a", stem + ".plain", stem + ".dta"])
    with open(stem + ".dta", encoding="latin-1", errors="replace") as fp:
        before = fp.read()

    head = found = sep = tail = ""
    body = before
    if within:
        opens, closes = within
        head, found, rest = before.partition(opens)
        body, sep, tail = rest.partition(closes)
        if not (found and sep):
            raise BuildError("%s is not laid out the way this expects, so the cymbals "
                             "cannot be drawn." % what.capitalize())
    for old, new in edits:
        spots = _rows(old).findall(body)
        if len(spots) != 1:
            raise BuildError("Expected one %s in %s, found %d."
                             % (" ".join(old.split())[:60], what, len(spots)))
        body = body.replace(spots[0], new)
    after = head + found + body + sep + tail

    with open(stem + " edited.dta", "w", encoding="latin-1", errors="replace") as fp:
        fp.write(after)
    convert("compile", ["-b", stem + " edited.dta", stem + ".built"])
    convert("encrypt", ["-e", stem + ".built", stem + ".out"])
    convert("check", ["-d", stem + ".out", stem + " back.dtb"])
    convert("check", ["-a", stem + " back.dtb", stem + " back.dta"])
    with open(stem + " back.dta", encoding="latin-1", errors="replace") as fp:
        again = fp.read()
    # Word for word rather than character for character: what comes back has been written
    # out again by the same tool that read it, and it breaks long lines where it sees fit.
    if again.split() != after.split():
        raise BuildError("The edit to %s does not read back as it was written, so it is "
                         "not safe to put on a disc." % what)
    shutil.copyfile(stem + ".out", live)


# ---- the executable -------------------------------------------------------

# Where the executable keeps the names of the gem table's rows, built once as the first note is
# drawn and kept eight bytes apart in the order it builds them: ordinary, star, unison,
# invisible, bonus, tom. The rewritten code below picks one of them by adding to the first.
ROWS = 0x69B0A0
ORDINARY, STAR, UNISON, TOM = 0, 8, 16, 40

# A note says which lanes it is played on as one bit per lane, counted the way the gem table
# numbers them: the kick, the snare, and then the three that can hold a cymbal. Neither the
# kick nor the snare is ever a cymbal, so the chart is never asked about them.
#
# Only those three bits are read, and everything above them is thrown away rather than walked.
# The byte is a byte and a lane is a lane: whatever the game keeps in the top three bits of it,
# a loop that walks every bit set would go on to ask the chart about lanes five, six and seven,
# and the chart would answer out of whatever sits past the end of its own lanes.
FIRST_CYMBAL_LANE = 2

# The block that picks a row, and the word after it, which everything falls out to with the
# chosen name in hand. Nothing in the game jumps into the middle of the block - it is entered
# in one place and left in one - which is what makes it safe to write over as a whole. There is
# room for forty five instructions and the rewrite uses forty four.
#
# The fingerprint is of the instructions being replaced. It is checked first, so a release this
# was not worked out against is refused rather than quietly scrambled.
PICK = 0x3D588C
PICK_OUT = 0x3D5940
PICK_WAS = "59a5daa19223bc0c606c7de1ec27489ab8764a0c"

# What the block did: ask whether the note is an overdrive one, and if it is, draw it with the
# star row unless the phrase is a unison phrase, in which case the unison row; and otherwise
# draw it with the tom row if the chart calls it a tom and the ordinary row if not.
#
# What it does now: work out which row to draw from as before, but ask the chart about the note
# on drum tracks whichever kind of note it is. So an overdrive cymbal draws the unison row and
# an overdrive tom draws the star row, which is where the white cymbal gem comes from. Away from
# a drum track nothing has changed: the unison row is still chosen by the game's own answer to
# whether this is a unison phrase, so guitar and bass are left exactly as they were.
#
# The two paths meet, which is the point: each arrives with the row it would have drawn from and
# how far along to move for a tom, and one shared piece of code asks the question. Everything
# that has to survive a call is kept in the registers a call has to preserve: the row, how far a
# tom moves from it, the answer so far, the lanes still to ask about, and which lane that is.
#
# Which lane to ask the chart about is the part worth reading twice. A note is one note however
# many lanes are played on it, so a crash and the kick under it are one note lighting two lanes,
# and the table names one gem for a note rather than one per lane. The game asked about the
# lowest lane a note lights and let that answer stand for the whole of it, which is harmless
# when every row names the same gem and ruinous now: a crash is nearly always played over a kick
# or the snare, both of which sit below it and neither of which is ever a cymbal, so the answer
# came back tom and a green cymbal was drawn as a pad for most of a song.
#
# So the two lanes that cannot hold a cymbal are skipped and every one that can is asked about,
# and the note draws cymbals if any lane it lights is one. A tom played together with a cymbal
# draws a cymbal too, which cannot be helped from here - one gem is named for the note, not one
# per lane - and it is the rarer way round to be wrong by some distance.
PICK_CODE = """
    daddu a0, s2, zero
    jal   0x3d5da0                 ; is this note an overdrive one at all
    daddu a1, s4, zero
    bnez  v0, pads
    daddu a0, s2, zero
    ori   a2, sp, 4
    jal   0x3d5bd8                 ; and is it inside an overdrive phrase
    daddu a1, s4, zero
    beqz  v0, pads
    lui   s0, %(high)#x
    addiu s0, s0, %(unison)d       ; from the unison row
    lw    s7, 4(sp)
    addiu s1, zero, %(back)d       ; a tom moves back to the star row
    b     both
    sltiu s7, s7, 1                ; and so does anything in a phrase of its own
pads:
    lui   s0, %(high)#x
    addiu s0, s0, %(ordinary)d     ; from the ordinary row
    addiu s1, zero, %(along)d      ; a tom moves along to the tom row
    daddu s7, zero, zero
both:
    jal   0x3db720                 ; is this a drum track
    lw    a0, 4(s2)
    beqz  v0, pick
    lw    t0, 0(s3)
    addu  t0, t0, s5
    lbu   s2, 0xc(t0)              ; the lanes this note is played on
    srl   s2, s2, %(first)d        ; the kick and the snare hold no cymbal, so drop them
    andi  s2, s2, %(cymbals)d      ; and nothing above the lanes that can is a lane at all
    addiu s3, zero, %(first)d      ; start from the first lane that can
lane:
    andi  v0, s2, 1
    beqz  v0, next                 ; this lane is not one of the ones played
    srl   s2, s2, 1
    lw    a0, 0x45f0(fp)           ; the chart, which the game keeps to hand here
    daddu a1, s3, zero
    daddu a3, s3, zero             ; the lane, which the chart is asked for twice over
    jal   0x1a3758                 ; does the chart call that lane a tom here
    daddu a2, s6, zero
    beqz  v0, pick                 ; one cymbal in the note and the note draws cymbals
    daddu s7, v0, zero             ; every lane so far says tom, and that stands if all do
next:
    bnez  s2, lane                 ; more lanes left to ask about
    addiu s3, s3, 1
pick:
    beql  s7, zero, take
    daddu s1, zero, zero           ; not a tom, so stay on the row we came in with
take:
    addu  v0, s0, s1
    b     %(out)#x
    lw    v0, 0(v0)
""" % {"high": mips.halves(ROWS)[0],
       "ordinary": mips.halves(ROWS + ORDINARY)[1],
       "unison": mips.halves(ROWS + UNISON)[1],
       "back": STAR - UNISON,
       "along": TOM - ORDINARY,
       "first": FIRST_CYMBAL_LANE,
       "cymbals": (1 << len(LANES)) - 1,
       "out": PICK_OUT}


WRONG_ELF = ("%s does not hold the code this knows how to rewrite, so the cymbals cannot be "
             "told apart inside an overdrive phrase. That code was worked out against the "
             "Deluxe PS2 release's own executable.")


def trouble(settings):
    """Why this release cannot have the cymbals, if it cannot, in one sentence.

    Asked before a build starts, because the songs take hours and the executable is rewritten
    once they are all done. Anything wrong with the release itself is left to the settings to
    report, this being only about what the executable holds.
    """
    try:
        base = settings.base_elf()
    except Exception:
        return None
    try:
        was = mips.Executable(base).read(PICK, PICK_OUT - PICK)
    except (BuildError, OSError):
        return WRONG_ELF % os.path.basename(base)
    if hashlib.sha1(was).hexdigest() != PICK_WAS:
        return WRONG_ELF % os.path.basename(base)
    return None


def _overdrive(settings, log=None):
    """Rewrite the block of the executable that picks which row of the gem table to draw from.

    The copy the disc is built from is the one written to. The game's own folder is never
    touched, and a build with the cymbals switched off ships the executable as it shipped.
    """
    base = settings.base_elf()
    elf = mips.Executable(base)
    was = elf.read(PICK, PICK_OUT - PICK)
    if hashlib.sha1(was).hexdigest() != PICK_WAS:
        raise BuildError(WRONG_ELF % os.path.basename(base))
    code = mips.assemble(PICK_CODE, PICK)
    if len(code) > len(was):
        raise BuildError("The rewritten code is %d instructions and there is room for %d."
                         % (len(code) // 4, len(was) // 4))
    elf.write(PICK, code + b"\x00" * (len(was) - len(code)))
    elf.save(os.path.join(settings.elf_out, os.path.basename(base)))
    _say(log, "  the executable: %d instructions rewritten, so an overdrive cymbal draws the "
              "unison row and an overdrive tom the star row" % (len(code) // 4))


def clear(settings):
    """Throw away an executable a build left patched, so the next one starts from the game's.

    A build with the cymbals switched off has to ship the executable the game shipped, and the
    patched copy sits in the work folder from one build to the next, so it goes first.
    """
    shutil.rmtree(settings.elf_out, ignore_errors=True)


def _block():
    """The lines the panel gains, one widget after another, with every name written out."""
    made = "".join(LANE_SCRIPT % {"widget": widget, "from": copies, "mesh": mesh}
                   for widget, copies, mesh in _widgets())
    return SCRIPT % {"mesh": CYMBAL % LANES[0], "lanes": made}


def _unison(colour):
    """The edit sending one lane's unison row to the white cymbal.

    A lane's unison row is found by the rows around it rather than on its own, because every
    lane of the kit holds the same one word for word. The tom row in front of it names the gem
    that lane is drawn with, so the three together belong to that lane and nothing else.
    """
    return ("(tom drum_%s.wid) (star drum_star.wid) (unison drum_star.wid)" % colour,
            "(tom drum_%s.wid)\n  (star drum_star.wid)\n  (unison %s)"
            % (colour, STYLE_WIDGET))


def apply(settings, log=None):
    """Draw the cymbals in the copy of the game about to be packed."""
    _say(log, "cymbals onto a gem of their own, so shape says cymbal and the toms keep "
              "their pads:")
    _meshes(settings, log=log)
    _overdrive(settings, log=log)
    _edit(settings, PANEL, ((HOOK, HOOK[:-1] + _block() + ")"),),
          "the track panel")
    _say(log, "  the panel makes %d cymbal widgets as a song loads" % len(_widgets()))
    _edit(settings, TABLE,
          tuple(("(normal drum_%s.wid)" % lane, "(normal %s)" % (WIDGET % lane))
                for lane in LANES) + tuple(_unison(lane) for lane in LANES),
          "the gem table", within=RIGHTY)
    _say(log, "  right-handed kit: %d lanes draw a cymbal for a cymbal, in overdrive too"
              % len(LANES))
    # The same three lanes of the chart, named by the gem this kit draws each of them with,
    # which is the gem from the far side of the track. The snare's row is the one naming the
    # green gem here, and it is left alone.
    mirrored = [LEFTY_GEMS[lane] for lane in LANES]
    _edit(settings, TABLE,
          tuple(("(normal drum_%s.wid)" % colour, "(normal %s)" % (WIDGET % colour))
                for colour in mirrored) + tuple(_unison(colour) for colour in mirrored),
          "the gem table", within=LEFTY)
    _say(log, "  left-handed kit: the same three lanes of the chart, drawn with the %s gems "
              "this kit mirrors them onto" % ", ".join(mirrored))
