"""The modifiers a disc has switched on by the time you reach the menu.

Deluxe's Modifiers screen is a list of things you can turn on for a session: No Fail,
Breakneck Speed, Autoplay, and a dozen more Deluxe added. A PlayStation 2 forgets all
of them. Nothing on this console writes the active set anywhere, so whatever was on
last night is off again the next time the game starts, and anyone who always plays with
one of these has to go and tick it again first. What this does is put the ticking on the
disc.

Where the list comes from: config/gen/modifiers.dtb registers every modifier the game
knows about, wrapped in the platform conditionals the console resolves as it reads the
file - a PS2 has HX_EE defined, and dx/config/gen/dx_macros.dtb turns that into MHX_OG
as well - which leaves eighteen. Another eight are in that file for other consoles, and
thirty more have a name in the locale but are never registered.

Twelve of those eighteen are here, because being on the Modifiers screen is not the same
as working. Something has to read a modifier, and whatever the reading leads to has to
exist in this build. Six do not survive that test and are left out:

  Auto Kick               its one line calls beatmatch set_kick_autoplay, and no such
                          method is in the PS2 executable. Worse, the modifier still
                          stops the game saving, so it costs scores and gives nothing
  Freestyle Drums         wants set_fake_hit_gems_in_fill and force_fill, neither of
                          which the executable has
  Sync Difficulty Speeds  read in one place, inside a block built for other consoles
  Midi Drum Bass Kick Fix nothing in the release reads it
  No SELECT in Practice   nothing in the release reads it
  Awesomeness Detection   nothing reads it, and its own description promises nothing
                          you could see anyway

Auto Kick was tested on hardware: on and ticked, by hand or from the disc, the kick
still had to be played. research/modcheck.py is how the rest were worked out.

How they get switched on: ui/gen/init.dtb ends by including dx/ui/gen/dx_init.dta, so
that script is the last thing the interface runs as it starts. On the consoles with
somewhere to keep settings, Deluxe already switches modifiers on from exactly there: it
reads a dx.dta off the hard drive and calls activate_modifier for everything it finds
turned on. A PS2 has no such file and that whole reader is compiled out of this build,
so the calls are appended to the end of the same script instead, which is late enough
that every manager the game needs is up - the line before ours already talks to one.
The block asks whether the modifier manager is there at all before using it, so a
release that has moved it still boots; it simply starts with nothing on.

Nothing here is locked in. Every one of these is still a line on the Modifiers screen
and can be turned off there for the rest of the session.

The edit happens in the copy of the game each build makes, after the songs are injected
and before the archive is packed, so the unpacked original is never touched.
"""

import collections
import os
import shutil

from . import proc
from .errors import BuildError

# The script the interface runs last as it starts, and so the one the calls go on the
# end of.
SCRIPT = "dx/ui/gen/dx_init.dtb"

# Who added which, which is how the page groups them.
GAME = "the game's own"
DELUXE = "added by Deluxe"

# What a modifier costs while it is on, as the game itself puts it in modifiers.dtb.
SAVING = "saving"
ACHIEVEMENTS = "achievements"
ONLINE = "online play"

# A modifier and what the game does with it. The three tuples are its own handlers out
# of modifiers.dtb: what switching it on switches off, what switching it on switches on
# as well, and what switching it off takes with it. Calls to modifiers this does not
# offer are left out, since nothing there can be on to switch off.
Modifier = collections.namedtuple(
    "Modifier",
    "name label whose note costs on_deactivates on_activates off_deactivates")

