#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Godot .pck reader / embedded-pck splitter.

Subcommands:
  info   <pck|exe>            engine version, pack format, file count
  list   <pck|exe> [regex]    list packed paths
  cat    <pck|exe> <regex>    dump matching packed files to stdout (text)
  gdext  <pck|exe>            report .gdextension / .gdnlib entries and whether
                              they ship a macOS library
  split  <exe> <out.pck>      slice out the pck appended to an exe, rebasing the
                              offsets so the result works as a standalone pck

Where a packed file really sits differs by pack format; all three rules below
were confirmed against exports produced by the official engine builds:
  format 1   (Godot 3.x)          offset is absolute inside the container file
  format 2/3 (Godot 4.0-4.3)      file_base is absolute, offset relative to it
  format 4+  (Godot 4.4+) with
             PACK_REL_FILEBASE    file_base is relative to the pck start
"""
import collections
import re
import struct
import sys

MAGIC = b"GDPC"
PACK_DIR_ENCRYPTED = 1 << 0
PACK_REL_FILEBASE = 1 << 1

Entry = collections.namedtuple(
    "Entry", "path real size flags md5 ofs_pos raw_ofs")


class Pck:
    def __init__(self, data, base=0):
        self.data, self.base = data, base
        if data[base:base + 4] != MAGIC:
            raise ValueError("no GDPC magic at offset %d" % base)
        self.fmt, self.major, self.minor, self.patch = struct.unpack_from(
            "<IIII", data, base + 4)
        self.dir_offset = None
        if self.fmt >= 2:
            self.flags, self.file_base = struct.unpack_from("<IQ", data, base + 20)
            after = base + 32
            if self.fmt >= 4:
                self.dir_offset = struct.unpack_from("<Q", data, base + 32)[0]
                after = base + 40
        else:
            self.flags, self.file_base = 0, 0
            after = base + 20
        self.encrypted = bool(self.flags & PACK_DIR_ENCRYPTED)
        self.rel_filebase = bool(self.flags & PACK_REL_FILEBASE)
        self.entries, self.dir_at, self.entry_has_flags = [], None, None
        if not self.encrypted:
            self._load_dir(after)

    @property
    def version(self):
        v = "%d.%d" % (self.major, self.minor)
        if self.patch:
            v += ".%d" % self.patch
        return v

    def _resolve(self, ofs):
        if self.fmt < 2:
            return ofs
        if self.rel_filebase:
            return self.base + self.file_base + ofs
        return self.file_base + ofs

    def _load_dir(self, after):
        cands = []
        if self.dir_offset is not None:
            cands.append(self.base + self.dir_offset)
        cands += [after + 64, after, after + 60, after + 68, after + 72]
        errs = []
        for dp in cands:
            for has_flags in ((True, False) if self.fmt >= 2 else (False,)):
                try:
                    ents = self._parse_dir(dp, has_flags)
                    self.entries, self.dir_at, self.entry_has_flags = ents, dp, has_flags
                    return
                except Exception as e:              # noqa: BLE001 - probing
                    errs.append("%d/%s: %s" % (dp, has_flags, e))
        raise ValueError("could not locate pack directory (%s)" % "; ".join(errs[:4]))

    def _parse_dir(self, dp, has_flags, check_bounds=True):
        d, n = self.data, len(self.data)
        if dp + 4 > n:
            raise ValueError("dir start past EOF")
        count = struct.unpack_from("<I", d, dp)[0]
        if count == 0 or count > 2_000_000:
            raise ValueError("implausible file_count %d" % count)
        p, out = dp + 4, []
        for _ in range(count):
            if p + 4 > n:
                raise ValueError("truncated")
            ln = struct.unpack_from("<I", d, p)[0]
            p += 4
            if ln == 0 or ln > 4096 or p + ln + 32 > n:
                raise ValueError("bad path_len %d" % ln)
            path = d[p:p + ln].rstrip(b"\0").decode("utf-8", "replace")
            p += ln
            if not path or "\x00" in path:
                raise ValueError("bad path")
            ofs_pos = p
            ofs, size = struct.unpack_from("<QQ", d, p)
            p += 16
            md5 = d[p:p + 16]
            p += 16
            fl = 0
            if has_flags:
                if p + 4 > n:
                    raise ValueError("truncated flags")
                fl = struct.unpack_from("<I", d, p)[0]
                p += 4
                if fl > 0xFF:
                    raise ValueError("implausible file flags 0x%x" % fl)
            real = self._resolve(ofs)
            if check_bounds and (size > n or real < 0 or real + size > n):
                raise ValueError("entry %r out of bounds" % path)
            out.append(Entry(path, real, size, fl, md5, ofs_pos, ofs))
        return out


def load(fn):
    """Open a .pck, or an exe with a pck appended."""
    data = open(fn, "rb").read()
    if data[:4] == MAGIC:
        return Pck(data, 0), 0
    start = embedded_start(data)
    if start is None:
        raise ValueError("%s: not a pck and no embedded pck footer" % fn)
    return Pck(data, start), start


def embedded_start(data):
    """Godot appends [u64 embedded_size][GDPC] after the pck. Returns pck start."""
    if len(data) < 12 or data[-4:] != MAGIC:
        return None
    size = struct.unpack_from("<Q", data, len(data) - 12)[0]
    start = len(data) - 12 - size
    if start < 0 or data[start:start + 4] != MAGIC:
        return None
    return start


def cmd_info(fn):
    pck, start = load(fn)
    print("file=%s" % fn)
    print("embedded=%s pck_start=%d" % (start != 0, start))
    print("pack_format=%d engine_version=%s" % (pck.fmt, pck.version))
    print("flags=0x%x encrypted_dir=%s rel_filebase=%s file_base=%d"
          % (pck.flags, pck.encrypted, pck.rel_filebase, pck.file_base))
    print("dir_at=%s file_count=%d" % (pck.dir_at, len(pck.entries)))


def cmd_list(fn, pattern=None):
    pck, _ = load(fn)
    rx = re.compile(pattern) if pattern else None
    for e in pck.entries:
        if rx and not rx.search(e.path):
            continue
        print("%10d  %s%s" % (e.size, e.path, "  [encrypted]" if e.flags & 1 else ""))


def cmd_cat(fn, pattern):
    pck, _ = load(fn)
    rx, hit = re.compile(pattern), 0
    for e in pck.entries:
        if not rx.search(e.path):
            continue
        hit += 1
        print("===== %s =====" % e.path)
        if e.flags & 1:
            print("(encrypted, cannot read)")
            continue
        sys.stdout.write(pck.data[e.real:e.real + e.size].decode("utf-8", "replace"))
        print()
    return 0 if hit else 3


def _ini_sections(text):
    """Minimal .gdextension / .gdnlib parser: {section: [(key, value)]}."""
    out, cur = {}, ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in ";#":
            continue
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            cur = m.group(1).strip()
            out.setdefault(cur, [])
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out.setdefault(cur, []).append((k.strip(), v.strip().strip('"')))
    return out


def cmd_gdext(fn):
    pck, _ = load(fn)
    paths = set(e.path.replace("res://", "") for e in pck.entries)
    found = bad = 0
    for e in pck.entries:
        if not (e.path.endswith(".gdextension") or e.path.endswith(".gdnlib")):
            continue
        found += 1
        print("EXT %s" % e.path)
        if e.flags & 1:
            print("    encrypted, cannot read -> unknown")
            bad += 1
            continue
        sec = _ini_sections(
            pck.data[e.real:e.real + e.size].decode("utf-8", "replace"))
        # GDExtension (Godot 4) uses [libraries]; GDNative (Godot 3) uses [entry].
        libs = sec.get("libraries", []) or sec.get("entry", [])
        if not libs:
            print("    no [libraries]/[entry] section -> unknown, 当未知处理")
            bad += 1
            continue
        mac = [(k, v) for k, v in libs if re.match(r"^(macos|osx)\b", k, re.I)]
        if not mac:
            print("    该扩展只有这些平台: %s"
                  % ", ".join(sorted(set(k.split(".")[0] for k, _ in libs))))
            print("    -> 缺 mac 版，换壳后这个扩展会加载失败")
            bad += 1
            continue
        missing = []
        for k, v in mac:
            want = v.replace("res://", "")
            inpack = any(q == want or q.startswith(want.rstrip("/") + "/")
                         for q in paths)
            print("    macos 条目: %s = %s   在 pck 内=%s" % (k, v, inpack))
            if not inpack:
                missing.append(v)
        if missing:
            print("    -> 有 macos 条目但库不在包里，需要对应的 mac 库"
                  "（Windows 版一般不带）")
            bad += 1
        else:
            print("    -> macos 库已在包内，可直接用")
    if not found:
        print("包里没有 .gdextension / .gdnlib")
    return 4 if bad else 0


def cmd_split(exe, out):
    data = open(exe, "rb").read()
    start = embedded_start(data)
    if start is None:
        print("no embedded pck in %s" % exe, file=sys.stderr)
        return 3
    size = struct.unpack_from("<Q", data, len(data) - 12)[0]
    src = Pck(data, start)                       # parses fine in place
    blob = bytearray(data[start:start + size])
    fixup = "none"
    if src.fmt >= 2 and not src.rel_filebase and src.file_base >= start:
        # Formats 2/3: one absolute file_base in the header.
        struct.pack_into("<Q", blob, 24, src.file_base - start)
        fixup = "file_base -= %d" % start
    elif src.fmt < 2:
        # Format 1 has no file_base; every entry offset is absolute.
        for e in src.entries:
            struct.pack_into("<Q", blob, e.ofs_pos - start, e.raw_ofs - start)
        fixup = "%d 条目 offset -= %d" % (len(src.entries), start)
    chk = Pck(bytes(blob), 0)
    print("split ok: pck_start=%d size=%d pack_format=%d version=%s files=%d fixup=%s"
          % (start, size, chk.fmt, chk.version, len(chk.entries), fixup))
    open(out, "wb").write(blob)
    return 0


def main(argv):
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "info":
        cmd_info(argv[2])
    elif cmd == "list":
        cmd_list(argv[2], argv[3] if len(argv) > 3 else None)
    elif cmd == "cat":
        return cmd_cat(argv[2], argv[3])
    elif cmd == "gdext":
        return cmd_gdext(argv[2])
    elif cmd == "split":
        return cmd_split(argv[2], argv[3])
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as e:                       # noqa: BLE001 - CLI boundary
        print("error: %s" % e, file=sys.stderr)
        sys.exit(1)
