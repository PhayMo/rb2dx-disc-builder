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
- **Your own copy of Rock Band 2 Deluxe (Custom Edition) for PS2**, unpacked - the folder holding
  `SLUS_218.00` and `gen\MAIN_0.ARK`.
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
or `background` file plays that, in any format Clone Hero accepts, and can be
moved a little either way to suit; `song.ini`'s `video_start_time` is honoured
where you have not. Or set *Background* to *Black* and have none at all, which
fits far more songs.

Black a video came with is cut off before it is framed, so a picture boxed inside
a file of another shape fills the screen rather than sitting in the middle of it.
Where the picture itself is a different shape from the screen there is no answer
that costs nothing, so *A song's own video* decides: keep all of it, with black
above and below, or fill the screen and crop what will not fit.

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

## The command line

Everything the interface does is available without it:

    python -m rb2dx setup --base-game "D:\RB2DXCE-PS2" --work "D:\rb2dx\work"
    python -m rb2dx setup --add-library "D:\Charts\Rock Band 3"
    python -m rb2dx setup --download
    python -m rb2dx setup --background black
    python -m rb2dx setup --screen 16:9
    python -m rb2dx setup --song-video fill
    python -m rb2dx setup --wide-mix yes
    python -m rb2dx setup --disc-folder yes
    python -m rb2dx scan
    python -m rb2dx plan
    python -m rb2dx nudge "D:\Charts\Some Song" 4.5
    python -m rb2dx nudge "D:\Charts\Some Song" --detect
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
with no syllable on them, a stray second `[end]` marker, a solo or a sung phrase
running into the big rock ending, an ending whose lanes do not all finish
together. Failures are remembered
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
