// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// ocr：读一张截图，把认出的文字按行输出成 JSON。测试框架用合成按键/点击驱动游戏后，
// 需要知道屏幕上写了什么（菜单项如 "Save"、"Load"、"Slot 1"、"存档"、"读档"、"開始"），
// 不能每张截图都靠人看，这个工具就是那双眼睛（2026-09-29 新增）。
//
// 编译（产物放 SN_RUNTIMES，不进仓库）：
//   swiftc -O tools/ocr.swift -o "$SN_RUNTIMES/bin/ocr"
// 用法：
//   ocr <image.png> [--lang zh-Hans,en,ja] [--fast] [--min-confidence 0.3]
//     --lang            逗号分隔的识别语言，默认 zh-Hans,zh-Hant,en,ja
//     --fast            用 fast 识别档（默认 accurate：慢一点，但中文小字更稳）
//     --min-confidence  置信度下限（0 到 1），低于它的行直接丢掉，默认 0.3；
//                       低对比度的游戏字体可以把门槛调低到 0.2 试试
// 输出（stdout 一行 JSON）：
//   {"image":"<path>","width":<px>,"height":<px>,
//    "lines":[{"text":"...","confidence":0.93,"x":<px>,"y":<px>,"w":<px>,"h":<px>},...]}
//   x/y/w/h 是图像像素，原点在左上角；Vision 的框是左下原点的归一化坐标，这里换算过。
//   行按从上到下、再从左到右排序；每条观察只保留置信度最高的第一候选。
// 退出码：0 成功（一张图里没认出字也是 0，lines 为空）；2 参数错；3 图像读不了；4 Vision 请求失败。
// 错误信息一律走 stderr（中文）。
// 只读传入的图像文件本身：不联网、不访问屏幕（截图由调用方用 screencapture 完成）。

import CoreGraphics
import Foundation
import ImageIO
import Vision

func die(_ code: Int32, _ message: String) -> Never {
    FileHandle.standardError.write("错误: \(message)\n".data(using: .utf8)!)
    exit(code)
}

let args = CommandLine.arguments
var path: String?
var langs = ["zh-Hans", "zh-Hant", "en", "ja"]
var fast = false
var minConfidence = 0.3

var i = 1
while i < args.count {
    let arg = args[i]
    switch arg {
    case "--lang":
        guard i + 1 < args.count else { die(2, "参数 \(arg) 缺少值") }
        i += 1
        langs = args[i].split(separator: ",").map { String($0) }.filter { !$0.isEmpty }
        guard !langs.isEmpty else { die(2, "--lang 的语言列表为空") }
    case "--fast":
        fast = true
    case "--min-confidence":
        guard i + 1 < args.count, let v = Double(args[i + 1]), v >= 0, v <= 1 else {
            die(2, "--min-confidence 需要一个 0 到 1 之间的数")
        }
        i += 1
        minConfidence = v
    default:
        if arg.hasPrefix("-") { die(2, "无法识别的参数 \(arg)") }
        if path != nil { die(2, "多余的参数 \(arg)") }
        path = arg
    }
    i += 1
}
guard let path else { die(2, "用法: ocr <image.png> [--lang zh-Hans,en,ja] [--fast] [--min-confidence 0.3]") }

let url = URL(fileURLWithPath: path)
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    die(3, "无法读取图像 \(path)")
}
let w = image.width, h = image.height

let request = VNRecognizeTextRequest()
request.recognitionLevel = fast ? .fast : .accurate
request.recognitionLanguages = langs
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(url: url, options: [:])
do {
    try handler.perform([request])
} catch {
    die(4, "Vision 识别失败: \(error)")
}

struct Line {
    let text: String, confidence: Double, x: Int, y: Int, w: Int, h: Int
}

// boundingBox 是归一化坐标、原点在左下；换算成左上原点的像素坐标
var lines: [Line] = []
for obs in request.results ?? [] {  // 每条观察下面只取 topCandidates(1)，即最可能的那一个
    guard let candidate = obs.topCandidates(1).first else { continue }
    let confidence = Double(candidate.confidence)
    if confidence < minConfidence { continue }
    let box = obs.boundingBox
    let px = box.origin.x * CGFloat(w)
    let pw = box.width * CGFloat(w)
    let ph = box.height * CGFloat(h)
    let py = (1 - box.origin.y - box.height) * CGFloat(h)
    lines.append(Line(text: candidate.string, confidence: confidence,
                      x: Int(px.rounded()), y: Int(py.rounded()),
                      w: Int(pw.rounded()), h: Int(ph.rounded())))
}
lines.sort { $0.y != $1.y ? $0.y < $1.y : $0.x < $1.x }

let out: [String: Any] = [
    "image": path,
    "width": w,
    "height": h,
    "lines": lines.map { line -> [String: Any] in
        ["text": line.text, "confidence": line.confidence,
         "x": line.x, "y": line.y, "w": line.w, "h": line.h]
    },
]
print(String(data: try JSONSerialization.data(withJSONObject: out), encoding: .utf8)!)
