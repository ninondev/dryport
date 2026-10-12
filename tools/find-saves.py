#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""查一个正在运行的游戏，它的存档到底写在哪。

为什么要这个工具：2026-09-13 验 Gun-Dog 的存档，连着三轮在 `~/Library/RenPy/` 找不到，
判断成「没存上」。实际存档在 `~/Games/.renpy-state/2118420/saves/`，
是本项目的打包脚本自己重定向的位置，跟 Ren'Py 的系统默认约定无关。

**按引擎的默认约定猜存档位置是不可靠的**，因为我们自己的包装层会改它。
唯一可靠的办法是问正在跑的进程：它打开了哪些文件、写了哪些目录。

用法
  find-saves.py --pid 12345            按进程号查
  find-saves.py --name "Gun-Dog"       按进程名子串查
  find-saves.py --pid 12345 --watch 30 跑 30 秒，报告期间被改动的文件

--watch 是最有用的模式：先记一份基线，等一会儿再比对，
改动过的文件就是这个游戏真正在写的东西，不需要任何先验知识。
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

# 这些目录是系统或运行时的，不是游戏存档，报出来只会淹没真正的结果
NOISE = ('/System/', '/usr/', '/private/var/db/', '/dev/', '/Library/Fonts/',
         '/Library/Preferences/Logging/', '.dylib', '.framework/', '.bundle/',
         '/Library/Caches/com.apple.', '/.cache/', '/CoreServices/')

# 一眼能认出是存档的名字特征。既配路径也配文件名。
# 「state」在里面，是因为本项目的 Ren'Py 打包把存档根叫 .renpy-state，
# 只认 save 会给它 0 分（2026-09-13 实测踩到）。
# --watch 期间这些路径必然在变，与游戏无关，报出来只会淹没结果
WATCH_NOISE = ('/Claude', '/Code Cache', '/Cache/', '/Caches/', '/Brave', '/Chrome',
               '/Firefox', '/CrashReporter', '/Logs/', '.log', '/Cookies', '/IndexedDB/LOG',
               '/Local Storage/leveldb/LOG', '/.DS_Store')

SAVE_HINTS = ('save', 'persistent', '.sav', 'slot', 'profile', 'playerprefs',
              '.es3', 'state', 'userdata', 'appdata', 'prefs',
              # WebKit 路线的存档是浏览器 localStorage，落在 WebsiteDataStore 下的 sqlite。
              # 只认 save 一类的词会给它 0 分（2026-09-13 Koi Farm 实测踩到）。
              'storage', 'websitedata', 'sqlite')


def resolve_pid(args):
    if args.pid:
        return args.pid
    out = subprocess.run(['pgrep', '-f', args.name], capture_output=True, text=True)
    pids = [int(x) for x in out.stdout.split()]
    if not pids:
        raise SystemExit(f'找不到匹配 {args.name!r} 的进程')
    if len(pids) > 1:
        print(f'  匹配到 {len(pids)} 个进程 {pids}，用第一个；要指定请用 --pid', file=sys.stderr)
    return pids[0]


def cmdline(pid):
    out = subprocess.run(['ps', '-o', 'command=', '-p', str(pid)], capture_output=True, text=True)
    return out.stdout.strip()


# 只有这些容器下面的目录才算「数据地盘」。不加这道闸的话，进程的工作目录
# （比如某个桌面工具自己的数据目录）也会被当成地盘，`lsof +D` 一反查就把整台机器的进程都捞进来
# （2026-09-13 实测捞到 40 个，全是别的会话的 node 和 zsh）。
DATA_CONTAINERS = ('Library/Application Support', 'Library/Preferences', 'Library/WebKit',
                   'Library/Containers', 'Library/Saved Application State',
                   'Documents', 'Games')


def territory(pid):
    """目标进程在家目录数据容器下用到的目录。必须落在 DATA_CONTAINERS 之一下面，
    而且比容器本身至少深一层；app 包内部不算，那是游戏原件不是存档落点。"""
    home = Path.home()
    roots = set()
    for raw in _lsof_paths(pid):
        if any(n in raw for n in NOISE) or '.app/' in raw:
            continue
        try:
            rel = Path(raw).relative_to(home)
        except ValueError:
            continue
        for container in DATA_CONTAINERS:
            head = Path(container)
            if rel.parts[:len(head.parts)] != head.parts:
                continue
            rest = rel.parts[len(head.parts):]
            if not rest:
                break
            depth = min(3, len(rest))   # 容器下面取到三层：Koi Farm 的 appid 正好在第三层
            roots.add(str(home / head / Path(*rest[:depth])))
            break
    return sorted(roots)


# 太常见的目录名不能当特征词，否则一反查就把整台机器捞进来
GENERIC = {'library', 'games', 'documents', 'support', 'application support', 'preferences',
           'webkit', 'containers', 'data', 'saves', 'save', 'cache', 'caches', 'logs', 'com.apple'}


