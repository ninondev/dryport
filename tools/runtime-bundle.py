#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""预编译运行时包：打包本机已构建的运行时，或按清单校验后装进 SN_RUNTIMES。

  tools/runtime-bundle.py pack --out 目录 [--only wine-cx,mkxp-z] [--level 6]
      按 lib/runtimes.json 把本机已有且通过架构检查的运行时打成 tar.xz（没有 xz 就 tar.gz），写 runtime-manifest.json：
      每项的文件名、SHA256、字节数、版本、许可证与来源，以及随包的许可证原文（third-party-licenses/<名字>/，
      登记的原文缺失时整个运行时跳过）。Unity 播放器、Steam 组件永远不在清单里。
  tools/runtime-bundle.py check --from 目录或URL [--only ...]
      只读：对照清单说明本机哪些已就绪、哪些会装、哪些清单里没有。
  tools/runtime-bundle.py install --from 目录或URL [--only ...] [--replace] [--untrusted-local]
      下载或复制归档到 SN_RUNTIMES/build/bundles/，SHA256 不符就拒绝；解包到暂存目录，主程序架构核对通过后整体原子落位。
      目标已存在时默认跳过（--replace 才把旧目录改名保留后换新）。记录写 SN_RUNTIMES/installed-runtimes.json。
      归档里带的许可证原文（third-party-licenses/<名字>/）随产品一起落位到 SN_RUNTIMES 同名目录，旧的原样改名保留。
      来源只认 https:// 或本地目录（明文 http 拒绝）；清单里的 archive/product/unpack_to/check.path
      必须是落在运行时目录内的相对路径；解包前先列归档，绝对路径、含 ..、链接目标越界的成员拒绝；
      清单本身要过哈希认证（lib/runtimes.json 的 manifest_hashes 登记过），本地目录可用 --untrusted-local 显式信任。

