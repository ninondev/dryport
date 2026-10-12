#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""受控真机试跑：给一个 Windows 游戏搭沙箱 harness，窗口化限时跑一轮，收证据。

这是把 2026-09-12 到 09-13 两轮手抄 harness（A Space for the Unbound、entry-05）
收敛成的工具。手抄版每个游戏一份，改路径容易漏；漏掉的那几处都不会报错，只会让证据失真。

它做什么
  prepare  建 prefix、装 DXMT 与 steambridge、按本游戏的路径数生成 policy.sb、
           生成 source-inventory / existing-saves / runtime-inventory / launch-materials，
           每一份都带 SHA256 绑定。
  run      跑之前逐项核对绑定，跑完收 evidence.json、helper.log、wine.log、窗口截图。

它不做什么
  不下载游戏，不改游戏原件，不碰 Steam 客户端，不放宽沙箱边界。
  退出码 0 只表示「受控实验按计划完成」，**不表示游戏可玩**：画面、操作、存档要人工验收。

用法
  wine-trial.py prepare --appid 42 --game ~/Games/fixture-game \\
      --exe FixtureGame/Binaries/Win64/Fixture-Win64-Shipping.exe --engine unreal
  wine-trial.py run --harness ~/Games/runtimes/sn-trial-xxxx --seconds 90

为什么参数里要引擎类型：窗口化参数各家不同，给错了游戏会全屏铺满桌面。
2026-09-11 有过一次独占全屏把桌面盖死、鼠标也没了的事故，见 the runtime notes。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib'))
from paths import SN_RUNTIMES, SN_GAMES          # noqa: E402

WINE = SN_RUNTIMES / 'wine-cx/bin/wine'
WINESERVER = SN_RUNTIMES / 'wine-cx/bin/wineserver'
BRIDGE_DIR = SN_RUNTIMES / 'steambridge'
DXMT_DLLS = ('d3d11.dll', 'dxgi.dll', 'd3d10core.dll', 'winemetal.dll')

# 窗口化参数按引擎分。unity 与 unreal 的写法互不通用：给 UE 传 -screen-width 它会忽略并全屏。
WINDOWED = {
    'unity':  ['-screen-fullscreen', '0', '-screen-width', '1280', '-screen-height', '720'],
    'unreal': ['-windowed', '-ResX=1280', '-ResY=720'],
    'other':  [],          # 交给 Wine 虚拟桌面兜底，仍然不会全屏
}

# 各引擎常见的用户数据落点。列进来只为「运行前登记、运行后核对没被动过」，不是给游戏用的。
SAVE_HINTS = (
    'Library/Application Support/{name}',
    'Library/Preferences/com.{name}.plist',
    'Documents/My Games/{name}',
)


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def build_source_inventory(game):
    """字段名必须是 bytes：run 阶段按这个键读。2026-09-13 用 size 写过一次，
    表现是 KeyError('bytes') 并安全中止，helper 与游戏都没起 —— 中止是对的，但错因难认。"""
    files = []
    for path in sorted(game.rglob('*')):
        if path.is_symlink():
            raise SystemExit(f'原游戏里有软链，写保护前提不成立：{path}')
        if not path.is_file():
            continue
        st = path.stat()
        if st.st_nlink != 1:
            raise SystemExit(f'原游戏里有硬链接，当前保护不覆盖此条件：{path}')
        files.append({'path': str(path.relative_to(game)), 'bytes': st.st_size, 'sha256': sha(path)})
    return {'root': str(game), 'files': files, 'count': len(files),
            'total_bytes': sum(f['bytes'] for f in files)}


