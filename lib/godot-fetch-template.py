#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Fetch one member out of a remote zip (Godot .tpz export templates) using HTTP range
requests, so we download ~30MB instead of the whole 1.2GB archive.

Usage: godot-fetch-template.py <url> <member-regex> <output-file>
Exits 3 if no member matches.
"""
import io, re, sys, zipfile, urllib.request, urllib.error

UA = {"User-Agent": "curl/8.7.1"}


def resolve(url):
    """Follow redirects once, return the final (signed) URL and total size."""
    req = urllib.request.Request(url, headers=dict(UA, Range="bytes=0-0"))
    with urllib.request.urlopen(req, timeout=60) as r:
        final = r.geturl()
        cr = r.headers.get("Content-Range", "")
        total = int(cr.split("/")[-1]) if "/" in cr else int(r.headers["Content-Length"])
    return final, total


class RangeFile(io.RawIOBase):
    def __init__(self, url, total):
        self.url, self.total, self.pos = url, total, 0
        self.nreq, self.nbytes = 0, 0

    def seekable(self): return True
    def readable(self): return True

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.total + off)
        return self.pos

    def tell(self): return self.pos

    def readinto(self, b):
        n = len(b)
        if n == 0 or self.pos >= self.total:
            return 0
        end = min(self.pos + n, self.total) - 1
        req = urllib.request.Request(
            self.url, headers=dict(UA, Range=f"bytes={self.pos}-{end}"))
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                break
            except (urllib.error.URLError, OSError):
                if attempt == 3:
                    raise
        b[:len(data)] = data
        self.pos += len(data)
        self.nreq += 1
        self.nbytes += len(data)
        return len(data)


def main():
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    url, pattern, out = sys.argv[1], sys.argv[2], sys.argv[3]
    final, total = resolve(url)
    rf = RangeFile(final, total)
    # Buffer so zipfile's many small reads coalesce into few range requests.
    bf = io.BufferedReader(rf, buffer_size=1 << 20)
    zf = zipfile.ZipFile(bf)
    rx = re.compile(pattern)
    hit = next((n for n in zf.namelist() if rx.search(n)), None)
    if hit is None:
        print("no member matching %r; archive has %d entries, e.g. %s"
              % (pattern, len(zf.namelist()), zf.namelist()[:5]), file=sys.stderr)
        return 3
    info = zf.getinfo(hit)
    with zf.open(hit) as src, open(out, "wb") as dst:
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            dst.write(chunk)
    print("member=%s compressed=%d uncompressed=%d http_requests=%d downloaded=%d total_archive=%d"
          % (hit, info.compress_size, info.file_size, rf.nreq, rf.nbytes, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