旧归档可能缺许可证原文；安装校验不构成许可证审查。
只动 SN_RUNTIMES；不碰游戏、Steam、系统。所有数字来自本次执行。
"""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from paths import SN_HOME, SN_RUNTIMES
from bundle_safety import output_violation, require_output, output_parent_fd, verify_parent_fd, atomic_output, archive_violation

REGISTRY = Path(__file__).resolve().parent.parent / "lib/runtimes.json"
MANIFEST = "runtime-manifest.json"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def archs(path):
    """file(1) 报告的架构集合；不是 Mach-O 或不存在返回空集。"""
    try:
        out = subprocess.run(["file", "-b", str(path)], capture_output=True, text=True, timeout=30).stdout.lower()
    except Exception:
        return set()
    found = set()
    for a in ("arm64", "x86_64", "i386"):
        if a in out:
            found.add(a)
    return found


def check_ok(root, spec):
    p = root / spec["check"]["path"]
    if not p.is_file():
        return f"缺主程序 {spec['check']['path']}"
    want = spec["check"].get("arch")
    if want and want not in archs(p):
        return f"{spec['check']['path']} 不含 {want}（file 报告 {sorted(archs(p)) or '非 Mach-O'}）"
    for rel in spec.get("also_required", []):
        if not (root / rel).exists():
            return f"缺 {rel}"
    return None


def relspec(e):
    """把清单项的核对路径换成相对 unpack_to（归档内部的路径）。"""
    base = e["unpack_to"]
    def rel(path):
        return str(Path(path).relative_to(base)) if base != "." else path
    return {"check": {"path": rel(e["check"]["path"]), "arch": e["check"].get("arch")},
            "also_required": [rel(x) for x in e.get("also_required", [])]}


def version_of(root, spec):
    for how in spec.get("version_from", []):
        kind, _, arg = how.partition(":")
        try:
            if kind == "file":
                text = (root / arg).read_text(errors="replace").strip()
                if text:
                    return text.splitlines()[0][:120]
            elif kind == "git":
                repo = SN_HOME if arg == "SN_HOME" else root / arg
                r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=30)
                if r.returncode == 0 and r.stdout.strip():
                    return ("dryport " if arg == "SN_HOME" else arg + " ") + r.stdout.strip()
            elif kind == "const":
                return arg
        except Exception:
            continue
    return "unknown"


def load_registry(path):
    data = json.loads(Path(path).read_text(encoding="utf8"))
    return data["runtimes"]


def flatten_license_path(rel):
    """归档内的平铺名：@SN_HOME/ 前缀去掉、/ 换成 __，全部收进 third-party-licenses/<名字>/ 一层。"""
    return (rel[len("@SN_HOME/"):] if rel.startswith("@SN_HOME/") else rel).replace("/", "__")


def license_source(root, rel):
    """@SN_HOME/ 开头的原文在仓库里，其余相对运行时根目录。"""
    return SN_HOME / rel[len("@SN_HOME/"):] if rel.startswith("@SN_HOME/") else root / rel


def stage_license_texts(root, out, name, spec, version):
    """把清单登记的许可证原文复制到 out 下的临时目录 third-party-licenses/<名字>/，并写 NOTICE.txt。

    返回 (临时目录, 归档内路径列表, 失败原因)；清单没登记或原文缺失时前两项为 None。
    每个新打包的运行时必须带登记的法律原文；缺失时整个运行时跳过。
    """
    files = spec.get("license_files")
    if not files:
        return None, None, "清单没登记许可证原文"
    found = []
    for rel in files:
        src = license_source(root, rel)
        if not src.is_file():   # 登记成目录也算缺：copy2 复制不了目录，会在建好暂存目录后中途崩掉
            return None, None, f"缺许可证原文：{rel}"
        found.append((src, flatten_license_path(rel)))
    stage = Path(tempfile.mkdtemp(prefix=".pack-lic-", dir=out))
    base = stage / "third-party-licenses" / name
    base.mkdir(parents=True)
    members = []
    for src, flat in found:
        shutil.copy2(src, base / flat)
        members.append(f"third-party-licenses/{name}/{flat}")
    (base / "NOTICE.txt").write_text(
        "\n".join([name, version, spec["license"], spec["source"], "", *spec.get("notice", [])]) + "\n",
        encoding="utf8")
    members.append(f"third-party-licenses/{name}/NOTICE.txt")
    return stage, members, None


def selected(reg, only):
    names = [n.strip() for n in only.split(",")] if only else list(reg)
    unknown = [n for n in names if n not in reg]
    if unknown:
        sys.exit(f"清单里没有这些运行时：{unknown}；可选 {list(reg)}")
    return names


def compressor():
    if shutil.which("xz"):
        return "xz", ".tar.xz"
    return "gzip", ".tar.gz"


def cmd_pack(a, reg):
    root = Path(a.runtimes).resolve()
    out = Path(a.out).resolve(); out.mkdir(parents=True, exist_ok=True)
    tool, ext = compressor()
    manifest = {"manifest_version": 1, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "host": {"macos": platform.mac_ver()[0], "machine": platform.machine()},
                "compression": tool, "runtimes": []}
    skipped = []
    for name in selected(reg, a.only):
        spec = reg[name]
        why = check_ok(root, spec)
        if why:
            skipped.append({"name": name, "why": why}); print(f"跳过 {name}：{why}"); continue
        product = root / spec["product"]
        parent = product.parent
        version = version_of(root, spec)
        safe_version = "".join(c if c.isalnum() or c in ".-_" else "-" for c in version)[:60]
        archive = out / f"{name}-{safe_version}{ext}"
        members = [product.name] if not spec.get("include") else [f"{product.name}/{m}" for m in spec["include"]]
        for m in members:
            if not (parent / m).exists():
                sys.exit(f"{name}：要打包的 {m} 不存在")
        lic_stage, license_members, lic_why = stage_license_texts(root, out, name, spec, version)
        if lic_why:
            skipped.append({"name": name, "why": lic_why}); print(f"跳过 {name}：{lic_why}"); continue
        t0 = time.monotonic()
        tar_root, cleanup = parent, None
        if product.is_symlink() and product.resolve().is_file():
            # 单文件产物是软链（install.sh 用 ln 落位后可能是）：打真文件，仍用产物名；app 目录里的软链照旧保留
            cleanup = Path(tempfile.mkdtemp(prefix=".pack-", dir=out))
            shutil.copy2(product.resolve(), cleanup / product.name); tar_root = cleanup
        with open(archive, "wb") as fh:
            # 许可证原文追加进同一条 tar 流：归档、清单里的原文与哈希永远出自同一次执行
            tar_cmd = ["tar", "-cf", "-", "-C", str(tar_root), *members]
            if lic_stage:
                tar_cmd += ["-C", str(lic_stage), "third-party-licenses"]
            tar = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE)
            comp = ["xz", f"-T{a.threads}", f"-{a.level}", "-c"] if tool == "xz" else ["gzip", f"-{a.level}", "-c"]
            z = subprocess.Popen(comp, stdin=tar.stdout, stdout=fh)
            tar.stdout.close()
            rc_z = z.wait(); rc_t = tar.wait()
        for temp in (cleanup, lic_stage):
            if temp:
                shutil.rmtree(temp, ignore_errors=True)
        if rc_t or rc_z:
            archive.unlink(missing_ok=True); sys.exit(f"{name}：打包失败 tar={rc_t} {tool}={rc_z}")
        entry = {"name": name, "version": version, "archive": archive.name, "sha256": sha256(archive),
                 "bytes": archive.stat().st_size, "unpack_to": str(parent.relative_to(root)) if parent != root else ".",
                 "product": spec["product"], "check": spec["check"], "also_required": spec.get("also_required", []),
                 "license": spec["license"], "source": spec["source"], "notice": spec.get("notice", []),
                 "license_members": license_members,
                 "pack_seconds": round(time.monotonic() - t0, 1)}
        manifest["runtimes"].append(entry)
        print(f"已打包 {name}：{archive.name}  {entry['bytes'] / 1e6:.0f} MB  {entry['pack_seconds']} s  {version}")
    manifest["skipped"] = skipped
    if a.only and (out / MANIFEST).exists():
        previous = json.loads((out / MANIFEST).read_text(encoding="utf8"))
        packed = {e["name"] for e in manifest["runtimes"]}
        manifest["runtimes"] = [e for e in previous.get("runtimes", []) if e["name"] not in packed] + manifest["runtimes"]
        manifest["skipped"] = [x for x in previous.get("skipped", []) if x["name"] not in packed and x["name"] not in {s["name"] for s in skipped}] + skipped
        print(f"并入已有清单：保留 {len(previous.get('runtimes', []))} 项里未重打的")
    (out / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    print(f"清单：{out / MANIFEST}（{len(manifest['runtimes'])} 项，跳过 {len(skipped)} 项）")
    return 0 if manifest["runtimes"] else 1


def fetch(src, name, dest, root):
    """src 是目录或 URL 前缀；把 name 取到 dest（已存在则复用，SHA 由调用方核）。明文 http 在 read_manifest 已被拒。"""
    if dest.exists():
        return
    if src.startswith(("https://",)):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(src.rstrip("/") + "/" + name, timeout=60) as r, atomic_output(root, dest) as fh:
            shutil.copyfileobj(r, fh, 1 << 20)
    else:
        with (Path(src) / name).open("rb") as r, atomic_output(root, dest) as fh:
            shutil.copyfileobj(r, fh, 1 << 20)


def read_manifest(src):
    """读发布清单：本地目录或 https；明文 http 一律拒绝。
    返回 (清单, 原始字节)——字节用于清单自身的 SHA256 认证。"""
    if src.startswith("http://"):
        sys.exit(f"错: 拒绝明文 http:// 来源，只接受 https:// 或本地目录：{src}")
    if src.startswith("https://"):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(src.rstrip("/") + "/" + MANIFEST, timeout=60) as r:
            raw = r.read()
    else:
        raw = (Path(src) / MANIFEST).read_bytes()
    return json.loads(raw.decode("utf8")), raw


def rel_violation(value, what):
    """从清单里取来的名字/路径必须是非空相对路径，不含 ..，不以 / 或 ~ 开头。"""
    if not isinstance(value, str) or not value or value.startswith(("/", "~")) or "\\" in value:
        return f"{what} 不是可用的相对路径：{value!r}"
    if any(part == ".." for part in PurePosixPath(value).parts):
        return f"{what} 含 ..：{value!r}"
    return None


def entry_violation(e, root):
    """先核 name：它会拼进许可证目录与搬移目标，绝对路径或 ../ 可能导致越界。
    再核 archive / product / unpack_to / check.path，
    且解析后必须仍在运行时根目录内。返回拒绝原因；None 表示可以继续取归档。"""
    name = e.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name in (".", ".."):
        return f"name 不是普通标识符：{name!r}"
    for field in ("archive", "product", "unpack_to"):
        why = rel_violation(e.get(field), field)
        if why:
            return why
    why = rel_violation(e.get("check", {}).get("path"), "check.path")
    if why:
        return why
    root_resolved = root.resolve()
    for field, value in (("product", e["product"]), ("unpack_to", e["unpack_to"]), ("check.path", e["check"]["path"])):
        target = (root / value).resolve()
        if target != root_resolved and root_resolved not in target.parents:
            return f"{field} 解析后不在运行时目录内：{value!r}"
    if e["unpack_to"] != ".":
        try:
            Path(e["product"]).relative_to(e["unpack_to"])
        except ValueError:
            return f"product 不在 unpack_to 之内：{e['product']!r} ↔ {e['unpack_to']!r}"
    return None


def load_manifest_hashes(path):
    """registry 里登记的已发布清单 SHA256 → 版本标注。"""
    data = json.loads(Path(path).read_text(encoding="utf8"))
    hashes = data.get("manifest_hashes")
    return hashes if isinstance(hashes, dict) else {}


def cache_violation(root, bundles, archive=None):
    """归档缓存目录与 .part 文件都不能躲在符号链接后面或越出运行时目录。
    archive 为 None 时只核目录；返回拒绝原因或 None。"""
    why = output_violation(root, bundles)
    if why:
        return f"归档缓存：{why}"
    if archive is None:
        return None
    for candidate in (archive, archive.with_suffix(archive.suffix + ".part"),
                      archive.with_suffix(archive.suffix + ".bad")):
        why = output_violation(root, candidate)
        if why:
            return f"归档缓存：{why}"
    return None


def installed_record(root):
    p = root / "installed-runtimes.json"
    require_output(root, p)
    if not p.exists():
        return {}
    parent = output_parent_fd(root, p)
    try:
        fd = os.open(p.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, encoding="utf8") as record:
        return json.load(record)


def move_output(root, source, dest, replace):
    """父目录逐级以 O_NOFOLLOW 打开；旧节点只改名保留，不沿链接搬移。"""
    parent = output_parent_fd(root, dest)
    try:
        require_output(root, dest)
        if dest.exists():
            if not replace:
                raise RuntimeError(f"目标已存在但没通过核对：{dest}（--replace 才换）")
            keep = dest.with_name(dest.name + ".replaced-" + str(time.time_ns()))
            require_output(root, keep)
            verify_parent_fd(root, dest, parent)
            os.rename(dest.name, keep.name, src_dir_fd=parent, dst_dir_fd=parent)
            print(f"  旧节点改名保留：{keep}")
        verify_parent_fd(root, dest, parent)
        os.rename(source, dest.name, dst_dir_fd=parent)
    finally:
        os.close(parent)


def cmd_check(a, reg):
    root = Path(a.runtimes).resolve()
    man, _ = read_manifest(a.src)
    names = set(selected(reg, a.only)) if a.only else None
    rec = installed_record(root)
    rc = 0
    for e in man["runtimes"]:
        if names and e["name"] not in names:
            continue
        why = check_ok(root, e)
        state = "已就绪" if not why else f"会安装（{why}）"
        if not why and rec.get(e["name"], {}).get("sha256") not in (None, e["sha256"]):
            state = "已就绪，但与清单不是同一份（--replace 才换）"
        print(f"{e['name']:12s} {state}；清单版本 {e['version']}，{e['bytes'] / 1e6:.0f} MB，{e['license'][:40]}")
    listed = {e["name"] for e in man["runtimes"]}
    for name in (names or set(reg)) - listed:
        print(f"{name:12s} 清单里没有；本机 {'已就绪' if not check_ok(root, reg[name]) else '也没有'}")
    return rc


def cmd_install(a, reg):
    root = Path(a.runtimes).resolve()
    root.mkdir(parents=True, exist_ok=True)
    man, raw = read_manifest(a.src)
    # 清单自身的 SHA256 必须在 registry 登记；未登记的清单仅允许本地目录加 --untrusted-local 显式信任。
    man_sha = hashlib.sha256(raw).hexdigest()
    if man_sha not in load_manifest_hashes(a.registry):
        local = not a.src.startswith("https://")
        if not (local and a.untrusted_local):
            sys.exit(f"清单未通过认证（SHA256 {man_sha}）——不在 {a.registry} 登记的发布清单里。"
                     + ("本地目录可用 --untrusted-local 显式信任后再装。" if local else "https 来源的清单必须先由项目登记哈希。"))
        print(f"警告：--untrusted-local 信任了未登记的本地清单（SHA256 {man_sha}）。")
    names = set(selected(reg, a.only)) if a.only else None
    rec = installed_record(root)
    bundles = root / "build/bundles"
    why = cache_violation(root, bundles)
    if why:
        sys.exit(f"错: {why}")
    # 许可证与产品的父目录也必须在首次缓存写入之前通过检查。
    for e in man["runtimes"]:
        if names and e["name"] not in names:
            continue
        if entry_violation(e, root) is None:
            for target in (root / e["product"], root / e["unpack_to"],
                           root / "third-party-licenses" / e["name"]):
                require_output(root, target)
    os.close(output_parent_fd(root, bundles / ".unused"))
    done, failed = [], []
    for e in man["runtimes"]:
        if names and e["name"] not in names:
            continue
        why = entry_violation(e, root)
        if why:
            failed.append((e["name"], f"清单路径被拒绝：{why}")); print(f"未装 {e['name']}：清单路径被拒绝：{why}"); continue
        why = cache_violation(root, bundles, bundles / e["archive"])
        if why:
            failed.append((e["name"], why)); print(f"未装 {e['name']}：{why}"); continue
        product = root / e["product"]
        if not check_ok(root, e) and not a.replace:
            print(f"跳过 {e['name']}：已就绪（{e['product']}）"); continue
        archive = bundles / e["archive"]
        try:
            fetch(a.src, e["archive"], archive, root)
        except Exception as error:
            failed.append((e["name"], f"取归档失败：{error}")); print(f"未装 {e['name']}：取归档失败：{error}"); continue
        got = sha256(archive)
        if got != e["sha256"]:
            bad = archive.with_suffix(archive.suffix + ".bad"); os.replace(archive, bad)
            failed.append((e["name"], "SHA256 不符")); print(f"未装 {e['name']}：SHA256 不符（清单 {e['sha256'][:12]}… 实得 {got[:12]}…），文件已改名 {bad.name}"); continue
        why = archive_violation(archive)
        if why:
            failed.append((e["name"], why)); print(f"未装 {e['name']}：{why}"); continue
        stage = Path(tempfile.mkdtemp(prefix=".bundle-stage.", dir=root))
        try:
            r = subprocess.run(["tar", "-xf", str(archive), "-C", str(stage)], capture_output=True, text=True, timeout=3600)
            if r.returncode:
                raise RuntimeError(f"解包失败：{r.stderr.strip()[:200]}")
            # 归档里的路径相对 unpack_to，所以把核对路径也换成相对 unpack_to 再用同一套 check_ok 核暂存目录
            why = check_ok(stage, relspec(e))
            if why:
                raise RuntimeError(f"解包后核对失败：{why}")
            rel_product = Path(e["product"]).relative_to(e["unpack_to"]) if e["unpack_to"] != "." else Path(e["product"])
            dest_parent = root / e["unpack_to"] if e["unpack_to"] != "." else root
            src_item = stage / rel_product.parts[0]
            dest_item = dest_parent / rel_product.parts[0]
            move_output(root, src_item, dest_item, a.replace)
            # 许可证原文随归档走：产品落位后搬到运行时根目录同名处；旧的原样改名保留，绝不删除
            lic_src = stage / "third-party-licenses" / e["name"]
            if lic_src.is_dir():
                lic_dest = root / "third-party-licenses" / e["name"]
                move_output(root, lic_src, lic_dest, True)
            else:
                print("  这个归档没带许可证原文（旧版发布包）")
            rec[e["name"]] = {"version": e["version"], "sha256": e["sha256"], "archive": e["archive"],
                              "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "product": e["product"]}
            with atomic_output(root, root / "installed-runtimes.json") as record:
                record.write((json.dumps(rec, ensure_ascii=False, indent=1) + "\n").encode("utf8"))
            done.append(e["name"]); print(f"已装 {e['name']} {e['version']} → {dest_item}")
        except Exception as error:
            failed.append((e["name"], str(error))); print(f"未装 {e['name']}：{error}")
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    print(f"完成：装了 {done}，失败 {failed}")
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", default=str(REGISTRY), help="运行时清单 JSON（测试用）")
    ap.add_argument("--runtimes", default=str(SN_RUNTIMES), help="SN_RUNTIMES（默认取 paths.py）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack"); p.add_argument("--out", required=True); p.add_argument("--only"); p.add_argument("--level", type=int, default=6); p.add_argument("--threads", type=int, default=0)
    c = sub.add_parser("check"); c.add_argument("--from", dest="src", required=True); c.add_argument("--only")
    i = sub.add_parser("install"); i.add_argument("--from", dest="src", required=True); i.add_argument("--only"); i.add_argument("--replace", action="store_true")
    i.add_argument("--untrusted-local", dest="untrusted_local", action="store_true",
                   help="本地目录的清单没登记 SHA256 时显式信任（https 来源永远需要登记）")
    a = ap.parse_args(argv)
    reg = load_registry(a.registry)
    return {"pack": cmd_pack, "check": cmd_check, "install": cmd_install}[a.cmd](a, reg)


if __name__ == "__main__":
    raise SystemExit(main())
