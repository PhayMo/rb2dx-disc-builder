"""Just enough of this console's instruction set to write a few instructions into the game.

Almost everything a disc needs is data, and data is what the rest of this builder edits. Now
and then a thing worth doing needs the game to ask a question it does not ask, and no amount of
editing scripts or tables can add one, because the asking lives in the executable. So this
assembles a handful of instructions and writes them over instructions already there.

Two things about this processor shape everything here. Its instructions are all four bytes, so
a patch has exactly as much room as the code it replaces and not a byte more. And a branch
takes effect one instruction late: the instruction after a branch runs whether the branch is
taken or not, which is why patches end up with something useful sitting in what looks like the
wrong place. The likely branches - the ones whose names end in l - are the exception, and skip
that instruction when they are not taken, which is worth a word here and there.

Nothing is written to the game folder. A patched executable is a copy, made in the work folder,
and the copy is what the disc is built from.
"""

import os
import re
import shutil
import struct

from .errors import BuildError

# The registers, in the order the processor numbers them.
REGISTERS = ("zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
             "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
             "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
             "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra")

# What each instruction this can write is made of. The three-register ones are told apart by
# the code in their last six bits, and the rest by the code in their first six.
THREE = {"addu": 0x21, "subu": 0x23, "and": 0x24, "or": 0x25, "sltu": 0x2B, "daddu": 0x2D}
SHIFTS = {"sll": 0x00, "srl": 0x02, "sra": 0x03}
VALUES = {"addiu": 0x09, "slti": 0x0A, "sltiu": 0x0B, "andi": 0x0C, "ori": 0x0D, "xori": 0x0E}
MEMORY = {"lb": 0x20, "lh": 0x21, "lw": 0x23, "lbu": 0x24, "lhu": 0x25, "sb": 0x28,
          "sh": 0x29, "sw": 0x2B}
# Branches, and whether they compare against a register or against nothing.
BRANCHES = {"beq": (0x04, False), "bne": (0x05, False),
            "beql": (0x14, False), "bnel": (0x15, False),
            "beqz": (0x04, True), "bnez": (0x05, True),
            "beqzl": (0x14, True), "bnezl": (0x15, True)}


def halves(address):
    """The two halves an address is written as, since none of them fit in one instruction.

    The upper half goes in with lui and the lower is added to it, and because the adding treats
    its half as a signed number the upper one has to be carried when the lower would come out
    negative.
    """
    low = address & 0xFFFF
    if low & 0x8000:
        return (address >> 16) + 1, low - 0x10000
    return address >> 16, low


def _register(text):
    name = text.strip().lstrip("$")
    if name not in REGISTERS:
        raise BuildError("%r is not a register this knows." % text)
    return REGISTERS.index(name)


def _value(text, labels=None):
    text = text.strip()
    if labels and text in labels:
        return labels[text]
    try:
        return int(text, 0)
    except ValueError:
        raise BuildError("%r is not a number this knows." % text)


def _at(text):
    """A place in memory, written the way the assembler writes it: 0x10(sp)."""
    match = re.match(r"^\s*(-?[\w]+)?\s*\(\s*\$?(\w+)\s*\)\s*$", text)
    if not match:
        raise BuildError("%r is not somewhere in memory." % text)
    return _value(match.group(1) or "0"), _register(match.group(2))


