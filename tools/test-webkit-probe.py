#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""显式运行自有 WebKit 机制样例；不接受游戏目录，不下载运行时。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import struct
import subprocess
import sys
import tempfile
import uuid

OBSERVER_SETUP = r'''
const documentEvents = [];
const globalEvents = [];
let timestamp = 0;
let writes = 0;
let gameCallbacks = 0;
globalThis.window = globalThis;
globalThis.performance = {now: () => ++timestamp};
globalThis.requestAnimationFrame = () => {};
globalThis.addEventListener = (type, handler, options) => globalEvents.push({type, handler, options});
globalThis.document = {readyState: 'complete', title: 'self fixture',
  addEventListener: (type, handler, options) => documentEvents.push({type, handler, options})};
const originalDocumentListener = document.addEventListener;
globalThis.location = {href: 'sn-resource://self-fixture/index.html'};
globalThis.innerWidth = 800; globalThis.innerHeight = 600; globalThis.devicePixelRatio = 2;
const canary = 'PRIVATE-SAVE-CANARY-MUST-NOT-APPEAR';
const stored = Object.fromEntries([['session', canary], ['session2', ''], ['language', 'zh'], ['__proto__', 'safe']]);
const keys = Object.keys(stored);
const storage = {get length() { return keys.length; }, key: index => keys[index] ?? null,
  getItem: key => Object.hasOwn(stored, key) ? stored[key] : null,
  setItem: () => { writes++; throw Error('write forbidden'); },
  removeItem: () => { writes++; throw Error('remove forbidden'); },
  clear: () => { writes++; throw Error('clear forbidden'); }};
Object.defineProperty(globalThis, 'localStorage', {configurable: true, get: () => storage});
function check(condition, message) { if (!condition) throw Error(message); }
'''

OBSERVER_CHECKS = r'''
const types = ['mousedown', 'mousemove', 'mouseup', 'mouseleave', 'pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'pointerleave'];
check(documentEvents.map(item => item.type).join(',') === types.join(','), 'input listener coverage');
check(documentEvents.every(item => item.options.capture === true && item.options.passive === true), 'listeners must be passive capture');
check(document.addEventListener === originalDocumentListener, 'game event registration was wrapped');
check(!window.webkit?.messageHandlers, 'message bridge introduced');
let sequence = 0;
function deliver(registration) {
  const index = sequence++;
  const event = Object.freeze({type: registration.type, clientX: index, clientY: index + 0.5,
    buttons: 1, button: 0, target: Object.freeze({id: 'renderer', tagName: 'CANVAS'}), isTrusted: index % 2 === 0,
    preventDefault: () => { throw Error('preventDefault forbidden'); },
    stopPropagation: () => { throw Error('stopPropagation forbidden'); },
    stopImmediatePropagation: () => { throw Error('stopImmediatePropagation forbidden'); }});
  registration.handler(event);
  gameCallbacks++;
}
documentEvents.forEach(deliver);
const mouseMove = documentEvents.find(item => item.type === 'mousemove');
for (let index = 0; index < 80; index++) deliver(mouseMove);
const input = window.__steamNativeWebKitObservation.input;
check(input.counts.mousemove === 81 && types.filter(type => type !== 'mousemove').every(type => input.counts[type] === 1), 'input counts');
check(gameCallbacks === 89 && input.last.length === 32, 'bounded event history or game delivery');
check(input.last[0].clientX === 57 && input.last[31].clientX === 88 && input.last[31].clientY === 88.5, 'history order and coordinates');
check(input.last.every(item => item.type === 'mousemove' && item.buttons === 1 && item.button === 0 && item.target.id === 'renderer' && item.target.tag === 'CANVAS'), 'recorded event metadata');
check(input.last.some(item => item.isTrusted) && input.last.some(item => !item.isTrusted), 'trusted flag lost');
'''

OBSERVER_MAIN = r'''
@main struct ObserverMain {
  @MainActor static func main() throws {
    let base = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
    let directory = base.appendingPathComponent("option-fixture", isDirectory: true)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
    let entry = directory.appendingPathComponent("index.html")
    try Data("self fixture".utf8).write(to: entry)
    let args = ["--entry", entry.path, "--output", base.appendingPathComponent("unused-output").path,
      "--storeUUID", "01234567-89AB-CDEF-8123-456789ABCDEF"]
    guard try Options(args).timeout == 10, try Options(args).deadlineSeconds == 15 else { throw ProbeFailure("默认超时变化") }
    guard try Options(args).lifecycle == "none",
          try Options(args + ["--mode", "scheme", "--lifecycle", "terminal-beforeunload"]).lifecycle == "terminal-beforeunload"
      else { throw ProbeFailure("生命周期默认或 scheme 试验选项不符") }
    for extra in [["--mode", "file", "--lifecycle", "terminal-beforeunload"],
                  ["--mode", "scheme", "--lifecycle", "unknown"],
                  ["--lifecycle", "none", "--lifecycle", "none"]] {
      var rejected = false
      do { _ = try Options(args + extra) } catch { rejected = true }
      guard rejected else { throw ProbeFailure("无效生命周期选项未拒绝") }
    }
    for seconds in [2, 240, 300] {
      let options = try Options(args + ["--timeout", String(seconds)])
      guard options.timeout == Double(seconds), options.deadlineSeconds == Double(seconds + 5) else { throw ProbeFailure("超时或截止不符") }
    }
    for seconds in ["1", "300.01", "301", "nan", "inf", "-inf"] {
      var rejected = false
      do { _ = try Options(args + ["--timeout", seconds]) } catch { rejected = true }
      guard rejected else { throw ProbeFailure("无效超时未拒绝：\(seconds)") }
    }
    guard let context = JSContext() else { throw ProbeFailure("无法创建无界面 JavaScriptCore") }
    let script = try String(contentsOf: base.appendingPathComponent("observer-check.js"), encoding: .utf8)
    guard let result = context.evaluateScript(script), context.exception == nil, let text = result.toString() else {
      throw ProbeFailure("观察脚本回归失败：\(context.exception?.toString() ?? "无结果")")
    }
    print(text)
  }
}
'''


