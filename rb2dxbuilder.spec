# PyInstaller build: pip install pyinstaller, then
#     pyinstaller rb2dxbuilder.spec
# Produces dist\RB2DX Disc Builder\, which can be zipped and shared. A folder
# rather than a single file, so the venues folder stays visible and users can drop
# their own background clips in beside the program.

a = Analysis(
    ["launch.py"],
    pathex=[],
    binaries=[],
    datas=[("venues", "venues"), ("README.md", ".")],
    hiddenimports=["pycdlib", "numpy", "certifi"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The external tools are downloaded at runtime, so nothing else to bundle.
    excludes=["matplotlib", "scipy", "pandas", "pytest", "setuptools",
              "PIL", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RB2DX Disc Builder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    # A startup failure should say so rather than vanish silently.
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="RB2DX Disc Builder",
)

# The videos also go beside the program, because that is where someone will look
# to add their own clips. The copy tucked away inside _internal is the fallback.
import os
import shutil

out = os.path.join(DISTPATH, "RB2DX Disc Builder")
beside = os.path.join(out, "venues")
shutil.rmtree(beside, ignore_errors=True)
shutil.copytree("venues", beside)
shutil.copy2("README.md", os.path.join(out, "README.md"))
shutil.copytree("docs", os.path.join(out, "docs"), dirs_exist_ok=True)