def build_saves_inventory(game, hints):
    """protected[0] 固定是游戏目录；run 阶段从 [1:] 开始遍历存档路径，顺序不能变。"""
    home = Path.home()
    protected = [str(game)] + [str(home / h) for h in hints]
    files = []
    for root in map(Path, protected[1:]):
        for path in (sorted(root.rglob('*')) if root.is_dir() else [root]):
            if path.is_symlink():
                raise SystemExit(f'既有存档里有软链：{path}')
            if path.exists() and path.is_file():
                if path.stat().st_nlink != 1:
                    raise SystemExit(f'既有存档里有硬链接：{path}')
                files.append({'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path)})
    return {'protected': protected, 'files': files}


def build_policy(protected, ancestors):
    """PROTECTED_n / ANCESTOR_n 的个数按本游戏的路径数展开。
    手抄版把个数写死（Unbound 是 6 与 7），换游戏后数量对不上，sandbox-exec 会因未定义参数拒绝执行。"""
    return (
        '(version 1)\n(allow default)\n(deny file-write*)\n(deny file-link)\n'
        '(allow file-write* (require-all (require-any '
        '(subpath (param "STATE")) (subpath (param "TEMP")) (subpath (param "WINE_SERVER")) '
        '(literal "/dev/null")) '
        + ' '.join(f'(require-not (subpath (param "PROTECTED_{i}")))' for i in range(len(protected)))
        + '))\n'
        '(allow file-write-create (literal (param "WINE_SERVER_PARENT")))\n'
        '(deny file-write-unlink '
        + ' '.join(f'(literal (param "ANCESTOR_{i}"))' for i in range(len(ancestors)))
        + ')\n'
    )


def ancestors_of(protected):
    out, seen = [], set()
    for p in protected:
        cur = Path(p).parent
        while True:
            s = str(cur)
            if s not in seen:
                seen.add(s)
                out.append(s)
            if cur == cur.parent:
                break
            cur = cur.parent
    return out


def build_sandbox_command(base, protected, ancestors, policy):
    st = (base / 'state/prefix').stat()
    real_tmp = os.path.realpath(tempfile.gettempdir())
    uid = os.getuid()
    cmd = ['/usr/bin/sandbox-exec']

    def define(key, value):
        cmd.extend(['-D', f'{key}={value}'])

    define('STATE', str(base / 'state'))
    define('TEMP', real_tmp)
    define('WINE_SERVER', f'/private/tmp/.wine-{uid}/server-{st.st_dev:x}-{st.st_ino:x}')
    define('WINE_SERVER_PARENT', f'/private/tmp/.wine-{uid}')
    for i, p in enumerate(protected):
        define(f'PROTECTED_{i}', p)
    for i, a in enumerate(ancestors):
        define(f'ANCESTOR_{i}', a)
    cmd.extend(['-p', policy])          # 必须与 policy.sb 逐字一致，run 阶段会核对
    return cmd


def probe_sandbox(cmd, game, base):
    """开跑前实测策略真的拦得住：不验证就等于没有保护。"""
    def write_to(path):
        r = subprocess.run(cmd + ['/bin/sh', '-c', f'echo probe > {json.dumps(path)} 2>&1'],
                           capture_output=True, text=True)
        return r.returncode != 0
    results = {
        'blocks_game_dir': write_to(str(game / '.wine-trial-probe')),
        'allows_state': not write_to(str(base / 'state/tmp/.wine-trial-probe')),
    }
    stray = game / '.wine-trial-probe'
    if stray.exists():
        stray.unlink()
        results['blocks_game_dir'] = False
    ok_probe = base / 'state/tmp/.wine-trial-probe'
    if ok_probe.exists():
        ok_probe.unlink()
    return results


def run_wine(base, args, log_name, timeout):
    env = dict(os.environ, WINEPREFIX=str(base / 'state/prefix'), HOME=str(base / 'state/home'),
               TMPDIR=str(base / 'state/tmp'), WINEARCH='win64', WINEDEBUG='-all')
    with (base / log_name).open('wb') as out:
        return subprocess.run([str(WINE)] + args, env=env, stdout=out,
                              stderr=subprocess.STDOUT, timeout=timeout)


def cmd_prepare(a):
    game = Path(a.game).expanduser().resolve()
    if not game.is_dir():
        raise SystemExit(f'游戏目录不存在：{game}')
    exe = game / a.exe
    if not exe.is_file():
        raise SystemExit(f'可执行文件不存在：{exe}')

    gate = subprocess.run([sys.executable, '-B', str(ROOT / 'lib/risk.py'), '--gate-json', a.appid],
                          capture_output=True, text=True)
    verdict = json.loads(gate.stdout) if gate.stdout.strip() else {}
    if gate.returncode != 0:
        raise SystemExit(f'风险闸未放行（退出码 {gate.returncode}）：{verdict.get("verdict", gate.stderr.strip())}')
    print(f'  风险闸 {a.appid} {verdict.get("name", "")}：{verdict.get("verdict", "")}')

    base = Path(a.harness).expanduser() if a.harness else SN_RUNTIMES / f'sn-trial-{uuid.uuid4().hex[:8]}'
    if base.exists():
        raise SystemExit(f'目标已存在，换一个：{base}')
    for sub in ('overlay/x86_64-windows', 'state/home', 'state/tmp', 'state/prefix',
                'state/logs', 'state/dxmt-cache', 'runs'):
        (base / sub).mkdir(parents=True)
    print(f'  harness {base}')

    print('  初始化 prefix …')
    r = run_wine(base, ['wineboot', '--init'], 'initialize.log', 600)
    if r.returncode != 0:
        raise SystemExit(f'prefix 初始化失败，见 {base}/initialize.log')
    for key, value in (('Desktop', 'Default'),):
        run_wine(base, ['reg', 'add', r'HKCU\Software\Wine\Explorer', '/v', key,
                        '/t', 'REG_SZ', '/d', value, '/f'], 'desktop-enable.log', 120)
    run_wine(base, ['reg', 'add', r'HKCU\Software\Wine\Explorer\Desktops', '/v', 'Default',
                    '/t', 'REG_SZ', '/d', '1280x720', '/f'], 'desktop-size.log', 120)
    subprocess.run([str(WINESERVER), '-k'],
                   env=dict(os.environ, WINEPREFIX=str(base / 'state/prefix')),
                   capture_output=True, timeout=60)

    system32 = base / 'state/prefix/drive_c/windows/system32'
    dxmt_src = Path(a.dxmt_from).expanduser() if a.dxmt_from else None
    for name in DXMT_DLLS:
        src = (dxmt_src / name) if dxmt_src else (SN_RUNTIMES / 'dxmt/v0.80/x86_64-windows' / name)
        if not src.is_file():
            raise SystemExit(f'找不到 DXMT 组件 {name}（{src}）。用 --dxmt-from 指一个已验证 prefix 的 system32')
        shutil.copy2(src, system32 / name)
    bridge = BRIDGE_DIR / 'steam_api64.dll'
    helper = BRIDGE_DIR / 'steambridge-helper'
    for f in (bridge, helper):
        if not f.is_file():
            raise SystemExit(f'缺 steambridge 产物：{f}（先跑 lib/steambridge/build.sh）')
    marker = open(bridge, 'rb').read(0x50)[0x40:0x50]
    if marker != b'Wine builtin DLL':
        raise SystemExit('桥缺 0x40 处的 Wine builtin 标记，外置加载会整条断掉，先重建')
    shutil.copy2(bridge, base / 'overlay/x86_64-windows/steam_api64.dll')
    shutil.copy2(bridge, system32 / 'steam_api64.dll')
    shutil.copy2(helper, base / 'steambridge-helper')
    print(f'  桥 {sha(bridge)[:16]}  helper {sha(helper)[:16]}  builtin 标记已核')

    b = str(base)
    env = {
        'HOME': b + '/state/home', 'TMPDIR': b + '/state/tmp',
        'WINEPREFIX': b + '/state/prefix', 'WINEDEBUG': '-all', 'WINEARCH': 'win64',
        'WINEDLLPATH': b + '/overlay',
        'WINEDLLOVERRIDES': 'steam_api64=b;mscoree,mshtml,winemenubuilder.exe=d',
        'XDG_CACHE_HOME': b + '/state/home/.cache',
        # DXMT 的着色器缓存默认落在系统临时目录，不在沙箱允许范围内，游戏会在建渲染管线时失败。
        # 引到 STATE 内即可，不必放宽 policy.sb。2026-09-13 实测。
        'DXMT_SHADER_CACHE_PATH': b + '/state/dxmt-cache',
    }
    (base / 'wine-environment.json').write_text(json.dumps(env, indent=2, sort_keys=True))

    hints = [h.format(name=a.save_name or game.name) for h in SAVE_HINTS] + list(a.also_protect)
    source_inv = build_source_inventory(game)
    saves_inv = build_saves_inventory(game, hints)
    (base / 'source-inventory.json').write_text(json.dumps(source_inv, indent=1))
    (base / 'existing-saves-inventory.json').write_text(json.dumps(saves_inv, indent=1))
    print(f'  原件 {source_inv["count"]} 个 {source_inv["total_bytes"]:,} 字节；'
          f'既有存档 {len(saves_inv["files"])} 个，受保护 {len(saves_inv["protected"])} 处')
    if not a.also_protect:
        print('  提示：受保护路径是按目录名套模板生成的。真实的 bundle id 与发行商命名猜不到，'
              '人工验收或要保存档的轮次，用 --also-protect 补齐。')

    images = []
    for p in [WINE, WINESERVER] + [system32 / n for n in DXMT_DLLS]:
        if Path(p).is_file():
            images.append({'path': str(p), 'sha256': sha(p), 'size': Path(p).stat().st_size})
    (base / 'runtime-inventory.json').write_text(json.dumps({'images': images}, indent=1))

    protected = saves_inv['protected']
    ancestors = ancestors_of(protected)
    policy = build_policy(protected, ancestors)
    (base / 'policy.sb').write_text(policy)
    sandbox = build_sandbox_command(base, protected, ancestors, policy)
    (base / 'sandbox-command.json').write_text(json.dumps(sandbox, indent=2))
    probes = probe_sandbox(sandbox, game, base)
    if not (probes['blocks_game_dir'] and probes['allows_state']):
        raise SystemExit(f'沙箱策略实测不符合预期，不能开跑：{probes}')
    print(f'  沙箱实测：拦住写游戏目录、放行写 STATE（受保护 {len(protected)} 处、祖先 {len(ancestors)} 处）')

    plan = {
        'appid': a.appid, 'game': str(game), 'exe': a.exe, 'engine': a.engine,
        'windowed_args': WINDOWED[a.engine],
        'prepared_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'prefix_identity': [(base / 'state/prefix').stat().st_dev, (base / 'state/prefix').stat().st_ino],
        'sandbox_probes': probes,
    }
    (base / 'trial-plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))

    names = ['steambridge-helper', 'overlay/x86_64-windows/steam_api64.dll', 'sandbox-command.json',
             'wine-environment.json', 'source-inventory.json', 'existing-saves-inventory.json',
             'runtime-inventory.json', 'policy.sb', 'trial-plan.json'] + \
            [f'state/prefix/drive_c/windows/system32/{n}' for n in DXMT_DLLS]
    (base / 'launch-materials.json').write_text(json.dumps(
        {'sha256': {n: sha(base / n) for n in names}, 'prefix_identity': plan['prefix_identity'],
         'rebinding_log': []}, ensure_ascii=False, indent=2))
    print(f'  绑定 {len(names)} 个文件\n\n  跑一轮：{Path(__file__).name} run --harness {base} --seconds 90')
    return 0


def machine_state():
    """跑前跑后各记一次磁盘与 swap。内存吃紧时 Rosetta 首次翻译是分钟级，
    画面会显得很卡；不记下来事后就分不清那是渲染问题还是内存问题
    （2026-09-11 把「很慢」误判成「oahd 卡死」就是因为没有这个基线）。"""
    state = {}
    try:
        df = subprocess.run(['df', '-k', '/'], capture_output=True, text=True, timeout=10)
        free_kb = int(df.stdout.strip().split('\n')[-1].split()[3])
        state['disk_free_gib'] = round(free_kb / 1024 / 1024, 1)
    except Exception:
        pass
    try:
        sw = subprocess.run(['sysctl', '-n', 'vm.swapusage'], capture_output=True, text=True, timeout=10)
        m = re.search(r'total = ([\d.]+)M.*?used = ([\d.]+)M.*?free = ([\d.]+)M', sw.stdout)
        if m:
            state['swap_total_mb'], state['swap_used_mb'], state['swap_free_mb'] = \
                (float(m.group(i)) for i in (1, 2, 3))
    except Exception:
        pass
    return state


WINDOW_LIST = SN_RUNTIMES / 'bin/window-list'


def ensure_window_list():
    """按 owner 名列窗口的小工具。源码在 tools/window-list.swift，产物放 SN_RUNTIMES，不进仓库。
    系统 python3 没有 Quartz 模块，osascript 又看不到别的应用的窗口，所以要它。"""
    src = ROOT / 'tools/window-list.swift'
    if WINDOW_LIST.is_file() and WINDOW_LIST.stat().st_mtime >= src.stat().st_mtime:
        return True
    WINDOW_LIST.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(['swiftc', '-O', str(src), '-o', str(WINDOW_LIST)],
                       capture_output=True, text=True, timeout=300)
    return r.returncode == 0 and WINDOW_LIST.is_file()


def snap_windows(owner, out_dir, tag):
    """按窗口 id 截图。全屏截图会被前景窗口盖住，还会把别的窗口内容带进证据，所以一律按 id 截。

    找不到窗口不算失败：Wine 游戏的 owner 名常常不是游戏名（mkxp-z 的窗口 owner 就叫 mkxp-z），
    而且有些引擎起窗口慢。如实记下 tried 与结果，让读证据的人知道是没找到而不是没截。
    """
    info = {'owner_query': owner, 'windows': [], 'shots': [], 'tool_ok': False}
    if not ensure_window_list():
        info['error'] = 'window-list 编译失败，本次运行无画面证据'
        return info
    info['tool_ok'] = True

    def listing(needle):
        out = subprocess.run([str(WINDOW_LIST), needle], capture_output=True, text=True, timeout=20)
        return json.loads(out.stdout or '[]')

    try:
        # Wine 跑的游戏，窗口 owner 一律是 "wine"，不是游戏名（2026-09-13 实测）；
        # 原生包装的游戏才用得上游戏名，而 mkxp-z 那类又叫运行时的名字。
        # 所以按游戏名找不到就回退到 wine，再找不到就列全部让人自己看。
        info['windows'] = listing(owner)
        if not info['windows']:
            info['fallback'] = 'wine'
            info['windows'] = listing('wine')
        if not info['windows']:
            info['fallback'] = 'all'
            info['all_windows'] = [w['owner'] for w in listing('')]
    except Exception as error:
        info['error'] = f'列窗口失败：{error}'
        return info
    for w in info['windows'][:2]:
        shot = out_dir / f'{tag}-win{w["id"]}.png'
        subprocess.run(['screencapture', '-x', '-o', '-l', str(w['id']), str(shot)],
                       capture_output=True, timeout=20)
        if shot.exists() and shot.stat().st_size > 0:
            info['shots'].append({'id': w['id'], 'owner': w.get('owner'),
                                  'bounds': w.get('bounds'), 'file': shot.name})
    return info


def terminate(process):
    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)


def check_originals(base):
    inv = json.loads((base / 'source-inventory.json').read_text())
    game = Path(inv['root'])
    seen = {}
    for path in game.rglob('*'):
        if path.is_symlink():
            raise RuntimeError('原游戏出现软链，停止')
        if path.is_file():
            if path.stat().st_nlink != 1:
                raise RuntimeError('原游戏出现硬链接，当前保护不覆盖此条件')
            seen[str(path.relative_to(game))] = (path.stat().st_size, sha(path))
    if seen != {r['path']: (r['bytes'], r['sha256']) for r in inv['files']}:
        raise RuntimeError('原游戏文件集合或字节发生变化，停止')
    saved = json.loads((base / 'existing-saves-inventory.json').read_text())
    found = {}
    for root in map(Path, saved['protected'][1:]):
        for path in (sorted(root.rglob('*')) if root.is_dir() else [root]):
            if path.is_symlink():
                raise RuntimeError('既有存档出现软链，停止')
            if path.exists() and path.is_file():
                found[str(path)] = (path.stat().st_size, sha(path))
    if found != {r['path']: (r['bytes'], r['sha256']) for r in saved['files']}:
        raise RuntimeError('既有存档集合或字节发生变化')


def checked_json(command, run, name):
    result = subprocess.run(command, capture_output=True, text=True, timeout=45)
    (run / (name + '.json')).write_text(result.stdout)
    (run / (name + '.stderr')).write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(name + ' 未通过；保留原始证据')
    return json.loads(result.stdout)


def cmd_run(a):
    base = Path(a.harness).expanduser().resolve()
    plan = json.loads((base / 'trial-plan.json').read_text())
    appid, game = plan['appid'], Path(plan['game'])
    # 上限 600 秒：目视验收要看主菜单之后的表现，180 秒不够走完一段流程。
    # 仍然设上限，是因为受控实验必须有确定的结束时间，不能变成放任它跑。
    if not 15 <= a.seconds <= 600:
        raise SystemExit('实验时间须在 15 到 600 秒内')
    run = base / 'runs' / f'{time.time_ns()}-{os.getpid()}'
    run.mkdir(parents=True)
    os.chmod(run, 0o700)

    report = {'scope': 'controlled_real_game_probe', 'playability': 'manual_verification_required',
              'appid': appid, 'engine': plan['engine'], 'seconds_requested': a.seconds,
              'helper_started': False, 'game_started': False, 'bounded_stop': False,
              'source_and_existing_saves_preserved': False, 'cleanup_complete': False,
              'resources_before': machine_state()}
    if a.owner_verdict:
        report['owner_verdict'] = a.owner_verdict          # 原话，不做解释
    helper = proc = None
    before = None                       # 先绑定：异常路径也要能安全引用（手抄版这里会抛 UnboundLocalError，
                                        # 掩盖真正的错误信息，2026-09-13 在两个游戏上都复现过）
    wine_env = dict(os.environ, **json.loads((base / 'wine-environment.json').read_text()))
    sandbox = json.loads((base / 'sandbox-command.json').read_text())
    try:
        check_originals(base)
        for row in json.loads((base / 'runtime-inventory.json').read_text())['images']:
            if sha(Path(row['path'])) != row['sha256']:
                raise RuntimeError('Wine/DXMT 运行时变化，须重新 prepare')
        ready = json.loads((base / 'launch-materials.json').read_text())
        for name, digest in ready['sha256'].items():
            if sha(base / name) != digest:
                raise RuntimeError('预备文件变化：' + name)
        if sandbox[-2:] != ['-p', (base / 'policy.sb').read_text()]:
            raise RuntimeError('生效 sandbox 策略与审阅文本不一致')
        st = (base / 'state/prefix').stat()
        if [st.st_dev, st.st_ino] != ready['prefix_identity']:
            raise RuntimeError('prefix 身份变化，不能复用服务目录权限')
        gate = checked_json([sys.executable, '-B', str(ROOT / 'lib/risk.py'), '--gate-json', appid],
                            run, 'risk-before')
        if gate.get('code') != 0 or str(gate.get('appid')) != appid:
            raise RuntimeError('风险闸未明确放行')
        before = checked_json([sys.executable, '-B', str(ROOT / 'lib/library_discovery.py'),
                               '--license', appid], run, 'license-before')
        if not before.get('allowed') or str(before.get('appid')) != appid:
            raise RuntimeError('当前账号许可未明确放行')
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            port = reserved.getsockname()[1]
        # helper 只服务带同一令牌的连接；令牌同时进 helper 与 Wine 进程环境。
        token = secrets.token_hex(32)
        with (run / 'helper.log').open('wb') as out:
            helper = subprocess.Popen([str(base / 'steambridge-helper'), appid, str(port)],
                                      cwd=run, env=dict(os.environ, STEAMBRIDGE_TOKEN=token), stdout=out,
                                      stderr=subprocess.STDOUT, start_new_session=True)
        report['helper_started'] = True
        until = time.monotonic() + 10
        while time.monotonic() < until:
            if helper.poll() is not None:
                raise RuntimeError('helper 已退出')
            if 'listening on 127.0.0.1:' in (run / 'helper.log').read_text(errors='replace'):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError('helper 就绪超时')
        wine_env.update(SteamAppId=appid, SteamGameId=appid, STEAMBRIDGE_PORT=str(port), STEAMBRIDGE_TOKEN=token,
                        WINEDEBUG='-all,+loaddll,+seh')
        command = sandbox + [str(WINE), str(game / plan['exe'])] + plan['windowed_args']
        (run / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
        with (run / 'wine.log').open('wb') as out:
            proc = subprocess.Popen(command, cwd=game, env=wine_env, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        report['game_started'] = True
        report['game_host_pid'] = proc.pid
        print(str(run), flush=True)
        # 画面证据：起来后与临停前各一次，按窗口 id 截，不靠全屏截图。
        time.sleep(min(20, a.seconds // 3))
        report['snapshot_early'] = snap_windows(a.window_owner or game.name, run, 'early')
        try:
            report['game_exit'] = proc.wait(timeout=max(1, a.seconds - min(20, a.seconds // 3) - 5))
        except subprocess.TimeoutExpired:
            report['snapshot_late'] = snap_windows(a.window_owner or game.name, run, 'late')
            try:
                report['game_exit'] = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                report['bounded_stop'] = True
    except Exception as error:
        report['error'] = str(error)
    finally:
        try:
            if proc is not None:
                with (run / 'cleanup.log').open('wb') as out:
                    killed = subprocess.run([str(WINESERVER), '-k'], env=wine_env, stdout=out,
                                            stderr=subprocess.STDOUT, timeout=15)
                    terminate(proc)
                    waited = subprocess.run([str(WINESERVER), '-w'], env=wine_env, stdout=out,
                                            stderr=subprocess.STDOUT, timeout=15)
                report['cleanup_complete'] = killed.returncode == waited.returncode == 0
            else:
                report['cleanup_complete'] = True
            terminate(helper)
        except Exception as error:
            terminate(proc)
            terminate(helper)
            report['cleanup_error'] = str(error)
        try:
            check_originals(base)
            report['source_and_existing_saves_preserved'] = True
        except Exception as error:
            report['preservation_error'] = str(error)
        if before is not None:          # 前置就失败时 before 为 None，不再抛 UnboundLocalError 掩盖真因
            try:
                after = checked_json([sys.executable, '-B', str(ROOT / 'lib/library_discovery.py'),
                                      '--license', appid], run, 'license-after')
                report['account_unchanged'] = after.get('account') == before.get('account')
            except Exception as error:
                report['license_after_error'] = str(error)
        else:
            report['account_check_skipped'] = '前置检查未通过，未取许可基线'
        report['resources_after'] = machine_state()
        (run / 'evidence.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        for line in ('  ' + k + ': ' + str(v) for k, v in report.items()
                     if k in ('helper_started', 'game_started', 'bounded_stop', 'game_exit',
                              'source_and_existing_saves_preserved', 'cleanup_complete',
                              'account_unchanged', 'error')):
            print(line)
    return 0 if (report['game_started'] and report['cleanup_complete']
                 and report['source_and_existing_saves_preserved'] and not report.get('error')
                 and (report['bounded_stop'] or report.get('game_exit') == 0)) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare', help='搭 harness')
    p.add_argument('--appid', required=True)
    p.add_argument('--game', required=True, help='游戏目录')
    p.add_argument('--exe', required=True, help='可执行文件相对游戏目录的路径')
    p.add_argument('--engine', required=True, choices=sorted(WINDOWED))
    p.add_argument('--harness', help='harness 目录，默认在 SN_RUNTIMES 下新建')
    p.add_argument('--save-name', help='存档目录名，默认用游戏目录名')
    p.add_argument('--also-protect', action='append', default=[], metavar='相对HOME的路径',
                   help='额外纳入保护的存档路径，可重复。模板只按目录名猜，'
                        '真实的 bundle id 与发行商命名猜不到，人工验收前务必补齐')
    p.add_argument('--dxmt-from', help='从这个已验证 prefix 的 system32 取 DXMT 四件')
    p.set_defaults(func=cmd_prepare)
    r = sub.add_parser('run', help='跑一轮')
    r.add_argument('--harness', required=True)
    r.add_argument('--seconds', type=int, default=90)
    r.add_argument('--window-owner', help='截图时按这个 owner 名找窗口，默认用游戏目录名')
    r.add_argument('--owner-verdict', metavar='原话',
                   help='人工验收的口头结论，原样记进 evidence.json，不代为判断')
    r.set_defaults(func=cmd_run)
    a = ap.parse_args()
    return a.func(a)


if __name__ == '__main__':
    raise SystemExit(main())