# Every modifier a PS2 both registers and does something with, in the order the game's
# own screen lists them. The notes are the game's own words, so what is written beside a
# tick here is what is written under it on the console. The one exception is Black
# Background, whose own description recommends it for a PlayStation 3 emulator's
# performance. What reads each one on this console, since that is what decided the list:
#
#   No Fail Mode        the executable, which is the only modifier it names
#   Breakneck Speed     its own handler, through profile_mgr set_doublespeed_mode
#   Performance Mode    the game's own, shipped working on this console
#   Unlock All Songs    its own handler, through profile_mgr unlock_all_songs
#   Black Background    ui/gen/game.dta, which stops drawing the world panel
#   Song Title Always On  track/gen/mtv_overlay.dta and ui/gen/mtv_overlay.dta
#   Legacy Drum Fills   config/gen/beatmatcher.dta, at every downbeat
#   Pad is Guitar/Drum  config/gen/band.dta, in the controller mapping
#   Calibration Mode    config/gen/beatmatcher.dta, which sets the hit window to 15
#   Autoplay            ui/gen/game.dta, through set_auto_play and deploy_if_possible
#   Jukebox Mode        track/gen/mtv_overlay.dta, and the two above that it brings on
CATALOGUE = (
    Modifier("mod_nofail", "No Fail Mode", GAME,
             "Never fail out of a song!", (), (), (), ()),
    Modifier("mod_doublespeed", "Breakneck Speed", GAME,
             "Increase the scrolling speed of instrument tracks!", (), (), (), ()),
    Modifier("mod_nohud", "Performance Mode", GAME,
             "Play the game without a track!",
             (), ("mod_blackvenue",), (), ("mod_fakejuke",)),
    Modifier("mod_unlockall", "Unlock All Songs", GAME,
             "Unlock every song in the game!",
             (SAVING, ACHIEVEMENTS), (), (), ()),
    Modifier("mod_blackvenue", "Black Background", DELUXE,
             "Completely black background in-game. Good for focus.",
             (), ("mod_nohud",), (), ()),
    Modifier("mod_songtitle", "Song Title Always On", DELUXE,
             "Recommended for streamers", (), (), (), ()),
    # Its handler also switches Freestyle Drums off, which is not offered here.
    Modifier("mod_staticfills", "Legacy Drum Fills", DELUXE,
             "I just really like not hitting all of the notes okay?",
             (), (), (), ()),
    Modifier("mod_padguitar", "Pad is Guitar", DELUXE,
             "Good ol' fashioned 5 fret pad play", (), ("mod_paddrum",), (), ()),
    Modifier("mod_paddrum", "Pad is Drum", DELUXE,
             "Good ol' fashioned 4 lane pad play", (), ("mod_padguitar",), (), ()),
    Modifier("mod_calibration", "Calibration Mode", DELUXE,
             "Drastically shrinks the hit window to assist with calibration",
             (), (), (), ()),
    Modifier("mod_auto_play", "Autoplay", DELUXE,
             "Ruin the leaderboards and your credibility all at once!",
             (SAVING, ACHIEVEMENTS, ONLINE), ("mod_staticfills",), (),
             ("mod_fakejuke",)),
    Modifier("mod_fakejuke", "Jukebox Mode", DELUXE,
             "Watch your band rock out!",
             (SAVING, ACHIEVEMENTS, ONLINE), (),
             ("mod_auto_play", "mod_nohud"), ("mod_auto_play", "mod_nohud")),
)

BY_NAME = {mod.name: mod for mod in CATALOGUE}


def _say(log, text):
    if log:
        log(text)


def known(names):
    """The modifiers in a saved list a disc can switch on, in the screen's order."""
    wanted = set(names or ())
    return [mod.name for mod in CATALOGUE if mod.name in wanted]


def label(name):
    mod = BY_NAME.get(name)
    return mod.label if mod else name


