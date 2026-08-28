# Rock Band 2 Deluxe Disc Builder

by PhayMo

Builds a custom Rock Band 2 Deluxe disc for the PlayStation 2 from folders of
Clone Hero songs. You choose the songs, it converts the audio, charts, album art
and background video into the formats the console expects, packs them into the
game's archive and writes a bootable ISO. Everything it produces has been tested
on real hardware.

![The Songs page](docs/songs-page.png)

## What you need

- **Windows.** The chart converter runs Magma, the official Rock Band compiler.
- **Your own copy of Rock Band 2 Deluxe for PS2**, unpacked - the folder holding
  `SLUS_218.00` and `gen\MAIN_0.ARK`. No game files are included here.
- **Songs in Clone Hero layout**: `notes.mid` or `notes.chart`, `song.ini`, the
  audio and `album.png`. Separate stems are ideal, since the game can then mute
  each part as it is missed, but one mixed `song.ogg` works too, as do `.wav`,
  `.mp3` and `.opus`.
- **`ps2str.exe`** from Sony's PS2 SDK, which cannot be distributed with this.
  Everything else - FFmpeg, Onyx, dtab, Mackiloha - the Setup page downloads.
- **Python 3.8 or newer** if you run from the source. The packaged `.exe` needs
  nothing installed.
- About 30 GB free for the work folder. A hundred-song disc takes a couple of
  hours.

![The tools list, once everything is in place](docs/tools-page.png)

A song offers exactly the parts its chart plays, so a guitar-only chart becomes a
guitar-only song. Stems for a part the chart skips go into the backing track
rather than being dropped. Lyrics on their own are not a vocals part.

Eleven background clips come with it, in `venues\`, one picked per song and
looped behind it - drop your own in there instead. A song holding its own `video`
or `background` file plays that, in any format Clone Hero accepts, and
`song.ini`'s `video_start_time` is honoured. Or set *Background* to *Black* and
have none at all, which fits far more songs.

## Running it

Unzip the release and run `RB2DX Disc Builder.exe`. The first start takes a good
twenty seconds while it unpacks itself, with nothing on screen until the window
appears. From the source, `run.bat`, or `python -m rb2dx gui`.

![The Setup page](docs/setup-page.png)

Work through the pages in turn: **Setup** for your folders and tools, **Songs**
to scan and tick what you want, **Build**, then **Results**. Songs that cannot be
converted are set aside with a reason rather than stopping the build.

Burn the ISO to a DVD-R, or run it from a hard drive loader.

## Playing in an emulator

A real PS2 boots the ISO this tool writes; PCSX2 does not. With identical files on
it, an image written by ImgBurn boots where one written here fails, so it is in
how the image is put together rather than anything on the disc.

Tick *Also save the disc's files in a folder* on the Setup page, then make the
image yourself in [ImgBurn](https://www.imgburn.com/): *Build* mode, destination
an image file, that folder as the source, and file system *ISO9660 + UDF* with
UDF revision 1.02. The folder costs almost no space, its files being hard links
to what the build already wrote.

## Choosing songs that fit

The stock Deluxe disc is 7.62 GiB and that is the default limit, because a disc
that size is known to boot on real hardware. A song costs roughly 11 MB a minute
at the default video quality - about 40 MB for three minutes - and two thirds of
that is the video. Setting *Background* to *Black* spends it on songs instead:
the same 33 songs that weigh 2.54 GB behind venue clips come to 0.99 GB, so about
two and a half times as many fit. The note track, crowd and scoring are drawn by
the game and look exactly as they always did.

The usage bar turns red when you are over and says by how much; nothing is
dropped behind your back. Sorting by *On disc* puts the most expensive songs
first, and lowering the video quality is the gentler lever. The ISO stage refuses
to write an image over the limit.

*Leave out the four songs the base game came with*, on by default, buys back
about 264 MB. Three of them never appear in the setlist and cannot play on this
build at all; the fourth is Afterlife.

## Audio

The drum kit and the guitar are carried in stereo, and the kick, snare, bass,
vocal and backing in one channel each, which is the shape the game's own songs
use. A stereo stem going into one of those is averaged into it, and that costs
however much its two sides differ - nothing at all for a centred vocal, up to
3 dB for a wide one. Every song is measured as it is mixed and its other channels
trimmed to match, so the balance you hear is the balance the stems came with.

Tick *Vocals: mix the vocal in stereo* to keep a vocal's width instead. That
costs one channel per song, about a twentieth of its size, and re-mixes the songs
already staged without putting any chart back through the converter.

## The command line

Everything the interface does is available without it:

    python -m rb2dx setup --base-game "D:\RB2DXCE-PS2" --work "D:\rb2dx\work"
    python -m rb2dx setup --add-library "D:\Charts\Rock Band 3"
    python -m rb2dx setup --download
    python -m rb2dx setup --background black
    python -m rb2dx setup --vocals stereo
    python -m rb2dx setup --disc-folder yes
    python -m rb2dx scan
    python -m rb2dx plan
    python -m rb2dx build

Both share one settings file, in `%LOCALAPPDATA%\rb2dxbuilder\settings.json`.

## When something goes wrong

**A tool will not download.** FFmpeg falls back to a GitHub mirror, and a
certificate this machine refuses is tried again against a bundled list, so
pressing the button again is usually all it takes. *Locate* will take a copy you
download yourself.

**A song is left off the disc.** The Results page says which stage failed and
why, in the converter's own words, with the path to its log. Missing album art
and charts Magma rejects are the usual causes. Plenty of what a Clone Hero chart
gets away with is repaired on the way through rather than costing you the song:
track numbers of zero, chords of four or five gems, nested vocal phrases, notes
with no syllable on them, a stray second `[end]` marker. Failures are remembered
so later builds do not stall on the same song; *Try the failed songs again*
clears that.

**The disc will not boot.** Check the ISO is within the size limit and that the
game folder you pointed at boots as it is. In PCSX2 it never will - see above.

**A song crashes as it loads.** Almost always a song list entry promising
something the song cannot deliver, an instrument with no audio channels or
nothing charted for it. Nothing the game ships breaks that rule and nothing built
here should either, so if a song still crashes, please report it.

## How it works

Songs build in parallel, each one through: mixing the stems at 22050 Hz into one
channel group per part the chart plays; converting the chart with Onyx and Magma;
turning the album art into a 256x256 paletted PS2 texture; encoding the mix to
PlayStation 4-bit ADPCM; and encoding the background to MPEG-2 and muxing the two
into a `.pss`. Then the songs and the compiled song list go into the game's
archive, an ISO9660/UDF image is written, and every shipped file is read back out
of the archive and compared.

Work is cached per song, so adding one song to a finished disc builds that one.
Changing a setting only redoes the stages that read it.

## Packaging it yourself

    pip install pyinstaller
    pyinstaller rb2dxbuilder.spec

That writes `dist\RB2DX Disc Builder\`, about 90 MB, ready to zip. It is a folder
rather than a single file so the `venues` folder stays visible: clips dropped in
there are used without changing any setting.

## Thanks

- The **Rock Band 2 Deluxe** team, for the mod this builds on.
- **Onyx Music Game Toolkit** and **dtab**, both by mtolly, for chart conversion
  and for reading and writing the song list.
- **Mackiloha** by PikminGuts92, for the archive and texture tools.
- **FFmpeg**, for everything audio and video.
- **Lysix**, for their amazing tutorial.

## License

MIT, in `LICENSE`. The background clips in `venues\` are not mine and are only
there as a convenience - replace them with your own if you plan to redistribute
this.