def observer_check(base):
    """执行源码中的实际观察脚本与 Options；只有自造事件和存储，不启动 WebKit。"""
    source_path = Path(__file__).with_name("probe-webkit.swift")
    source = source_path.read_text()
    observer = source.split('let observationScript = #"""', 1)[1].split('"""#', 1)[0]
    pull = source.split('let pullObservation = #"""', 1)[1].split('"""#', 1)[0]
    checkpoint = source.split('let pullCheckpoint = #"""', 1)[1].split('"""#', 1)[0]
    javascript = OBSERVER_SETUP + observer + OBSERVER_CHECKS + '\nconst first = ' + pull + ';\n' + r'''
const report = JSON.parse(first);
check(report.localStorage.ok && report.localStorage.keyLengths.session === canary.length &&
  report.localStorage.keyLengths.session2 === 0 && report.localStorage.keyLengths.language === 2 &&
  report.localStorage.keyLengths['__proto__'] === 4, 'storage lengths');
check(!first.includes(canary) && writes === 0, 'stored content leaked or storage written');
''' + '\nconst checkpoint = ' + checkpoint + ';\n' + r'''
const checkpointReport = JSON.parse(checkpoint);
check(checkpointReport.input.last.length === 32 && checkpointReport.input.counts.mousemove === 81, 'checkpoint lost input');
check(checkpointReport.error_count === 0 && checkpointReport.rejection_count === 0, 'checkpoint error counts');
check(JSON.stringify(checkpointReport.localStorage) === JSON.stringify(report.localStorage), 'checkpoint storage lengths');
check(!checkpoint.includes(canary) && !('observation' in checkpointReport) && !('raf' in checkpointReport) && writes === 0, 'checkpoint leaked contents or copied RAF');
Object.defineProperty(globalThis, 'localStorage', {configurable: true, get: () => { throw Error(canary); }});
''' + '\nconst denied = ' + pull + ';\n' + r'''
check(JSON.parse(denied).localStorage.ok === false && !denied.includes(canary) && writes === 0, 'storage failure leaked content');
''' + '\nconst checkpointDenied = ' + checkpoint + ';\n' + r'''
check(JSON.parse(checkpointDenied).localStorage.ok === false && !checkpointDenied.includes(canary) && writes === 0, 'checkpoint failure leaked content');
JSON.stringify({passive_capture:true, bounded_history:32, input_events_observed:gameCallbacks,
  storage_lengths_only:true, storage_writes:writes, storage_failure_reported:true,
  checkpoint_lengths_only:true, checkpoint_without_raf:true,
  no_message_bridge:true, timeout_default:10, timeout_maximum:300, deadline_extra_seconds:5,
  lifecycle_option_boundaries:true});
'''
    (base / "observer-check.js").write_text(javascript)
    core_path = Path(__file__).resolve().parents[1] / "lib/webkit-core.swift"
    options = source[source.index("struct Options {"):source.index("let observationScript =")]
    harness = base / "observer.swift"
    harness.write_text("import AppKit\nimport Foundation\nimport JavaScriptCore\n" + options + OBSERVER_MAIN)
    binary = base / "observer-probe"
    compiled = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "6", "-strict-concurrency=complete",
        "-default-isolation", "MainActor", "-enable-upcoming-feature", "NonisolatedNonsendingByDefault", "-O",
        "-target", "arm64-apple-macosx14.0", str(core_path), str(harness), "-o", str(binary)], capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stderr
    run = subprocess.run([str(binary), str(base)], capture_output=True, text=True, timeout=10)
    result = {"kind": "exact observer/pull/Options source; JavaScriptCore self fixture; no GUI or game",
              "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
              "exit": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
    (base / "observer-evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert run.returncode == 0, result
    result["checks"] = json.loads(run.stdout)
    return result

TIMING_HARNESS = r'''
@main struct TimingMain {
  @MainActor static func main() async throws {
    let schedule = ProbeSchedule()
    guard schedule.checkpointOffsets(before: 10).isEmpty,
          schedule.checkpointOffsets(before: 30).isEmpty,
          schedule.checkpointOffsets(before: 60) == [30],
          schedule.checkpointOffsets(before: 240) == [30,60,90,120,150,180,210],
          schedule.checkpointOffsets(before: 300) == [30,60,90,120,150,180,210,240,270]
      else { throw ProbeFailure("周期采样必须严格早于最终采集") }
    // 人为延后观察任务的开始；用于证明相对 sleep 的协议缺陷，不冒充 run4 的根因重现。
    let oldObserver = Task {
      try await schedule.wait(until: 0.7)
      let began = schedule.elapsed
      try await Task.sleep(for: .seconds(0.5), tolerance: .zero)
      return ["started": began, "woke": schedule.elapsed]
    }
    let oldDeadline = Task {
      try await Task.sleep(for: .seconds(0.9), tolerance: .zero)
      return schedule.elapsed
    }
    let newObserver = Task {
      try await schedule.wait(until: 0.7)
      let began = schedule.elapsed
      try await schedule.wait(until: 0.5)
      return ["started": began, "woke": schedule.elapsed]
    }
    let newDeadline = Task {
      try await schedule.wait(until: 0.9)
      return schedule.elapsed
    }
    let old = try await oldObserver.value
    let oldEnd = try await oldDeadline.value
    let fixed = try await newObserver.value
    let fixedEnd = try await newDeadline.value
    guard old["woke"]! > oldEnd, fixed["woke"]! < fixedEnd,
          fixed["woke"]! - fixed["started"]! < 0.15,
          fixedEnd >= 0.9 else { throw ProbeFailure("共用绝对时基未消除任务迟启动造成的时长重置") }
    let cancellation = Task { try await schedule.wait(until: 30) }
    cancellation.cancel()
    var cancelled = false
    do { try await cancellation.value } catch is CancellationError { cancelled = true }
    guard cancelled else { throw ProbeFailure("取消未中断等待") }
    let report: [String: Any] = ["relative_late_start": old, "relative_deadline_woke": oldEnd,
      "absolute_late_start": fixed, "absolute_deadline_woke": fixedEnd,
      "checkpoint_boundaries": true, "cancellation": cancelled,
      "constructed_late_task_start_seconds": 0.7, "observation_seconds": 0.5,
      "deadline_seconds": 0.9, "tolerance_seconds": 0]
    print(String(decoding: try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]), as: UTF8.self))
  }
}
'''


def timing_check(base):
    """提取真实绝对计时助手，构造迟启动反例；只运行无窗口短任务。"""
    source_path = Path(__file__).with_name("probe-webkit.swift")
    source = source_path.read_text()
    helper = source[source.index("struct ProbeSchedule {"):source.index("let observationScript =")]
    core = (Path(__file__).resolve().parents[1] / "lib/webkit-core.swift").read_text()
    failure = core[core.index("struct ProbeFailure:"):core.index("// 仅用于已核实")]
    harness = base / "timing.swift"
    harness.write_text("import Foundation\n" + failure + helper + TIMING_HARNESS)
    binary = base / "timing-probe"
    compiled = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "6", "-strict-concurrency=complete",
        "-default-isolation", "MainActor", "-enable-upcoming-feature", "NonisolatedNonsendingByDefault", "-O",
        "-target", "arm64-apple-macosx14.0", str(harness), "-o", str(binary)], capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    result = {"kind": "exact ProbeSchedule; constructed late task start, not a reproduction of run4's actual scheduling",
              "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
              "exit": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
    (base / "timing-evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert run.returncode == 0, result
    result["checks"] = json.loads(run.stdout)
    return result

BOUNDARY_HARNESS = r'''
@main struct BoundaryMain {
  @MainActor static func main() throws {
    let manager = FileManager.default
    let base = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
    let parent = base.appendingPathComponent("original-parent", isDirectory: true)
    let root = parent.appendingPathComponent("resources", isDirectory: true)
    let foreign = base.appendingPathComponent("foreign-parent", isDirectory: true)
    let foreignRoot = foreign.appendingPathComponent("resources", isDirectory: true)
    try manager.createDirectory(at: root, withIntermediateDirectories: true)
    try manager.createDirectory(at: foreignRoot, withIntermediateDirectories: true)
    try Data("original".utf8).write(to: root.appendingPathComponent("payload.txt"))
    try Data("outside-canary".utf8).write(to: foreignRoot.appendingPathComponent("payload.txt"))
    let handler = try ResourceScheme(root: root, host: "boundary-fixture")
    func read(_ path: String) throws -> Data {
      let (handle, _) = try handler.openResource(path)
      defer { try? handle.close() }
      return try handle.readToEnd() ?? Data()
    }
    guard try read("/payload.txt") == Data("original".utf8) else { throw ProbeFailure("初次读取不符") }
    let moved = base.appendingPathComponent("original-parent-moved", isDirectory: true)
    try manager.moveItem(at: parent, to: moved)
    try manager.createSymbolicLink(at: parent, withDestinationURL: foreign)
    guard try read("/payload.txt") == Data("original".utf8) else { throw ProbeFailure("祖先软链将读取重定向到外部") }
    let movedRoot = moved.appendingPathComponent("resources", isDirectory: true)
    try manager.createSymbolicLink(at: movedRoot.appendingPathComponent("leaf"), withDestinationURL: foreignRoot.appendingPathComponent("payload.txt"))
    try manager.createSymbolicLink(at: movedRoot.appendingPathComponent("nested"), withDestinationURL: foreignRoot)
    for path in ["/leaf", "/nested/payload.txt"] {
      var rejected = false
      do { _ = try read(path) } catch { rejected = true }
      guard rejected else { throw ProbeFailure("资源软链未拒绝") }
    }
    print("{\"original_root_read\":true,\"root_alias_redirected\":false,\"leaf_symlink_rejected\":true,\"nested_symlink_rejected\":true}")
  }
}
'''


def boundary_check(base):
    """直接提取生产探针的完整 handler 编译；不复制实现，不创建 NSApplication 或窗口。"""
    source_path = Path(__file__).resolve().parents[1] / "lib/webkit-core.swift"
    harness = base / "boundary.swift"
    harness.write_text("import AppKit\nimport Darwin\nimport Foundation\nimport WebKit\n" + BOUNDARY_HARNESS)
    binary = base / "boundary-probe"
    command = ["xcrun", "swiftc", "-parse-as-library", "-swift-version", "6", "-strict-concurrency=complete",
               "-default-isolation", "MainActor", "-enable-upcoming-feature", "NonisolatedNonsendingByDefault",
               "-O", "-target", "arm64-apple-macosx14.0", str(source_path), str(harness), "-o", str(binary)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stderr
    run = subprocess.run([str(binary), str(base)], capture_output=True, text=True, timeout=10)
    result = {"kind": "exact ResourceScheme source; no GUI or game", "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
              "exit": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
    (base / "boundary-evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert run.returncode == 0, result
    result["checks"] = json.loads(run.stdout)
    assert result["checks"] == {"original_root_read": True, "root_alias_redirected": False,
                                "leaf_symlink_rejected": True, "nested_symlink_rejected": True}, result
    return result

FIXTURE = r"""<!doctype html><meta charset="utf-8">
<title>WebKit fixture</title>
<style>body{font:18px system-ui;background:#ecf2ef;color:#18251f;padding:32px}
canvas{width:256px;height:144px;border:1px solid #888}pre{white-space:pre-wrap}</style>
<h1>WebKit 自有机制样例</h1><canvas id="canvas" width="128" height="72"></canvas><pre id="result">检查中</pre>
<script>
const result = window.__steamNativeWebKitFixture = {ready:false};
function xhr(path) {
  return new Promise(resolve => {
    const request = new XMLHttpRequest();
    request.open('GET',path,true); request.timeout=1500;
    request.onload=()=>resolve({event:'load',status:request.status,body:request.responseText});
    request.onerror=()=>resolve({event:'error',status:request.status,body:request.responseText});
    request.ontimeout=()=>resolve({event:'timeout',status:request.status,body:''});
    request.send();
  });
}
(async()=>{
  try {
    const key='dryport-webkit-fixture';
    const prior=Number(localStorage.getItem(key)||'0');
    localStorage.setItem(key,String(prior+1));
    result.storage={prior,after:Number(localStorage.getItem(key))};
  } catch(error) { result.storage={error:String(error)}; }
  try {
    const gl=document.querySelector('canvas').getContext('webgl');
    if (!gl) throw Error('WebGL context unavailable');
    gl.clearColor(0,0.5,1,1); gl.clear(gl.COLOR_BUFFER_BIT);
    const pixel=new Uint8Array(4);
    gl.readPixels(0,0,1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);
    result.webgl={version:gl.getParameter(gl.VERSION),pixel:Array.from(pixel)};
  } catch(error) { result.webgl={error:String(error)}; }
  try {
    const Offline=window.OfflineAudioContext||window.webkitOfflineAudioContext;
    const audio=new Offline(1,128,44100);
    const oscillator=audio.createOscillator(); const gain=audio.createGain();
    gain.gain.value=0.125; oscillator.connect(gain); gain.connect(audio.destination);
    oscillator.start(); oscillator.stop(128/44100);
    const buffer=await audio.startRendering();
    result.audio={api:typeof(window.AudioContext||window.webkitAudioContext),
      frames:buffer.length,sampleRate:buffer.sampleRate,
      peak:Math.max(...Array.from(buffer.getChannelData(0),Math.abs)),kind:'offline; no audible playback'};
  } catch(error) { result.audio={error:String(error)}; }
  result.inside=await xhr('payload.json');
  result.outside=await xhr('../outside-canary.txt');
  result.encodedOutside=await xhr('..%2foutside-canary.txt');
  result.ready=true;
  document.querySelector('#result').textContent=JSON.stringify(result,null,2);
  setTimeout(()=>{ location.href='https://outside.invalid/blocked'; },50);
})();
</script>"""


def inventory(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def invoke(binary, entry, output, store, timeout=4, mode="file"):
    process = subprocess.Popen([str(binary), "--entry", str(entry), "--output", str(output),
                                "--storeUUID", store, "--timeout", str(timeout), "--width", "800", "--height", "600", "--mode", mode],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout + 12)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        raise AssertionError("探针没有在自身截止时间内退出：" + stderr)
    return {"exit": process.returncode, "stdout": stdout, "stderr": stderr}


LIFECYCLE_SCRIPT = r'''
(() => {
  const fixtureID = document.documentElement.dataset.fixture;
  const kind = document.documentElement.dataset.kind;
  const prefix = 'dryport-lifecycle-' + fixtureID;
  const ledgerKey = prefix + '.ledger';
  const issues = [];
  let state = {protocol:1, fixture_id:fixtureID, documents:[], events:[], errors:[]};
  try {
    const prior = localStorage.getItem(ledgerKey);
    if (prior !== null) {
      const parsed = JSON.parse(prior);
      if (parsed.protocol !== 1 || parsed.fixture_id !== fixtureID) throw Error('fixture ledger mismatch');
      state = parsed;
    }
  } catch (error) { issues.push('ledger read failed: ' + String(error)); }
  const documentID = state.documents.length + 1;
  const activation = () => navigator.userActivation ? {
    isActive:navigator.userActivation.isActive, hasBeenActive:navigator.userActivation.hasBeenActive
  } : null;
  const eventKey = sequence => prefix + '.canary.' + sequence;
  function persistedEvents() {
    return state.events.map(item => {
      try { return {sequence:item.sequence, canary_readback:localStorage.getItem(eventKey(item.sequence)) === item.canary}; }
      catch { return {sequence:item.sequence, canary_readback:false}; }
    });
  }
  state.documents.push({id:documentID, kind, href:location.href,
    prior_event_readbacks:persistedEvents(), activation:activation()});
  function publish() {
    const result = {protocol:1, kind:'self-authored natural lifecycle fixture', fixture_id:fixtureID,
      ready:true, document_id:documentID, document_kind:kind, ledger:state,
      persisted_events:persistedEvents(), issues,
      message_handlers_present:!!window.webkit?.messageHandlers};
    window.__steamNativeWebKitFixture = result;
    document.querySelector('#result').textContent = JSON.stringify(result, null, 2);
  }
  function record(type, event, extra = {}) {
    const sequence = state.events.length + 1;
    const canary = 'SELF-LIFECYCLE:' + fixtureID + ':' + documentID + ':' + sequence + ':' + type;
    const item = {sequence, document_id:documentID, document_kind:kind, type,
      time:performance.now(), isTrusted:event?.isTrusted ?? null,
      cancelable:event?.cancelable ?? null, defaultPrevented:event?.defaultPrevented ?? null,
      persisted:typeof event?.persisted === 'boolean' ? event.persisted : null,
      visibility:document.visibilityState, activation:activation(), canary,
      canary_write_attempted:true, canary_write_ok:false, canary_readback_ok:false,
      ledger_write_ok:false, ...extra};
    state.events.push(item);
    try {
      localStorage.setItem(eventKey(sequence), canary);
      item.canary_write_ok = true;
      item.canary_readback_ok = localStorage.getItem(eventKey(sequence)) === canary;
      const serialized = JSON.stringify(state);
      localStorage.setItem(ledgerKey, serialized);
      item.ledger_write_ok = localStorage.getItem(ledgerKey) === serialized;
      localStorage.setItem(ledgerKey, JSON.stringify(state));
    } catch (error) { issues.push(type + ' persistence failed: ' + String(error)); }
    publish();
  }
  // 与 Koi 相同的属性入口，只记录/写入自有 canary，不返回取消值或调用 preventDefault。
  window.onbeforeunload = event => { record('beforeunload', event); };
  for (const type of ['pageshow','pagehide','unload']) {
    window.addEventListener(type, event => record(type, event));
  }
  window.addEventListener('error', event => {
    state.errors.push({document_id:documentID, type:'error', message:String(event.message || 'resource error'),
      filename:event.filename || '', line:event.lineno || 0, column:event.colno || 0});
    record('error_recorded', event);
  });
  window.addEventListener('unhandledrejection', event => {
    state.errors.push({document_id:documentID, type:'unhandledrejection', message:String(event.reason)});
    record('error_recorded', event);
  });
  document.querySelector('#reload').onclick = event => {
    record('navigation_requested', event, {navigation_kind:'same_url_reload'});
    window.location = window.location.protocol + '//' + window.location.host + window.location.pathname;
  };
  document.querySelector('#other').onclick = event => {
    record('navigation_requested', event, {navigation_kind:'other_local_document'});
    window.location = new URL('other.html', location.href).href;
  };
  record('document_loaded', null);
})();
'''


def terminal_lifecycle_script():
    script = LIFECYCLE_SCRIPT.replace(
        "window.onbeforeunload = event => { record('beforeunload', event); };",
        """state.cleanup_counts = state.cleanup_counts || {};
  window.onbeforeunload = event => {
    const count = (state.cleanup_counts[documentID] || 0) + 1;
    state.cleanup_counts[documentID] = count;
    if (count > 1) state.errors.push({document_id:documentID, type:'duplicate_cleanup', count});
    record('beforeunload', event, {cleanup_count:count,
      event_interface:Object.prototype.toString.call(event), return_value:event.returnValue});
  };""")
    script = script.replace("window.addEventListener(type, event => record(type, event));",
                            "window.addEventListener(type, event => record(type, event), {capture:true});")
    script = script.replace("const issues = [];", "const issues = []; let negativePagehideCase = null;")
    script = script.replace("ledger_write_ok:false, ...extra", "ledger_write_ok:false, negative_pagehide_case:negativePagehideCase, ...extra")
    script = script.replace("prior_event_readbacks:persistedEvents(), activation:activation()});",
                            "prior_event_readbacks:persistedEvents(), activation:activation(), page_message_handlers_present:!!window.webkit?.messageHandlers});")
    return script.replace("record('document_loaded', null);", r'''
  record('document_loaded', null);
  if (documentID === 1) {
    state.negative_pagehide_checks = [];
    for (const name of ['generic_event','untrusted_page_transition']) {
      negativePagehideCase = name;
      const check = {name, cleanup_before:state.cleanup_counts[documentID] || 0,
        cleanup_after:null, construction_error:null, dispatch_threw:false};
      try {
        const event = name === 'generic_event' ? new Event('pagehide') :
          new PageTransitionEvent('pagehide', {persisted:false});
        check.interface = Object.prototype.toString.call(event);
        check.isTrusted = event.isTrusted;
        check.persisted = event.persisted ?? null;
        try { check.dispatch_return = window.dispatchEvent(event); }
        catch (error) { check.dispatch_threw = true; check.dispatch_error = String(error); }
      } catch (error) { check.construction_error = String(error); }
      check.cleanup_after = state.cleanup_counts[documentID] || 0;
      state.negative_pagehide_checks.push(check);
      record('negative_pagehide_checked', null);
      negativePagehideCase = null;
    }
  }
''')


BEFOREUNLOAD_TYPES_SCRIPT = r'''
(() => {
  const marker = 'SELF BEFOREUNLOAD EXPECTED HANDLER ERROR';
  const result = window.__steamNativeWebKitFixture = {protocol:1, kind:'standard BeforeUnloadEvent self fixture',
    ready:false, cases:[], error_events:[], rejections:[], fatal:null,
    message_handlers_present:!!window.webkit?.messageHandlers};
  window.addEventListener('error', event => {
    result.error_events.push({message:String(event.message || ''), isTrusted:event.isTrusted});
  });
  window.addEventListener('unhandledrejection', event => { result.rejections.push(String(event.reason)); });
  const handlers = [
    ['no_return', () => {}], ['return_null', () => null],
    ['return_string', () => 'self cancellation'], ['return_empty', () => ''],
    ['prevent_default', event => { event.preventDefault(); }],
    ['set_return_value', event => { event.returnValue = 'self cancellation'; }],
    ['throw_handler', () => { throw Error(marker); }]
  ];
  try {
    for (const [name, handler] of handlers) {
      const event = document.createEvent('BeforeUnloadEvent');
      event.initEvent('beforeunload', false, true);
      const record = {name, type:event.type, interface:Object.prototype.toString.call(event),
        instance_of_beforeunload:typeof BeforeUnloadEvent === 'function' && event instanceof BeforeUnloadEvent,
        bubbles:event.bubbles, cancelable:event.cancelable, isTrusted:event.isTrusted,
        initial_return_value:event.returnValue, calls:0, dispatch_threw:false};
      window.onbeforeunload = received => { record.calls++; return handler(received); };
      try { record.dispatch_return = window.dispatchEvent(event); }
      catch (error) { record.dispatch_threw = true; record.dispatch_error = String(error); }
      record.default_prevented = event.defaultPrevented;
      record.return_value = event.returnValue;
      result.cases.push(record);
    }
  } catch (error) { result.fatal = String(error); }
  finally { window.onbeforeunload = null; }
  // Handler 抛错由全局 error 接收；dispatchEvent 正常返回不能掩盖它。
  setTimeout(() => {
    result.ready = true;
    document.querySelector('#result').textContent = JSON.stringify(result, null, 2);
  }, 20);
})();
'''


def beforeunload_types_prepare(base, binary):
    root = base / "beforeunload-types-fixture"
    root.mkdir()
    entry = root / "index.html"
    entry.write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>标准 BeforeUnloadEvent 自有类型检查</title>
<style>body{font:16px system-ui;margin:24px}pre{font:12px monospace;white-space:pre-wrap}</style>
<h1>标准 BeforeUnloadEvent 自有类型检查</h1>
<p>只操作自有事件与 handler。预期恰好出现一条自造 handler 错误；它必须被记录。</p>
<pre id="result">等待真实浏览器返回</pre><script>''' + BEFOREUNLOAD_TYPES_SCRIPT + '</script></html>', encoding="utf-8")
    output = base / "beforeunload-types-output"
    store = str(uuid.uuid4())
    argv = [str(binary.resolve()), "--entry", str(entry), "--output", str(output), "--storeUUID", store,
            "--mode", "scheme", "--timeout", "10", "--width", "1000", "--height", "720"]
    report = {"protocol":1, "kind":"prepared standard-event type fixture; no GUI started; no host fallback",
              "base":str(base), "resources":str(root), "output":str(output), "store_uuid":store,
              "argv":argv, "shell_command":shlex.join(argv), "resources_before":inventory(root),
              "probe_sha256":hashlib.sha256(binary.read_bytes()).hexdigest(),
              "check_command":shlex.join([sys.executable,"-B",str(Path(__file__).resolve()),"--beforeunload-types-check",str(base)])}
    (base / "beforeunload-types-preparation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def beforeunload_types_assess(observation):
    fixture = observation.get("page", {}).get("fixture") or {}
    failures = []
    if not observation.get("ok") or not observation.get("resources_unchanged") or not fixture.get("ready"):
        failures.append("探针采集、原件复核或类型样例执行未完成")
    if fixture.get("fatal") or fixture.get("message_handlers_present") is not False or fixture.get("rejections"):
        failures.append("样例发生未预期错误或存在消息桥")
    expected = {"no_return":(True,False,""), "return_null":(True,False,""),
                "return_string":(False,True,"self cancellation"), "return_empty":(False,True,""),
                "prevent_default":(False,True,""), "set_return_value":(True,False,"self cancellation"),
                "throw_handler":(True,False,"")}
    cases = fixture.get("cases", [])
    if [item.get("name") for item in cases] != list(expected):
        failures.append("标准事件测试项缺失或重复")
    for item in cases:
        common = (item.get("type") == "beforeunload" and item.get("interface") == "[object BeforeUnloadEvent]"
                  and item.get("instance_of_beforeunload") is True and item.get("bubbles") is False
                  and item.get("cancelable") is True and item.get("isTrusted") is False
                  and item.get("initial_return_value") == "" and item.get("calls") == 1
                  and item.get("dispatch_threw") is False)
        actual = (item.get("dispatch_return"),item.get("default_prevented"),item.get("return_value"))
        if not common or actual != expected.get(item.get("name")):
            failures.append(str(item.get("name")) + " 的真实事件类型或取消语义不符")
    errors = fixture.get("error_events", [])
    if len(errors) != 1 or "SELF BEFOREUNLOAD EXPECTED HANDLER ERROR" not in errors[0].get("message", ""):
        failures.append("handler 异常没有恰好由全局 error 记录一次")
    return {"protocol":1, "kind":"real browser standard-event type check; no game or normal-exit acceptance",
            "ok":not failures, "failures":failures, "fixture":fixture,
            "boundary":"isTrusted=false; successful dispatch does not mean handler success or durable storage"}


def lifecycle_prepare(base, binary, terminal=False):
    """只生成样例与明确命令；真实按钮点击由根代理协调，不自动启动 GUI。"""
    root = base / "lifecycle-fixture"
    root.mkdir()
    identifier = str(uuid.uuid4())
    script = terminal_lifecycle_script() if terminal else LIFECYCLE_SCRIPT
    for name, kind in (("index.html", "index"), ("other.html", "other")):
        html = f'''<!doctype html><html lang="zh-CN" data-fixture="{identifier}" data-kind="{kind}">
<meta charset="utf-8"><title>生命周期机制：{kind}</title>
<style>body{{font:17px system-ui;margin:24px;color:#18251f;background:#eff4f1}}button{{font:18px system-ui;margin:8px 16px 16px 0;padding:12px}}pre{{font:12px monospace;white-space:pre-wrap}}</style>
<h1>自有页面生命周期：{kind}</h1>
<p>先点击“同 URL 重载”，确认文档编号增加；再点击“另一本文档”。此页面只写专用测试 canary。</p>
<button id="reload">同 URL 重载</button><button id="other">另一本文档</button>
<pre id="result">正在读取自有测试记录</pre><script>{script}</script></html>'''
        (root / name).write_text(html, encoding="utf-8")
    store = str(uuid.uuid4())
    output = base / "lifecycle-output"
    argv = [str(binary.resolve()), "--entry", str(root / "index.html"), "--output", str(output),
            "--storeUUID", store, "--mode", "scheme", "--timeout", "120", "--width", "1000", "--height", "720"]
    if terminal:
        argv += ["--lifecycle", "terminal-beforeunload"]
    report = {"protocol": 1, "kind": "prepared only; no GUI or game was started", "fixture_id": identifier,
              "base": str(base), "resources": str(root), "output": str(output), "store_uuid": store,
              "argv": argv, "shell_command": shlex.join(argv), "resources_before": inventory(root),
              "probe_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "steps": ["根代理先与其他 GUI 试验协调，然后用上述参数启动自有探针。",
                        "真实点击一次“同 URL 重载”，确认 document_id 从 1 到 2。",
                        "真实点击一次“另一本文档”，确认 document_kind 为 other，document_id 为 3。",
                        "等待采集或正常点击窗口关闭；外部进程监督上限 130 秒。",
                        "用 --lifecycle-check <base> 读回；缺 beforeunload 会明确报告失败，不当成通过。"],
              "check_command": shlex.join([sys.executable, "-B", str(Path(__file__).resolve()), "--lifecycle-check", str(base)])}
    if terminal:
        report["kind"] = "prepared terminal-only fallback fixture; original natural control unchanged; no GUI started"
        report["negative_pagehide_guards_required"] = True
        report["page_world_message_handlers_must_be_absent"] = True
        report["steps"] = ["与其他 GUI 试验协调后启动；首次加载会自动派发 generic 与不可信 typed pagehide，两个 cleanup 计数都必须保持 0。",
                           "真实点击一次“同 URL 重载”，检查旧文档 pagehide→beforeunload→unload、cleanup_count=1。",
                           "真实点击一次“另一本文档”，缓存页 persisted=true 时不得补发或清理。",
                           "用窗口关闭按钮触发宿主终态重载，再由宿主采集；外部进程上限 130 秒。",
                           "用 --terminal-lifecycle-check <base> 判读；重复清理、错误类型或缓存页被清理均失败。"]
        report["check_command"] = shlex.join([sys.executable, "-B", str(Path(__file__).resolve()), "--terminal-lifecycle-check", str(base)])
    filename = "terminal-lifecycle-preparation.json" if terminal else "lifecycle-preparation.json"
    (base / filename).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def lifecycle_capture(preparation, observation):
    fixture = observation.get("page", {}).get("fixture") or {}
    ledger = fixture.get("ledger") or {}
    events, documents = ledger.get("events", []), ledger.get("documents", [])
    failures = []
    if not observation.get("ok") or not observation.get("resources_unchanged"):
        failures.append("探针采集或原件复核未通过")
    if fixture.get("fixture_id") != preparation["fixture_id"] or fixture.get("message_handlers_present") is not False:
        failures.append("样例身份不符或存在消息桥")
    if fixture.get("issues") or ledger.get("errors"):
        failures.append("存在写入问题或旧文档错误")
    if not events or not all(item.get("canary_write_ok") and item.get("canary_readback_ok") and item.get("ledger_write_ok") for item in events):
        failures.append("部分事件没有成功写入并即时回读 canary/记录")
    if len(fixture.get("persisted_events", [])) != len(events) or not all(item.get("canary_readback") for item in fixture.get("persisted_events", [])):
        failures.append("最终页面未完整读回各事件 canary")
    return fixture, ledger, events, documents, failures


def lifecycle_assess(preparation, observation):
    """分开采集完整性和 beforeunload 支持；自然缺失必须保留为负结果。"""
    fixture, ledger, events, documents, failures = lifecycle_capture(preparation, observation)
    transitions = []
    for navigation_kind in ("same_url_reload", "other_local_document"):
        requested = [item for item in events if item.get("type") == "navigation_requested" and item.get("navigation_kind") == navigation_kind]
        if len(requested) != 1:
            failures.append(navigation_kind + " 需要恰好一次真实按钮请求")
            continue
        request = requested[0]
        old = request["document_id"]
        next_document = next((item for item in documents if item.get("id") == old + 1), None)
        lifecycle = [item for item in events if item.get("document_id") == old and item["type"] in ("beforeunload", "pagehide", "unload")]
        sequence = [item["type"] for item in lifecycle]
        pagehide = next((item for item in lifecycle if item["type"] == "pagehide"), None)
        persisted = pagehide.get("persisted") if pagehide else None
        # 页面进入缓存时会暂存而不卸载；必须有 persisted=true，不能把单纯缺事件当完整。
        cached = persisted is True and sequence in (["beforeunload", "pagehide"], ["pagehide"])
        ordered = sequence in (["beforeunload", "pagehide", "unload"], ["pagehide", "unload"]) or cached
        valid = (request.get("isTrusted") is True and (request.get("activation") or {}).get("hasBeenActive") is True
                 and next_document is not None and ordered)
        if next_document and next_document.get("kind") != ("index" if navigation_kind == "same_url_reload" else "other"):
            valid = False
        if not valid:
            failures.append(navigation_kind + " 的真实激活、后继文档或事件顺序不完整")
        transitions.append({"navigation_kind": navigation_kind, "document_id": old,
                            "sequence": sequence, "observation_complete": valid,
                            "pagehide_persisted": persisted, "page_cached": cached,
                            "beforeunload_observed": "beforeunload" in sequence})
    complete = not failures and len(transitions) == 2
    supported = complete and all(item["beforeunload_observed"] for item in transitions)
    return {"protocol": 1, "kind": "natural lifecycle observation; no synthetic events or game acceptance",
            "observation_complete": complete, "beforeunload_supported": supported, "ok": supported,
            "result": "两类自然导航均执行 beforeunload" if supported else
                      "采集完整，但自然导航未执行 beforeunload" if complete else "观察不完整，不能判断生命周期机制",
            "transitions": transitions, "failures": failures, "fixture": fixture}


def terminal_lifecycle_assess(preparation, observation):
    fixture, ledger, events, documents, failures = lifecycle_capture(preparation, observation)
    counts = ledger.get("cleanup_counts") or {}
    if any(type(value) is not int or value > 1 for value in counts.values()):
        failures.append("同一文档重复清理或计数无效")
    negative = ledger.get("negative_pagehide_checks", [])
    if [item.get("name") for item in negative] != ["generic_event","untrusted_page_transition"]:
        failures.append("导航前的不可信 pagehide 负例缺失或重复")
    for item in negative:
        expected_type = "[object Event]" if item.get("name") == "generic_event" else "[object PageTransitionEvent]"
        if (item.get("interface") != expected_type or item.get("isTrusted") is not False
                or item.get("construction_error") is not None or item.get("dispatch_threw") is not False
                or item.get("cleanup_before") != 0 or item.get("cleanup_after") != 0
                or (item.get("name") == "untrusted_page_transition" and item.get("persisted") is not False)):
            failures.append(str(item.get("name")) + " 错误触发清理或未完成真实负例")
    if any(item.get("negative_pagehide_case") and item.get("type") == "beforeunload" for item in events):
        failures.append("不可信 pagehide 引发了 beforeunload")
    if any(item.get("page_message_handlers_present") is not False for item in documents):
        failures.append("某个 page world 出现消息桥或缺少检查")
    requests = [item for item in events if item.get("type") == "navigation_requested"]
    if [(item.get("document_id"),item.get("navigation_kind")) for item in requests] != [(1,"same_url_reload"),(2,"other_local_document")]:
        failures.append("需要恰好两次指定真实导航按钮操作")
    if not all(item.get("isTrusted") is True and (item.get("activation") or {}).get("hasBeenActive") is True for item in requests):
        failures.append("按钮缺少真实激活证据")
    if [(item.get("id"),item.get("kind")) for item in documents] != [(1,"index"),(2,"index"),(3,"other"),(4,"other")]:
        failures.append("两次按钮导航和最终退出重载未形成四个预期文档")
    if observation.get("lifecycle") != "terminal-beforeunload" or observation.get("reason") not in ("window_closed","application_quit"):
        failures.append("没有观测到指定宿主模式下的主动正常关闭")
    transitions = []
    for identifier, cached in ((1,False),(2,True),(3,False)):
        lifecycle = [item for item in events if item.get("document_id") == identifier and not item.get("negative_pagehide_case") and item.get("type") in ("beforeunload","pagehide","unload")]
        sequence = [item["type"] for item in lifecycle]
        pagehide = next((item for item in lifecycle if item["type"] == "pagehide"), {})
        before = [item for item in lifecycle if item["type"] == "beforeunload"]
        cleanup = counts.get(str(identifier),0)
        expected = ["pagehide"] if cached else ["pagehide","beforeunload","unload"]
        valid = sequence == expected and pagehide.get("persisted") is cached and cleanup == (0 if cached else 1)
        if not cached:
            valid = valid and len(before) == 1 and before[0].get("isTrusted") is False and before[0].get("event_interface") == "[object BeforeUnloadEvent]" and before[0].get("return_value") == ""
        if not valid:
            failures.append(f"文档 {identifier} 的事件顺序、真实类型或清理次数不符")
        transitions.append({"document_id":identifier,"sequence":sequence,"pagehide_persisted":pagehide.get("persisted"),
                            "cleanup_count":cleanup,"isTrusted":[item.get("isTrusted") for item in before],"passed":valid})
    return {"protocol":1,"kind":"terminal-only BeforeUnloadEvent fallback mechanism; separate from natural control",
            "ok":not failures,"terminal_reload_mechanism_passed":not failures,"failures":failures,"transitions":transitions,
            "negative_pagehide_checks":negative,
            "fixture":fixture,"boundary":"synthetic isTrusted=false; cached pages are not cleaned; no cancellation, game-save or general compatibility guarantee"}


def prepared_check(base, prefix, assessor):
    preparation = json.loads((base / (prefix + "-preparation.json")).read_text())
    observation = json.loads((Path(preparation["output"]) / "observation.json").read_text())
    report = assessor(preparation, observation)
    report["resources_unchanged"] = inventory(Path(preparation["resources"])) == preparation["resources_before"]
    if not report["resources_unchanged"]:
        report["ok"] = False
        if "terminal_reload_mechanism_passed" in report:
            report["terminal_reload_mechanism_passed"] = False
        report["failures"].append("样例资源在结束后变化")
    path = base / (prefix + "-evidence.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(path)
    return 0 if report["ok"] else 1


def lifecycle_check(base):
    preparation = json.loads((base / "lifecycle-preparation.json").read_text())
    output = Path(preparation["output"])
    observation = json.loads((output / "observation.json").read_text())
    report = lifecycle_assess(preparation, observation)
    unchanged = inventory(Path(preparation["resources"])) == preparation["resources_before"]
    report["resources_unchanged"] = unchanged
    if not unchanged:
        report["ok"] = False
        report["observation_complete"] = False
        report["failures"].append("样例资源在结束后变化")
    (base / "lifecycle-evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(base / "lifecycle-evidence.json")
    return 0 if report["ok"] else 1


def lifecycle_self_check(base):
    """只验证自有 logger 与判读器；模拟回调不代表浏览器实际派发了任何事件。"""
    script = r'''
function scenario(withBeforeUnload, oldError, failWrites, disposal = 'unload') {
  const values = new Map();
  let callbacks, buttons, destination;
  globalThis.window = globalThis;
  globalThis.navigator = {userActivation:{isActive:true, hasBeenActive:true}};
  globalThis.performance = {now:()=>123};
  globalThis.localStorage = {getItem:key=>values.get(key)??null, setItem:(key,value)=>{
    if (failWrites) throw Error('self fixture injected storage failure'); values.set(key,value);
  }};
  const address = {protocol:'sn-resource:',host:'self',pathname:'/index.html',href:'sn-resource://self/index.html'};
  Object.defineProperty(globalThis, 'location', {configurable:true,get:()=>address,set:value=>{destination=value;}});
  globalThis.URL = class { constructor(path) { this.href='sn-resource://self/'+path; } };
  const event = {isTrusted:true, cancelable:false, defaultPrevented:false, persisted:false};
  const boot = kind => {
    callbacks = {}; buttons = {'#reload':{},'#other':{},'#result':{}};
    globalThis.document = {documentElement:{dataset:{fixture:'selftest',kind}},visibilityState:'visible',
      querySelector:key=>buttons[key]};
    globalThis.addEventListener = (name, callback) => {callbacks[name]=callback;};
    address.pathname = kind==='other'?'/other.html':'/index.html'; address.href='sn-resource://self'+address.pathname;
''' + LIFECYCLE_SCRIPT + r'''
    callbacks.pageshow(event);
  };
  boot('index');
  buttons['#reload'].onclick(event);
  if (oldError) callbacks.error({message:'SELF OLD DOCUMENT ERROR',filename:'index.html',lineno:1});
  if (withBeforeUnload) globalThis.onbeforeunload({...event,cancelable:true});
  callbacks.pagehide(event); callbacks.unload(event);
  boot('index');
  buttons['#other'].onclick(event);
  if (withBeforeUnload) globalThis.onbeforeunload({...event,cancelable:true});
  callbacks.pagehide({...event,persisted:disposal==='bfcache'});
  if (disposal === 'unload') callbacks.unload(event);
  boot('other');
  return globalThis.__steamNativeWebKitFixture;
}
JSON.stringify({positive:scenario(true,false,false), missing_beforeunload:scenario(false,false,false),
                old_error:scenario(true,true,false), storage_failure:scenario(true,false,true),
                bfcache_positive:scenario(true,false,false,'bfcache'),
                bfcache_missing_beforeunload:scenario(false,false,false,'bfcache'),
                missing_unload_without_persistence:scenario(false,false,false,'missing')});
'''
    javascript = base / "lifecycle-self-check.js"
    javascript.write_text(script)
    harness = base / "lifecycle-self-check.swift"
    harness.write_text(r'''
import Foundation
import JavaScriptCore
@main struct LifecycleMain {
  @MainActor static func main() throws {
    let script = try String(contentsOfFile: CommandLine.arguments[1], encoding: .utf8)
    guard let context = JSContext(), let result = context.evaluateScript(script), context.exception == nil,
          let output = result.toString() else { throw NSError(domain:"lifecycle-self-check",code:1) }
    print(output)
  }
}
''')
    binary = base / "lifecycle-self-check"
    compiled = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "6", "-strict-concurrency=complete",
        "-default-isolation", "MainActor", "-O", "-target", "arm64-apple-macosx14.0", str(harness), "-o", str(binary)],
        capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stderr
    run = subprocess.run([str(binary), str(javascript)], capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr
    fixtures = json.loads(run.stdout)
    results = {name:lifecycle_assess({"fixture_id":"selftest"},
        {"ok":True,"resources_unchanged":True,"page":{"fixture":fixture}}) for name,fixture in fixtures.items()}
    assert results["positive"]["ok"] and results["positive"]["observation_complete"], results["positive"]
    assert not results["missing_beforeunload"]["ok"] and results["missing_beforeunload"]["observation_complete"], results["missing_beforeunload"]
    assert not results["old_error"]["ok"] and results["old_error"]["fixture"]["ledger"]["errors"][0]["document_id"] == 1, results["old_error"]
    assert not results["storage_failure"]["ok"], results["storage_failure"]
    assert results["bfcache_positive"]["ok"] and results["bfcache_positive"]["observation_complete"], results["bfcache_positive"]
    assert not results["bfcache_missing_beforeunload"]["ok"] and results["bfcache_missing_beforeunload"]["observation_complete"], results["bfcache_missing_beforeunload"]
    assert not results["missing_unload_without_persistence"]["ok"] and not results["missing_unload_without_persistence"]["observation_complete"], results["missing_unload_without_persistence"]
    report = {"kind":"JavaScriptCore self logger/reader unit check; no WebKit, GUI or genuine browser lifecycle",
              "ok":True,"driver_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "checks":{"positive":True,"missing_beforeunload_stays_failure":True,"old_document_error_retained":True,
                        "storage_failure_stays_failure":True,"bfcache_with_beforeunload":True,
                        "bfcache_missing_beforeunload_stays_failure":True,"missing_unload_needs_persisted_true":True,
                        "no_gui_started":True},"results":results}
    (base / "lifecycle-self-evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def terminal_lifecycle_self_check(base):
    lifecycle_self_check(base)
    script = r'''
function scenario(duplicate, cachedCleanup, generic, unsafeEvent = '') {
  const values = new Map(); let callbacks, buttons;
  globalThis.window = globalThis;
  globalThis.navigator = {userActivation:{isActive:true,hasBeenActive:true}};
  globalThis.performance = {now:()=>123};
  globalThis.localStorage = {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
  const address = {protocol:'sn-resource:',host:'self',pathname:'/index.html',href:'sn-resource://self/index.html'};
  Object.defineProperty(globalThis,'location',{configurable:true,get:()=>address,set:()=>{}});
  globalThis.URL = class {constructor(path){this.href='sn-resource://self/'+path;}};
  globalThis.Event = class {
    constructor(type){this.type=type;this.isTrusted=false;this.cancelable=false;this.defaultPrevented=false;}
    get [Symbol.toStringTag](){return 'Event';}
  };
  globalThis.PageTransitionEvent = class extends Event {
    constructor(type,options){super(type);this.persisted=options.persisted;}
    get [Symbol.toStringTag](){return 'PageTransitionEvent';}
  };
  const event = {isTrusted:true,cancelable:false,defaultPrevented:false,persisted:false};
  const before = () => globalThis.onbeforeunload({[Symbol.toStringTag]:generic?'Event':'BeforeUnloadEvent',
    isTrusted:false,cancelable:true,defaultPrevented:false,returnValue:''});
  globalThis.dispatchEvent = event => {
    callbacks[event.type](event);
    if ((unsafeEvent==='generic' && Object.prototype.toString.call(event)==='[object Event]') ||
        (unsafeEvent==='typed' && event instanceof PageTransitionEvent)) before();
    return true;
  };
  const boot = kind => {
    callbacks={}; buttons={'#reload':{},'#other':{},'#result':{}};
    globalThis.document={documentElement:{dataset:{fixture:'selftest',kind}},visibilityState:'visible',querySelector:key=>buttons[key]};
    globalThis.addEventListener=(type,callback)=>{callbacks[type]=callback;};
    address.pathname=kind==='other'?'/other.html':'/index.html'; address.href='sn-resource://self'+address.pathname;
''' + terminal_lifecycle_script() + r'''
    callbacks.pageshow(event);
  };
  boot('index'); buttons['#reload'].onclick(event);
  callbacks.pagehide(event); before(); if(duplicate)before(); callbacks.unload(event);
  boot('index'); buttons['#other'].onclick(event);
  callbacks.pagehide({...event,persisted:true}); if(cachedCleanup)before();
  boot('other'); callbacks.pagehide(event); before(); callbacks.unload(event);
  boot('other');
  return globalThis.__steamNativeWebKitFixture;
}
JSON.stringify({positive:scenario(false,false,false),duplicate_cleanup:scenario(true,false,false),
  cached_cleanup:scenario(false,true,false),generic_event:scenario(false,false,true),
  generic_pagehide_cleanup:scenario(false,false,false,'generic'),
  untrusted_pagehide_cleanup:scenario(false,false,false,'typed')});
'''
    javascript = base / "terminal-lifecycle-self-check.js"
    javascript.write_text(script)
    run = subprocess.run([str(base / "lifecycle-self-check"),str(javascript)],capture_output=True,text=True,timeout=10)
    assert run.returncode == 0, run.stderr
    fixtures = json.loads(run.stdout)
    reports = {name:terminal_lifecycle_assess({"fixture_id":"selftest"},
        {"ok":True,"resources_unchanged":True,"lifecycle":"terminal-beforeunload","reason":"window_closed","page":{"fixture":fixture}})
        for name,fixture in fixtures.items()}
    assert reports["positive"]["ok"], reports["positive"]
    for name in ("duplicate_cleanup","cached_cleanup","generic_event","generic_pagehide_cleanup","untrusted_pagehide_cleanup"):
        assert not reports[name]["ok"], reports[name]
    report = {"kind":"JavaScriptCore self logger/assessor unit check; not browser event construction or host fallback proof",
              "ok":True,"driver_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "checks":{"positive":True,"duplicate_cleanup_rejected":True,"cached_cleanup_rejected":True,
                        "generic_event_rejected":True,"generic_pagehide_cleanup_rejected":True,
                        "untrusted_pagehide_cleanup_rejected":True,"natural_control_regression_passed":True},"results":reports}
    (base / "terminal-lifecycle-self-evidence.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


TERMINAL_ERROR_SCRIPT = r'''
(() => {
  const fixtureID = document.documentElement.dataset.fixture;
  const marker = 'SELF TERMINAL HANDLER ERROR|' + fixtureID + '|page_document=1';
  const key = 'dryport-terminal-error-' + fixtureID;
  const history = JSON.parse(localStorage.getItem(key) || '[]');
  const current = {document_sequence:history.length + 1, beforeunload_calls:0,
    page_message_handlers_present:!!window.webkit?.messageHandlers};
  history.push(current);
  localStorage.setItem(key, JSON.stringify(history));
  const result = window.__steamNativeWebKitFixture = {protocol:1, kind:'old document original handler error',
    fixture_id:fixtureID, ready:true, history, document_sequence:current.document_sequence, errors:[], marker};
  const display = () => { document.querySelector('#result').textContent=JSON.stringify(result,null,2); };
  window.addEventListener('error',event=>{result.errors.push(String(event.message || 'resource error'));display();});
  window.onbeforeunload = () => {
    current.beforeunload_calls++;
    localStorage.setItem(key, JSON.stringify(history));
    if (current.document_sequence === 1) throw Error(marker);
  };
  display();
  if (current.document_sequence === 1) setTimeout(() => {
    current.navigation_request='self_timer_same_url_reload';
    localStorage.setItem(key, JSON.stringify(history));
    window.location=window.location.protocol+'//'+window.location.host+window.location.pathname;
  },500);
})();
'''


def terminal_error_prepare(base, binary):
    root = base / "terminal-error-fixture"
    root.mkdir()
    identifier = str(uuid.uuid4())
    entry = root / "index.html"
    entry.write_text(f'''<!doctype html><html lang="zh-CN" data-fixture="{identifier}"><meta charset="utf-8">
<title>旧文档 handler 错误负例</title><style>body{{font:16px system-ui;margin:24px}}pre{{white-space:pre-wrap}}</style>
<h1>旧文档 handler 错误负例</h1><p>首次加载 0.5 秒后仅重载一次；首文档原始 handler 故意抛错。新文档无错不能抹去 native 记录。</p>
<pre id="result"></pre><script>{TERMINAL_ERROR_SCRIPT}</script></html>''',encoding="utf-8")
    output = base / "terminal-error-output"
    argv = [str(binary.resolve()),"--entry",str(entry),"--output",str(output),"--storeUUID",str(uuid.uuid4()),
            "--mode","scheme","--lifecycle","terminal-beforeunload","--timeout","10","--width","1000","--height","720"]
    report = {"protocol":1,"kind":"prepared intentional old-document handler error; no GUI started",
              "fixture_id":identifier,"marker":"SELF TERMINAL HANDLER ERROR|"+identifier+"|page_document=1",
              "resources":str(root),"resources_before":inventory(root),"output":str(output),"argv":argv,
              "shell_command":shlex.join(argv),"probe_sha256":hashlib.sha256(binary.read_bytes()).hexdigest(),
              "expected_process_exit":1,"process_receipt":"process-result.json",
              "steps":["协调 GUI 后启动，无需输入。首文档 0.5 秒自动重载，探针第 10 秒进行终态重载并采集。",
                       "按根代理 supervisor 原样保留 process-result.json；必须 exit=1、external_kill=false、reaped=true。",
                       "运行检查命令；检查器成功表示负例如实失败，不表示保存成功。"],
              "check_command":shlex.join([sys.executable,"-B",str(Path(__file__).resolve()),"--terminal-error-check",str(base)])}
    (base / "terminal-error-preparation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def terminal_error_check(base):
    preparation = json.loads((base / "terminal-error-preparation.json").read_text())
    output = Path(preparation["output"])
    observation = json.loads((output / "observation.json").read_text())
    receipt = json.loads((output / "lifecycle.json").read_text())
    process = json.loads((base / "process-result.json").read_text())
    page = observation.get("page") or {}
    fixture = page.get("fixture") or {}
    events = receipt.get("events",[])
    starts = [item for item in events if item.get("kind") == "document_started"]
    errors = [item for item in events if item.get("kind") == "script_error"]
    failures = []
    if not (process.get("exit") == 1 and process.get("external_kill") is False and process.get("reaped") is True):
        failures.append("进程未以明确错误正常回收")
    if observation.get("ok") is not False or observation.get("observation_ok") is not False or not observation.get("failures"):
        failures.append("旧文档错误被最终总回执误判为成功")
    if page.get("observation",{}).get("errors") != [] or page.get("observation",{}).get("rejections") != [] or fixture.get("errors") != []:
        failures.append("最终新文档错误未清空，不能验证跨文档错误保留")
    if fixture.get("fixture_id") != preparation["fixture_id"] or [(item.get("document_sequence"),item.get("beforeunload_calls")) for item in fixture.get("history",[])] != [(1,1),(2,1),(3,0)]:
        failures.append("原始 handler 与两次重载的文档历史不符")
    if page.get("messageHandlersPresent") is not False or any(item.get("page_message_handlers_present") is not False for item in fixture.get("history",[])):
        failures.append("page world 出现消息桥")
    if len(starts) != 3 or len({item.get("document_id") for item in starts}) != 3:
        failures.append("native 未记录三个独立文档 ID")
    old_id = starts[0].get("document_id") if starts else None
    old = [item for item in events if item.get("document_id") == old_id]
    kinds = [item.get("kind") for item in old]
    if len(errors) != 1 or errors[0].get("document_id") != old_id or preparation["marker"] not in errors[0].get("message",""):
        failures.append("native 缺少唯一且归属于旧文档的指定 script_error")
    required = ["dispatch_started","beforeunload","script_error","dispatch_returned"]
    complete_dispatch = all(kinds.count(kind) == 1 for kind in required)
    if not complete_dispatch or [kinds.index(kind) for kind in required] != sorted(kinds.index(kind) for kind in required):
        failures.append("旧文档派发、原 handler 错误与正常返回未形成完整顺序")
    returned = next((item for item in old if item.get("kind") == "dispatch_returned"),{})
    if returned.get("accepted") is not True or returned.get("default_prevented") is not False:
        failures.append("该负例没有复现 handler 抛错但 dispatch 正常返回")
    if any(item.get("kind") in ("resource_error","dispatch_failed","receipt_overflow") for item in events):
        failures.append("资源、派发或溢出错误不能替代指定原 handler 错误")
    if observation.get("lifecycle_events") != events or receipt.get("saving_verified") is not False:
        failures.append("最终观察与跨文档 native 回执不一致")
    unchanged = observation.get("resources_unchanged") is True and inventory(Path(preparation["resources"])) == preparation["resources_before"]
    if not unchanged: failures.append("样例原件变化")
    report = {"protocol":1,"kind":"intentional handler-error negative acceptance; not save success",
              "ok":not failures,"negative_failure_detected":not failures,"failures":failures,"old_document_id":old_id,
              "new_document_ids":[item.get("document_id") for item in starts[1:]],"old_document_event_sequence":kinds,
              "matching_script_errors":errors,"final_page_errors":page.get("observation",{}).get("errors"),
              "process":process,"resources_unchanged":unchanged}
    path = base / "terminal-error-evidence.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)); print(path)
    return 0 if report["ok"] else 1


def lifecycle_send_limit_check(base):
    source_path = Path(__file__).resolve().parents[1] / "lib/webkit-core.swift"
    source = source_path.read_text()
    exact = source.split('let terminalLifecycleScript = #"""',1)[1].split('"""#',1)[0]
    script = r'''
function run(count, type) {
  const messages=[], callbacks={};
  globalThis.window={webkit:{messageHandlers:{snLifecycle:{postMessage:value=>messages.push(value)}}},
    addEventListener:(kind,callback)=>{callbacks[kind]=callback;}};
  globalThis.document={createEvent:()=>{throw Error('untrusted pagehide must not construct an event');}};
''' + exact + r'''
  for(let index=0;index<count;index++) callbacks[type](type==='error'?{message:'x'.repeat(900)}:{isTrusted:false,persisted:false});
  return messages;
}
const exactLimit=run(63,'error'), overflow=run(1000,'error'), fresh=run(1000,'pagehide');
function check(value,message){if(!value)throw Error(message);}
check(exactLimit.length===64&&!exactLimit.some(item=>item.kind==='receipt_overflow'),'normal limit');
for(const items of [overflow,fresh]) {
  check(items.length===65&&items[0].kind==='document_started','bounded count');
  check(items[64].kind==='receipt_overflow'&&items.filter(item=>item.kind==='receipt_overflow').length===1,'one overflow');
  check(Object.keys(items[64]).sort().join(',')==='document_id,kind','overflow payload must be empty');
  check(new Set(items.map(item=>item.document_id)).size===1,'one document identity');
}
check(overflow[0].document_id!==fresh[0].document_id,'fresh document identity');
check(overflow.slice(1,64).every(item=>item.kind==='script_error'&&item.message.length===512),'error truncation');
check(fresh.slice(1,64).every(item=>item.kind==='pagehide'&&item.trusted===false),'untrusted input bypass');
JSON.stringify({normal_limit:64,total_with_overflow:65,one_overflow:true,attempted_per_overflow_case:1000,
                fresh_document_limit_reset:true,error_message_limit:512,untrusted_no_dispatch:true});
'''
    javascript = base / "lifecycle-send-limit.js"; javascript.write_text(script)
    harness = base / "lifecycle-send-limit.swift"
    harness.write_text(r'''
import Foundation
import JavaScriptCore
@main struct LimitMain {
  @MainActor static func main() throws {
    let text=try String(contentsOfFile:CommandLine.arguments[1],encoding:.utf8)
    let context=JSContext()!
    guard let value=context.evaluateScript(text),context.exception==nil,let output=value.toString()
      else { throw NSError(domain:context.exception?.toString() ?? "limit check failed",code:1) }
    print(output)
  }
}
''')
    binary = base / "lifecycle-send-limit"
    compile_result = subprocess.run(["xcrun","swiftc","-parse-as-library","-swift-version","6","-strict-concurrency=complete",
        "-default-isolation","MainActor","-O","-target","arm64-apple-macosx14.0",str(harness),"-o",str(binary)],capture_output=True,text=True,timeout=60)
    assert compile_result.returncode == 0, compile_result.stderr
    run = subprocess.run([str(binary),str(javascript)],capture_output=True,text=True,timeout=10)
    report = {"kind":"exact Swift embedded script in JavaScriptCore; no GUI; JS send cap only, not native 128 cap",
              "source_sha256":hashlib.sha256(source_path.read_bytes()).hexdigest(),
              "script_sha256":hashlib.sha256(exact.encode()).hexdigest(),"exit":run.returncode,"stdout":run.stdout,"stderr":run.stderr}
    path=base / "lifecycle-send-limit-evidence.json"; path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    assert run.returncode == 0, report
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, help="编译后的 probe-webkit 可执行文件")
    parser.add_argument("--boundary-only", action="store_true", help="仅编译并检查读取边界，不启动窗口")
    parser.add_argument("--observer-only", action="store_true", help="仅用 JavaScriptCore 检查观察脚本和超时参数，不启动窗口")
    parser.add_argument("--timing-only", action="store_true", help="仅检查共用绝对计时与迟启动反例，不启动窗口")
    parser.add_argument("--lifecycle-prepare", action="store_true", help="仅生成自然生命周期样例和操作命令，不启动窗口")
    parser.add_argument("--lifecycle-check", type=Path, help="只读指定样例目录中的真实生命周期回执")
    parser.add_argument("--lifecycle-self-check", action="store_true", help="只用 JavaScriptCore 检查自有生命周期记录与正负判读")
    parser.add_argument("--beforeunload-types-prepare", action="store_true", help="只生成标准 BeforeUnloadEvent 类型样例，不启动窗口")
    parser.add_argument("--beforeunload-types-check", type=Path, help="读取真实浏览器标准事件类型回执")
    parser.add_argument("--terminal-lifecycle-prepare", action="store_true", help="只生成独立终态补发样例，不启动窗口")
    parser.add_argument("--terminal-lifecycle-check", type=Path, help="读取独立终态补发样例回执")
    parser.add_argument("--terminal-lifecycle-self-check", action="store_true", help="无窗口检查终态记录器、次数和类型判读")
    parser.add_argument("--terminal-error-prepare", action="store_true", help="只生成旧文档原 handler 抛错负例，不启动窗口")
    parser.add_argument("--terminal-error-check", type=Path, help="核对旧文档错误保留及明确失败退出")
    parser.add_argument("--lifecycle-send-limit-check", action="store_true", help="仅用 JavaScriptCore 检验精确宿主脚本发送上限")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("需要 macOS，不运行真实游戏")
    if args.lifecycle_check:
        return lifecycle_check(args.lifecycle_check.resolve())
    if args.beforeunload_types_check:
        return prepared_check(args.beforeunload_types_check.resolve(),"beforeunload-types",lambda _preparation,observation:beforeunload_types_assess(observation))
    if args.terminal_lifecycle_check:
        return prepared_check(args.terminal_lifecycle_check.resolve(),"terminal-lifecycle",terminal_lifecycle_assess)
    if args.terminal_error_check:
        return terminal_error_check(args.terminal_error_check.resolve())
    base = Path(tempfile.mkdtemp(prefix="sn-webkit-mechanism-")).resolve()
    if args.lifecycle_send_limit_check:
        lifecycle_send_limit_check(base)
        print(base / "lifecycle-send-limit-evidence.json")
        return 0
    if args.terminal_error_prepare:
        if args.probe is None or not args.probe.is_file(): parser.error("生成负例需要 --probe 指定现有探针")
        terminal_error_prepare(base,args.probe)
        print(base / "terminal-error-preparation.json")
        return 0
    if args.terminal_lifecycle_self_check:
        terminal_lifecycle_self_check(base)
        print(base / "terminal-lifecycle-self-evidence.json")
        return 0
    if args.beforeunload_types_prepare or args.terminal_lifecycle_prepare:
        if args.probe is None or not args.probe.is_file():
            parser.error("生成样例需要 --probe 指定现有探针")
        if args.beforeunload_types_prepare:
            beforeunload_types_prepare(base,args.probe)
            print(base / "beforeunload-types-preparation.json")
        else:
            lifecycle_prepare(base,args.probe,terminal=True)
            print(base / "terminal-lifecycle-preparation.json")
        return 0
    if args.lifecycle_self_check:
        lifecycle_self_check(base)
        print(base / "lifecycle-self-evidence.json")
        return 0
    if args.lifecycle_prepare:
        if args.probe is None or not args.probe.is_file():
            parser.error("--lifecycle-prepare 需要 --probe 指定现有探针")
        lifecycle_prepare(base, args.probe)
        print(base / "lifecycle-preparation.json")
        return 0
    if args.timing_only:
        timing_check(base)
        print(base / "timing-evidence.json")
        return 0
    if args.observer_only:
        observer_check(base)
        print(base / "observer-evidence.json")
        return 0
    if args.boundary_only:
        boundary_check(base)
        print(base / "boundary-evidence.json")
        return 0
    if args.probe is None:
        parser.error("需要 --probe、--boundary-only、--observer-only 或 --timing-only")
    game = base / "fixture"
    game.mkdir()
    entry = game / "index.html"
    entry.write_text(FIXTURE, encoding="utf8")
    (game / "payload.json").write_text('{"local":"allowed resource"}')
    (base / "outside-canary.txt").write_text("outside-must-not-be-readable")
    (base / "outside.html").write_text("<title>outside page must not load</title>")
    before = inventory(game)
    stores = [str(uuid.uuid4()) for _ in range(4)]
    evidence = {"kind": "self-authored windowed WebKit mechanism; no game compatibility or FPS claim",
                "ok": False, "stores": stores, "runs": []}
    try:
        evidence["observer_contract"] = observer_check(base)
        evidence["absolute_timing"] = timing_check(base)
        evidence["resource_boundary"] = boundary_check(base)
        combinations = [(mode, store, prior) for mode, a, b in (("file", 0, 1), ("scheme", 2, 3))
                        for store, prior in ((stores[a], 0), (stores[a], 1), (stores[b], 0))]
        for index, (mode, store, expected_prior) in enumerate(combinations, 1):
            output = base / f"{mode}-run-{index}"
            run = invoke(args.probe.resolve(), entry, output, store, mode=mode)
            run["mode"] = mode
            run["output"] = str(output)
            evidence["runs"].append(run)
            report = json.loads((output / "observation.json").read_text()) if (output / "observation.json").exists() else None
            run["observation"] = report
            assert run["exit"] == 0 and report and report["ok"], run
            partial = json.loads((output / "page-observation.json").read_text())
            progress = json.loads((output / "progress.json").read_text())
            assert partial["complete"] is False and partial["page"] == report["page"], partial
            assert progress["complete"] is True and progress["stage"] == "completed", progress
            stage_names = [step["stage"] for step in progress["stages"]]
            assert stage_names.index("page_captured") < stage_names.index("snapshot_started") < stage_names.index("resource_inventory_started"), progress
            page, fixture = report["page"], report["page"]["fixture"]
            assert fixture["ready"] is True, fixture
            assert fixture["storage"] == {"prior": expected_prior, "after": expected_prior + 1}, fixture
            local_xhr = fixture["inside"]["event"] == "load" and fixture["inside"]["body"] == '{"local":"allowed resource"}'
            run["local_xhr_supported"] = local_xhr
            if mode == "scheme":
                assert local_xhr, fixture
            else:
                # 原始 file 机制的负结果单独留档；不能把成功截屏当作 XHR 已可用。
                assert local_xhr or (fixture["inside"]["event"] == "error" and not fixture["inside"]["body"]), fixture
            assert fixture["outside"]["event"] == "error" and not fixture["outside"]["body"], fixture
            assert fixture["encodedOutside"]["event"] == "error" and not fixture["encodedOutside"]["body"], fixture
            assert page["title"] == "WebKit fixture" and "https://outside.invalid/blocked" in report["blocked_navigations"], report
            assert page["messageHandlersPresent"] is False and page["requireType"] == "undefined", page
            assert report["element_fullscreen_preference"] is False and report["window_fullscreen"] is False, report
            assert page["fullscreenEnabled"] is None or page["fullscreenEnabled"] is False, report
            assert fixture["webgl"]["pixel"] == [0, 128, 255, 255], fixture
            assert fixture["audio"]["frames"] == 128 and fixture["audio"]["sampleRate"] == 44100 and fixture["audio"]["peak"] > 0, fixture
            assert page["observation"]["raf"]["frames"] > 0, page
            assert not page["observation"]["errors"] and not page["observation"]["rejections"], page
            assert report["resources_unchanged"] is True and inventory(game) == before, report
            png = (output / "snapshot.png").read_bytes()
            assert png[:8] == b"\x89PNG\r\n\x1a\n" and all(struct.unpack(">II", png[16:24])), "快照不是有效 PNG"
        evidence["file_local_xhr_supported"] = all(run["local_xhr_supported"] for run in evidence["runs"] if run["mode"] == "file")
        evidence["scheme_mechanism_passed"] = True
        sentinel = base / "existing-output"
        sentinel.mkdir()
        (sentinel / "keep").write_text("preserved")
        rejected = invoke(args.probe.resolve(), entry, sentinel, stores[0], 2)
        assert rejected["exit"] == 2 and (sentinel / "keep").read_text() == "preserved", rejected
        rejected = invoke(args.probe.resolve(), entry, game / "nested-output", stores[0], 2)
        assert rejected["exit"] == 2 and not (game / "nested-output").exists(), rejected
        linked = base / "linked-fixture"
        linked.mkdir()
        (linked / "index.html").write_text("<title>rejected symlink fixture</title>")
        (linked / "linked-resource").symlink_to(base / "outside-canary.txt")
        rejected = invoke(args.probe.resolve(), linked / "index.html", base / "symlink-output", stores[0], 2, mode="scheme")
        assert rejected["exit"] == 2 and not (base / "symlink-output").exists(), rejected
        hang = base / "hang-fixture"
        hang.mkdir()
        (hang / "index.html").write_text("<script>while(true){}</script>")
        stopped = invoke(args.probe.resolve(), hang / "index.html", base / "hang-output", stores[0], 2, mode="scheme")
        deadline = json.loads((base / "hang-output" / "deadline.json").read_text())
        assert stopped["exit"] == 1 and deadline["reason"] == "observation_deadline_exceeded", stopped
        assert deadline["stage_at_deadline"] == "evaluate_javascript_started" and deadline["page_captured"] is False, deadline
        assert json.loads((base / "hang-output" / "progress.json").read_text())["stage"] == "evaluate_javascript_started", deadline
        evidence["deadline"] = {"process": stopped, "report": deadline}
        evidence["ok"] = True
    finally:
        evidence["resources_unchanged"] = inventory(game) == before
        evidence["ok"] = evidence["ok"] and evidence["resources_unchanged"]
        (base / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        print(base / "evidence.json")
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
