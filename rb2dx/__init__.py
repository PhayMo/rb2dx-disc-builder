"""Build a custom Rock Band 2 Deluxe disc for the PlayStation 2.

The pipeline turns folders of Clone Hero songs into a bootable PS2 ISO:

  library   find songs and read what they contain
  plan      choose which ones fit the disc
  audio     mix the stems into the channel layout the game expects
  vgs       encode that mix as PlayStation ADPCM
  video     encode a background video and mux it with the audio into a .pss
  charts    convert each chart to Rock Band 2 form via Onyx and Magma
  art       convert album art to the PS2's paletted texture format
  dta       write and compile the song list the game reads
  ark       inject everything into the game's archive and repack it
  iso       write the bootable disc image
  verify    read every shipped file back out of the archive and compare

Windows only: Onyx runs Magma, the official Rock Band compiler, internally.
"""

__version__ = "0.1.0"