def fingerprints(pid):
    """从目标的地盘里挑出够特别的路径片段，用来认亲。

    Koi Farm 的例子：主进程开的是 `Application Support/dryport/webkit/1518810`，
    而存档在 `~/Library/WebKit/io.dryport.webkit.1518810/...`，两棵树同级但不同名，
    共有的只有 appid 这个词。所以按片段认，不按目录树认。
    """
    tokens = set()
    for root in territory(pid):
        for part in Path(root).parts:
            low = part.lower()
            if low in GENERIC or len(part) < 5:
                continue
            if part.isdigit() and len(part) >= 5:      # appid
                tokens.add(part)
            elif ('.' in part or '-' in part) and len(part) >= 8:   # 反向域名、带连字符的产品名
                tokens.add(low)
    cmd = subprocess.run(['ps', '-o', 'command=', '-p', str(pid)],
                         capture_output=True, text=True).stdout
    if '.app/' in cmd:                                  # app 包名本身
        stem = cmd[:cmd.index('.app/')].rsplit('/', 1)[-1]
        if len(stem) >= 5:
            tokens.add(stem.lower())
    # appid 这类纯数字词是**这一个游戏**专有的；`dryport` 这种是所有游戏共用的产品名，
    # 拿它认亲会把本工具自己的进程、别的游戏的 wineserver 全捞进来（实测多捞 15 个）。
    # 所以有数字词就只用数字词。
    digits = {x for x in tokens if x.isdigit()}
    return digits or tokens


def related_pids(pid):
    """目标进程加上所有可能替它写盘的进程。

    为什么不能只看目标自己：2026-09-13 验 Koi Farm 的存档，主进程 `WebKitGame` 的 lsof 里
    根本没有 localStorage 的 sqlite，真正持有它的是 `com.apple.WebKit.Networking.xpc`。

    为什么不能按进程树找：那三个 WebKit XPC 服务的父进程都是 launchd（ppid 1），
    命令行里也不带客户端 pid，子孙遍历和命令行匹配都连不上（实测都试过）。

    为什么不能按共用目录找：主进程与 XPC 用的是两棵同级但不同名的树
    （`Application Support/dryport/webkit/1518810` 对
    `Library/WebKit/io.dryport.webkit.1518810`），`lsof +D` 反查不到对方。

    可行的是按路径特征词认亲：两棵树共有 `1518810`。全量 `lsof -u` 一次约 7 秒，
    对一个诊断工具可以接受。只认落在 DATA_CONTAINERS 下面的匹配，
    否则 `dryport` 这种词会把本仓库目录下的一堆开发进程也捞进来。
    """
    found = {pid}
    frontier = [pid]
    while frontier:                                     # 子孙进程仍然要，只是不够
        cur = frontier.pop()
        out = subprocess.run(['pgrep', '-P', str(cur)], capture_output=True, text=True)
        for child in (int(x) for x in out.stdout.split()):
            if child not in found:
                found.add(child)
                frontier.append(child)
    tokens = fingerprints(pid)
    if tokens:
        home = str(Path.home())
        containers = tuple(f'{home}/{c}/' for c in DATA_CONTAINERS)
        out = subprocess.run(['lsof', '-u', os.environ.get('USER', '')],
                             capture_output=True, text=True, timeout=180)
        for line in out.stdout.splitlines()[1:]:
            parts = line.split(None, 8)
            if len(parts) < 9:
                continue
            path = parts[8].strip()
            if not path.startswith(containers):
                continue
            low = path.lower()
            if any(tok in low for tok in tokens):
                try:
                    found.add(int(parts[1]))
                except ValueError:
                    pass
    found.discard(os.getpid())
    return sorted(found)


def _lsof_paths(pid):
    """一个进程此刻打开的绝对路径，不过滤。"""
    out = subprocess.run(['lsof', '-p', str(pid)], capture_output=True, text=True)
    paths = set()
    for line in out.stdout.splitlines()[1:]:
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue
        p = parts[8].strip()
        if p.startswith('/'):
            paths.add(p)
    return paths


def open_files(pids):
    """一组进程当前打开的文件。只反映此刻，短暂写完就关的文件抓不到，所以还要 --watch。"""
    paths = set()
    for pid in pids:
        for p in _lsof_paths(pid):
            if not any(n in p for n in NOISE):
                paths.add(p)
    return paths


