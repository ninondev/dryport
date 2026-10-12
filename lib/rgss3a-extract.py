#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""
解包 RGSS3A 归档。
用途: macOS 上 mkxp-z 查询路径与归档内 NFC 文件名的规范化不一致,
含浊音假名的日文资源会找不到(实测 ダンジョンA1 / ばけ / ベル 等全部 ENOENT)。
解包成散文件后, 文件名由 APFS 按它自己的规范化存储, 查询即可命中。
用法: rgss3a-extract.py <Game.rgss3a> <输出目录>
"""
import sys, struct, os

def extract(arc, outdir):
    with open(arc, 'rb') as f:
        magic = f.read(8)
        if magic[:7] != b'RGSSAD\x00' or magic[7] != 3:
            raise SystemExit(f"不是 RGSS3A 归档: {magic!r}")
        seed = struct.unpack('<I', f.read(4))[0]
        key = (seed * 9 + 3) & 0xFFFFFFFF
        kb = struct.pack('<I', key)
        table = []
        while True:
            hdr = f.read(16)
            if len(hdr) < 16: break
            off, size, fkey, nlen = [x ^ key for x in struct.unpack('<4I', hdr)]
            if off == 0: break
            raw = f.read(nlen)
            name = bytes(b ^ kb[i & 3] for i, b in enumerate(raw)).decode('utf-8')
            table.append((name.replace('\\', '/'), off, size, fkey))

        n_ok = n_skip = 0
        for name, off, size, fkey in table:
            dst = os.path.join(outdir, name)
            if os.path.exists(dst) and os.path.getsize(dst) == size:
                n_skip += 1; continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            f.seek(off)
            data = bytearray(f.read(size))
            k = fkey
            for i in range(0, size, 4):
                kb4 = struct.pack('<I', k)
                for j in range(min(4, size - i)):
                    data[i + j] ^= kb4[j]
                k = (k * 7 + 3) & 0xFFFFFFFF
            with open(dst, 'wb') as o:
                o.write(data)
            n_ok += 1
        return len(table), n_ok, n_skip

if __name__ == '__main__':
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    total, ok, skip = extract(sys.argv[1], sys.argv[2])
    print(f"归档条目 {total}, 解出 {ok}, 已存在跳过 {skip}")