def outcome(names):
    """What the disc really starts with, and where that differs from the ticks.

    The calls are written in the order the game's own screen lists them, and the game
    runs each modifier's handler as it goes on, so one tick can bring a second modifier
    with it or switch an earlier one straight back off. Ticking Black Background and
    Jukebox Mode together, for instance, leaves Black Background off, because Jukebox
    Mode turns Performance Mode on and those two cannot both be on. Working that out
    here is better than shipping a disc that surprises somebody.

    Returns the names left on, in screen order, and a line about each difference.
    """
    on = []
    notes = []

    def activate(name, by=None):
        mod = BY_NAME.get(name)
        if mod is None or name in on:
            return
        on.append(name)
        if by:
            notes.append("%s brings %s on as well" % (label(by), label(name)))
        for other in mod.on_deactivates:
            deactivate(other, name)
        for other in mod.on_activates:
            activate(other, name)

    def deactivate(name, by):
        if name not in on:
            return
        on.remove(name)
        notes.append("%s switches %s off again" % (label(by), label(name)))
        for other in BY_NAME[name].off_deactivates:
            deactivate(other, name)

    for name in known(names):
        activate(name)
    return [mod.name for mod in CATALOGUE if mod.name in on], notes


def _block(names):
    """The lines appended to the script: one call each, in the screen's order."""
    calls = "\n".join(" {modifier_mgr activate_modifier %s}" % name
                      for name in names)
    return "{if\n {exists modifier_mgr}\n%s}\n" % calls


def apply(settings, log=None):
    """Write the chosen modifiers into the copy of the game about to be packed."""
    names = known(settings.modifiers)
    if not names:
        return
    dtab = settings.tool("dtab")
    live = os.path.join(settings.ark_build, *SCRIPT.split("/"))
    if not os.path.exists(live):
        raise BuildError("%s is not in the unpacked game, so the modifiers cannot be "
                         "switched on. Is that folder a Deluxe PS2 release?" % SCRIPT)
    work = settings.work_dir("modifiers")

    def convert(step, args):
        if os.path.exists(args[-1]):
            os.remove(args[-1])
        r = proc.run([dtab] + args, capture_output=True, text=True)
        if not os.path.exists(args[-1]):
            raise BuildError("Could not %s the startup script: %s"
                             % (step, (r.stderr or r.stdout).strip()[:200]))

    plain = os.path.join(work, "dx_init.dtb")
    text_path = os.path.join(work, "dx_init.dta")
    convert("decrypt", ["-d", live, plain])
    convert("read", ["-a", plain, text_path])
    with open(text_path, encoding="latin-1", errors="replace") as fp:
        before = fp.read()
    if "activate_modifier" not in before:
        raise BuildError("%s does not look like Deluxe's startup script - nothing in "
                         "it switches a modifier on - so the modifiers cannot be set."
                         % SCRIPT)

    after = before.rstrip("\n") + "\n" + _block(names)
    edited = os.path.join(work, "dx_init edited.dta")
    with open(edited, "w", encoding="latin-1", errors="replace") as fp:
        fp.write(after)
    built = os.path.join(work, "dx_init built.dtb")
    ship = os.path.join(work, "dx_init.out")
    convert("compile", ["-b", edited, built])
    convert("encrypt", ["-e", built, ship])

    # Read it back the way the console will: a startup script it cannot parse is a disc
    # that does not reach its menu. Word for word rather than character for character,
    # because what comes back has been written out again by the same tool that read it
    # and it breaks long lines where it sees fit.
    back_plain = os.path.join(work, "dx_init back.dtb")
    back_text = os.path.join(work, "dx_init back.dta")
    convert("check", ["-d", ship, back_plain])
    convert("check", ["-a", back_plain, back_text])
    with open(back_text, encoding="latin-1", errors="replace") as fp:
        again = fp.read()
    if again.split() != after.split():
        raise BuildError("The edited startup script does not read back as it was "
                         "written, so it is not safe to put on a disc.")

    shutil.copyfile(ship, live)
    _say(log, "switched on as the game starts: %s"
              % ", ".join(label(name) for name in names))
    ends_on, notes = outcome(names)
    for line in notes:
        _say(log, "  " + line)
    if notes:
        _say(log, "  so the disc starts with %s"
                  % ", ".join(label(name) for name in ends_on))