def interesting_roots(paths):
    """把一堆文件路径收敛成几个值得看的目录，按「像不像存档」排序。

    候选根必须落在 DATA_CONTAINERS 之一下面并且比容器本身深：
    不加这道闸的话 `~/Library` 这种通用容器会因为文件数多而排到真答案前面
    （2026-09-13 Koi Farm 实测：`~/Library` 12 个文件排第一，
    真正的 `~/Library/WebKit/io.dryport.webkit.1518810` 9 个文件排第二）。
    """
    home = Path.home()
    roots = {}
    for p in paths:
        try:
            rel = Path(p).relative_to(home)
        except ValueError:
            continue
        for container in DATA_CONTAINERS:
            head = Path(container)
            if rel.parts[:len(head.parts)] != head.parts:
                continue
            rest = rel.parts[len(head.parts):]
            if not rest:
                break
            for depth in (3, 2, 1):
                if len(rest) >= depth:
                    root = str(home / head / Path(*rest[:depth]))
                    roots.setdefault(root, set()).add(p)
            break
    scored = []
    for root, files in roots.items():
        low = root.lower()
        score = sum(3 for h in SAVE_HINTS if h in low)
        score += sum(1 for f in files if any(h in Path(f).name.lower() for h in SAVE_HINTS))
        # 目录里真有 saves/ 子目录，比名字像更有说服力
        try:
            for sub in Path(root).iterdir():
                if sub.is_dir() and sub.name.lower() in ('saves', 'savegames', 'savedata', 'save'):
                    score += 8
        except OSError:
            pass
        # app 包内部与游戏本体目录不是存档落点，压低它们
        if '.app/contents' in low or low.endswith('.app'):
            score -= 5
        scored.append((score, len(files), root))
    scored.sort(reverse=True)
    return scored


def snapshot(roots):
    """给一批目录记一份文件状态，用于比对改动。"""
    state = {}
    for root in roots:
        r = Path(root)
        if not r.is_dir():
            continue
        for p in r.rglob('*'):
            try:
                if p.is_file():
                    st = p.stat()
                    state[str(p)] = (st.st_size, st.st_mtime)
            except OSError:
                pass
    return state


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--pid', type=int)
    g.add_argument('--name', help='进程名子串')
    ap.add_argument('--watch', type=int, metavar='秒',
                    help='观察这么久，报告期间新增或改动的文件')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    a = ap.parse_args()

    pid = resolve_pid(a)
    proc = subprocess.run(['ps', '-o', 'comm=', '-p', str(pid)], capture_output=True, text=True)
    print(f'  进程 {pid}  {proc.stdout.strip()[:90]}')
    pids = related_pids(pid)
    if len(pids) > 1:
        print(f'  连同 {len(pids) - 1} 个相关进程一起查（子孙进程，以及路径特征词对上的进程）：')
        for other in pids:
            if other != pid:
                name = subprocess.run(['ps', '-o', 'comm=', '-p', str(other)],
                                      capture_output=True, text=True).stdout.strip()
                print(f'    {other:>7}  {name.rsplit("/", 1)[-1][:70]}')

    paths = open_files(pids)
    scored = interesting_roots(paths)
    print(f'\n  当前打开的用户目录文件收敛出 {len(scored)} 个候选根（按像存档的程度排序）：')
    for score, n, root in scored[:8]:
        mark = ' ←' if score > 0 else ''
        print(f'    [{score:>2}分 {n:>3}个文件] {root.replace(str(Path.home()), "~")}{mark}')

    result = {'pid': pid, 'candidates': [{'score': s, 'files': n, 'root': r} for s, n, r in scored[:8]]}

    if a.watch:
        watch_roots = [r for _, _, r in scored[:6]]
        # 家目录下的常见落点也一并盯着：进程此刻没打开不代表待会儿不写。
        # 这里**不能**整个盯 `Library/Application Support`：2026-09-13 实测那样会被浏览器
        # 与别的 app 的缓存写入淹没，游戏那一条要 grep 才找得到。只盯已知的存档根，
        # 外加 Application Support 下与本游戏相关的子目录。
        home = Path.home()
        for extra in ('Library/Preferences', 'Library/RenPy', 'Library/WebKit',
                      'Documents/My Games', 'Games/.renpy-state', 'Games/.wine-state'):
            p = home / extra
            if p.is_dir():
                watch_roots.append(str(p))
        support = home / 'Library' / 'Application Support'
        if support.is_dir():
            # 从已打开的文件里认出本游戏用的是哪几个子目录，只盯这几个
            prefix = str(support) + '/'
            for opened in paths:
                if opened.startswith(prefix):
                    watch_roots.append(prefix + opened[len(prefix):].split('/', 1)[0])
        watch_roots = sorted(set(watch_roots))
        print(f'\n  观察 {a.watch} 秒，盯 {len(watch_roots)} 个目录…')
        before = snapshot(watch_roots)
        time.sleep(a.watch)
        after = snapshot(watch_roots)
        changed = []
        for path, meta in after.items():
            if path not in before:
                changed.append(('新增', path, meta[0]))
            elif before[path] != meta:
                changed.append(('改动', path, meta[0]))
        changed = [c for c in changed if not any(n in c[1] for n in WATCH_NOISE)]
        changed.sort(key=lambda x: x[1])
        if changed:
            print(f'  期间有 {len(changed)} 个文件变化：')
            for kind, path, size in changed[:25]:
                print(f'    {kind}  {size:>9,} bytes  {path.replace(str(home), "~")}')
        else:
            print('  期间没有文件变化。游戏可能停在不写盘的画面，'
                  '或者存档落在盯的目录之外（把候选根用 --pid 重查一次）。')
        result['changed'] = [{'kind': k, 'path': p, 'bytes': s} for k, p, s in changed]

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
