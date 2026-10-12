#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""对提取到临时目录的 HLSLcc 请求做禁用/冷/热对照，不改游戏或默认运行时。"""
import argparse
import concurrent.futures
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics
import platform
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from paths import SN_LIB
sys.path.insert(0, str(SN_LIB / "unity-retarget"))
import retarget
from shader_cache import ShaderCache


def collect(resources, directory):
    import UnityPy
    import retarget_verify
    profile, requests, workload = [], [], []
    total_bytes = 0
    original = retarget.run_hlslcc
    try:
        for index, path in enumerate(resources):
            started = time.perf_counter()
            source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            env = UnityPy.load(str(path))
            retarget.set_unity_version(env.file.version)
            current, errors = [], []
            shaders = 0

            def capture(dxbc, lang, flags):
                nonlocal total_bytes
                if len(current) < 512 and total_bytes + len(dxbc) <= 32 * 1024 * 1024:
                    sha = hashlib.sha256(dxbc).hexdigest()
                    target = directory / (sha + ".dxbc")
                    if not target.exists():
                        target.write_bytes(dxbc)
                    current.append({"input_sha256": sha, "input_file": str(target), "lang": lang, "flags": flags})
                    total_bytes += len(dxbc)
                return None, "profile-only"

            retarget.run_hlslcc = capture
            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                for obj in env.objects:
                    if obj.type.name != "Shader":
                        continue
                    tt = obj.read_typetree()
                    if retarget.PLAT_D3D11 not in tt["platforms"]:
                        continue
                    try:
                        retarget_verify.strip(tt)
                        retarget.process_shader(tt, True, True, pool, None)
                        shaders += 1
                    except Exception as exc:
                        errors.append(str(exc))
                    if len(current) >= 512 or total_bytes >= 32 * 1024 * 1024:
                        break
            if source_hash != hashlib.sha256(path.read_bytes()).hexdigest():
                raise SystemExit("停止：输入资源在测量期间发生变化")
            unique = len({(item["input_sha256"], item["lang"], item["flags"]) for item in current})
            profile.append({"source": "resource-%d" % (index + 1), "unity_version": str(env.file.version),
                            "resource_sha256": source_hash, "requests": len(current), "unique_compiler_inputs": unique,
                            "duplicates": len(current) - unique, "shaders_walked": shaders,
                            "extraction_seconds": time.perf_counter() - started, "extraction_errors": errors,
                            "source_unchanged": True})
            requests.extend(current)
            workload.extend(current[:128 // len(resources)])
    finally:
        retarget.run_hlslcc = original
        retarget.log.clear()
        retarget.errors.clear()
    return {"profile": profile, "requests": requests, "benchmark_requests": workload,
            "compiler_sha256": hashlib.sha256(Path(retarget.CLI).read_bytes()).hexdigest()}


def digest(results):
    return hashlib.sha256(json.dumps(results, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, nargs="?", help="包含 benchmark_requests 的既有提取清单")
    parser.add_argument("--resource", type=Path, action="append", help="只读提取 Unity 资源，最多两份；生成的输入留在临时目录")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True, help="测量结果 JSON")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 2 or not 1 <= args.trials <= 5:
        parser.error("本试点只允许 1 至 2 个并发任务、1 至 5 轮对照")
    if bool(args.manifest) == bool(args.resource) or len(args.resource or []) > 2:
        parser.error("请提供提取清单，或使用 --resource 指定 1 至 2 份资源")
    with tempfile.TemporaryDirectory(prefix="shader-cache-inputs-") as temporary:
        manifest = json.loads(args.manifest.read_text()) if args.manifest else collect(args.resource, Path(temporary))
        inputs = load_inputs(manifest, parser)
    benchmark(args, manifest, inputs)


def load_inputs(manifest, parser):
    requests = manifest["benchmark_requests"]
    if not 1 <= len(requests) <= 128:
        parser.error("每轮需要 1 至 128 次请求")
    inputs = []
    for request in requests:
        data = Path(request["input_file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != request["input_sha256"]:
            parser.error("输入文件 SHA256 不匹配")
        inputs.append((data, request["lang"], request["flags"]))
    return inputs


def benchmark(args, manifest, inputs):
    before = args.manifest.parent / "baseline.json" if args.manifest else None
    reference = json.loads(before.read_text())["results"] if before and before.exists() else None
    rows = []
    with tempfile.TemporaryDirectory(prefix="shader-cache-benchmark-") as temporary:
        for trial in range(args.trials):
            directory = Path(temporary) / str(trial)
            for mode in ("disabled", "cold", "hot"):
                # 每组新对象模拟新进程；热组只继承磁盘结果，包含动态库发现和指纹开销。
                cache = ShaderCache(directory, enabled=mode != "disabled")
                retarget.HLSLCC_CACHE = cache
                actual_processes = 0
                count_lock = threading.Lock()
                original_run = retarget.subprocess.run

                def counted_run(command, *positional, **keyword):
                    nonlocal actual_processes
                    if isinstance(command, (list, tuple)) and command[0] == retarget.CLI:
                        with count_lock:
                            actual_processes += 1
                    return original_run(command, *positional, **keyword)

                start = time.perf_counter()
                retarget.subprocess.run = counted_run
                try:
                    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
                        results = list(pool.map(lambda request: retarget.run_hlslcc(*request), inputs))
                finally:
                    retarget.subprocess.run = original_run
                elapsed = time.perf_counter() - start
                normalized = json.loads(json.dumps(results))
                if reference is None:
                    reference = normalized
                if reference != normalized:
                    raise SystemExit("对照失败：源码字节或 reflection 与禁用缓存基线不同")
                if elapsed > 60:
                    raise SystemExit("对照停止：单组耗时超过 60 秒")
                if actual_processes != cache.stats().get("compiles", 0):
                    raise SystemExit("对照失败：实际编译器子进程数与缓存计数不一致")
                row = {"trial": trial + 1, "mode": mode, "seconds": elapsed, "stats": cache.stats(),
                       "actual_compiler_processes": actual_processes,
                       "successes": sum(result[0] is not None for result in results), "result_sha256": digest(results)}
                rows.append(row)
                print(json.dumps(row), flush=True)
        summary = {mode: {"median_seconds": statistics.median(row["seconds"] for row in rows if row["mode"] == mode),
                          "compiler_processes": [row["actual_compiler_processes"] for row in rows if row["mode"] == mode]}
                   for mode in ("disabled", "cold", "hot")}
        report = {"measured_at": datetime.now().astimezone().isoformat(),
                  "python": sys.version.split()[0], "system": platform.platform(),
                  "cpu": subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip(),
                  "memory_bytes": int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)),
                  "implementation_sha256": {name: hashlib.sha256((SN_LIB / "unity-retarget" / name).read_bytes()).hexdigest()
                                            for name in ("retarget.py", "shader_cache.py")},
                  "requests": len(inputs), "jobs": args.jobs, "profile": manifest.get("profile"),
                  "compiler_sha256": hashlib.sha256(Path(retarget.CLI).read_bytes()).hexdigest(),
                  "compiler_and_libraries_fingerprint": cache.identity.fingerprint(retarget.CLI),
                  "trials": rows, "summary": summary,
                  "output_identical": True, "scope": "HLSLcc 纯计算；不含资源解析、重新打包或游戏运行"}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