def _one(line, at, labels):
    """One instruction, as the four bytes the processor reads it as."""
    name, _, rest = line.strip().partition(" ")
    parts = [p.strip() for p in rest.split(",")] if rest.strip() else []

    if name == "nop":
        return 0
    if name == "b":
        name, parts = "beq", ["zero", "zero", parts[0]]
    if name == "j" or name == "jal":
        target = _value(parts[0], labels)
        return ((0x02 if name == "j" else 0x03) << 26) | ((target >> 2) & 0x3FFFFFF)
    if name == "lui":
        return (0x0F << 26) | (_register(parts[0]) << 16) | (_value(parts[1]) & 0xFFFF)
    if name in THREE:
        rd, rs, rt = (_register(p) for p in parts)
        return (rs << 21) | (rt << 16) | (rd << 11) | THREE[name]
    if name in SHIFTS:
        rd, rt = _register(parts[0]), _register(parts[1])
        return (rt << 16) | (rd << 11) | ((_value(parts[2]) & 31) << 6) | SHIFTS[name]
    if name == "srav":
        rd, rt, rs = (_register(p) for p in parts)
        return (rs << 21) | (rt << 16) | (rd << 11) | 0x07
    if name in VALUES:
        rt, rs = _register(parts[0]), _register(parts[1])
        return (VALUES[name] << 26) | (rs << 21) | (rt << 16) | (_value(parts[2]) & 0xFFFF)
    if name in MEMORY:
        rt = _register(parts[0])
        offset, rs = _at(parts[1])
        return (MEMORY[name] << 26) | (rs << 21) | (rt << 16) | (offset & 0xFFFF)
    if name in BRANCHES:
        code, alone = BRANCHES[name]
        rs = _register(parts[0])
        rt = 0 if alone else _register(parts[1])
        target = _value(parts[-1], labels)
        step = (target - (at + 4)) >> 2
        if not -0x8000 <= step < 0x8000:
            raise BuildError("%#x is too far from %#x to branch to." % (target, at))
        return (code << 26) | (rs << 21) | (rt << 16) | (step & 0xFFFF)
    raise BuildError("%r is not an instruction this knows how to write." % name)


def assemble(source, at):
    """The bytes for a run of instructions meant to sit at a given address.

    Labels are written as a name and a colon, either on a line of their own or in front of an
    instruction, and the run is read twice: once to find where each label lands, and again to
    write the instructions now that the labels are known.
    """
    lines = []
    for line in source.splitlines():
        line = line.split(";")[0].strip()
        while line:
            match = re.match(r"^(\w+):\s*(.*)$", line)
            if not match or match.group(1) in ("nop", "b"):
                break
            lines.append((match.group(1), ""))
            line = match.group(2)
        if line:
            lines.append((None, line))

    labels = {}
    step = at
    for label, text in lines:
        if label:
            labels[label] = step
        if text:
            step += 4

    out = bytearray()
    step = at
    for label, text in lines:
        if text:
            out += struct.pack("<I", _one(text, step, labels))
            step += 4
    return bytes(out)


class Executable(object):
    """The game's executable, and where each of its addresses lives in the file."""

    def __init__(self, path):
        self.path = path
        self.raw = bytearray(open(path, "rb").read())
        if self.raw[:4] != b"\x7fELF":
            raise BuildError("%s is not a PlayStation 2 executable." % path)
        start, = struct.unpack_from("<I", self.raw, 28)
        size, count = struct.unpack_from("<2H", self.raw, 42)
        self.loaded = []
        for i in range(count):
            kind, off, addr, _paddr, in_file, _in_memory, _flags, _align = struct.unpack_from(
                "<8I", self.raw, start + i * size)
            if kind == 1:
                self.loaded.append((off, addr, in_file))

    def _where(self, addr, size):
        for off, base, in_file in self.loaded:
            if base <= addr and addr + size <= base + in_file:
                return off + addr - base
        raise BuildError("%#x is not somewhere the console loads." % addr)

    def read(self, addr, size):
        at = self._where(addr, size)
        return bytes(self.raw[at:at + size])

    def write(self, addr, data):
        at = self._where(addr, len(data))
        self.raw[at:at + len(data)] = data

    def save(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fp:
            fp.write(self.raw)
        return path


def copy(source, into):
    """The executable, copied where it can be written to, keeping the name it must keep."""
    os.makedirs(into, exist_ok=True)
    out = os.path.join(into, os.path.basename(source))
    shutil.copyfile(source, out)
    return out
