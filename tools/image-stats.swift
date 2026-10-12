// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// 截图内容统计：主色占比、量化后的颜色数、平均亮度。判「窗口在但画面是纯色」用（2026-09-14 一款 Wine 游戏 白窗被记成 alive 之后加的）。
//
// 编译：swiftc -O tools/image-stats.swift -o "$SN_RUNTIMES/bin/image-stats"
// 用法：image-stats <png>  → {"width","height","dominant_fraction","colors","mean_luma","samples"}
// 采样步长按尺寸自适应，几万个像素足够判纯色；不落盘、不联网。
import CoreGraphics
import Foundation
import ImageIO

let args = CommandLine.arguments
guard args.count >= 2, let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: args[1]) as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    FileHandle.standardError.write("用法: image-stats <png>\n".data(using: .utf8)!); exit(2)
}
let w = image.width, h = image.height
let space = CGColorSpaceCreateDeviceRGB()
var pixels = [UInt8](repeating: 0, count: w * h * 4)
guard let ctx = CGContext(data: &pixels, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: space,
                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(3) }
ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
let step = max(1, Int((Double(w * h) / 60000.0).squareRoot()))
var counts: [UInt32: Int] = [:]
var luma = 0.0, n = 0
var y = 0
while y < h {
    var x = 0
    while x < w {
        let i = (y * w + x) * 4
        let r = pixels[i], g = pixels[i + 1], b = pixels[i + 2]
        let key = (UInt32(r >> 3) << 10) | (UInt32(g >> 3) << 5) | UInt32(b >> 3)   // 5 bit 量化
        counts[key, default: 0] += 1
        luma += 0.299 * Double(r) + 0.587 * Double(g) + 0.114 * Double(b)
        n += 1
        x += step
    }
    y += step
}
let top = counts.values.max() ?? 0
// 细节占比：中央 80% 区域切成 24 px 的格子，格子内亮度极差 > 24 算「有细节」。纯色窗口只有边框/标题栏那一圈有细节，
// 极简画风的游戏（一款像素游戏 三色、一款像素游戏 灰白）也有大量格子带细节，颜色数判不出来的它能判。
let tile = 24
let x0 = tile, y0 = tile, x1 = w - tile, y1 = h - tile   // 只去掉最外一圈（窗口边框/标题栏），黑底白字在底部的提示也要算进来
var tiles = 0, detailed = 0
var ty = y0
while ty + tile <= y1 {
    var tx = x0
    while tx + tile <= x1 {
        var lo = 255.0, hi = 0.0
        var yy = ty
        while yy < ty + tile {
            var xx = tx
            while xx < tx + tile {
                let i = (yy * w + xx) * 4
                let l = 0.299 * Double(pixels[i]) + 0.587 * Double(pixels[i + 1]) + 0.114 * Double(pixels[i + 2])
                if l < lo { lo = l }
                if l > hi { hi = l }
                xx += 3
            }
            yy += 3
        }
        tiles += 1
        if hi - lo > 24 { detailed += 1 }
        tx += tile
    }
    ty += tile
}
let out: [String: Any] = ["width": w, "height": h, "samples": n, "colors": counts.count,
                          "dominant_fraction": n > 0 ? Double(top) / Double(n) : 1.0,
                          "mean_luma": n > 0 ? luma / Double(n) : 0,
                          "detail_fraction": tiles > 0 ? Double(detailed) / Double(tiles) : 0, "tiles": tiles]
print(String(data: try JSONSerialization.data(withJSONObject: out), encoding: .utf8)!)
