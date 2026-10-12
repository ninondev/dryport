# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""自动冒烟的判据与清理边界，全部在临时目录里演：安装失败归类、纯色窗口判据、verdict 组合、清理只删开跑前不存在的路径、
api.py 的 .smoke join。不起游戏、不下载、不碰真实 SN_GAMES。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_smoke_run(env):
    """按给定 SN_GAMES 加载 tools/smoke-run.py（它顶层 import lib/paths，路径来自环境）。"""
    for k, v in env.items():
        os.environ[k] = v
    for m in ("paths", "evidence", "running", "uninstall_purge", "api"):
        sys.modules.pop(m, None)
    sys.path.insert(0, str(ROOT / "lib"))
    spec = importlib.util.spec_from_file_location("smoke_run", ROOT / "tools" / "smoke-run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SmokeRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sn-smoke-")
        base = Path(self.tmp.name)
        self.games = base / "games"; self.games.mkdir()
        self.runtimes = base / "runtimes"; self.runtimes.mkdir()
        self.steam = base / "steam"; self.steam.mkdir()
        self.saved = {k: os.environ.get(k) for k in ("SN_GAMES", "SN_HOME", "SN_RUNTIMES", "STEAM", "HOME")}
        self.env = {"SN_GAMES": str(self.games), "SN_HOME": str(ROOT), "SN_RUNTIMES": str(self.runtimes), "STEAM": str(self.steam)}
        self.mod = load_smoke_run(self.env)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_install_failure_classification_follows_cli_wording(self):
        c = self.mod.classify_install
        self.assertEqual(c(2, "已停止: 检出反作弊，不下载或打包。"), "install_failed:risk_anticheat")
        self.assertEqual(c(1, "已停止: 该游戏带联机，自动改包不放行。"), "install_failed:risk_online")
        self.assertEqual(c(3, "已停止: 风险检查未完成（退出码 3）"), "install_failed:risk_pending")
        self.assertEqual(c(1, "主程序 x.exe 带 Steamworks DRM 包装（PE 节 .bind）。…所以这款不打包。\n打包失败, 到此为止。"), "install_failed:drm")
        self.assertEqual(c(1, "本机没有 Unity 2021.3.1f1 的 Mac 播放器模板，SN_UNITY_NO_FETCH=1 下不下载 (unity_player_missing)"), "install_failed:unity_player_missing")
        self.assertEqual(c(3, "      客户端下载没成, 退回 steamcmd 的老办法。"), "install_failed:download")
        self.assertEqual(c(1, "缺 wine-cx 运行时：有发布包时 …"), "install_failed:runtime_missing")
        self.assertEqual(c(1, "assemble-mac-app.sh 失败\n打包失败, 到此为止。"), "install_failed:wrap")
        self.assertEqual(c(124, "install 超时 3600 秒"), "install_failed:timeout")
        self.assertEqual(c(7, "莫名其妙"), "install_failed:rc7")

    def test_blank_and_liveness_rules_on_real_cases(self):
        blank = self.mod.looks_blank
        self.assertTrue(blank({"colors": 2, "dominant_fraction": 0.96}))       # 一款 Wine 游戏 全白
        self.assertTrue(blank({"colors": 200, "dominant_fraction": 0.995}))    # 几乎全黑
        self.assertFalse(blank({"colors": 11, "dominant_fraction": 0.88}))     # 一款像素游戏 三色画风，是真画面
        self.assertFalse(blank({"colors": 60, "dominant_fraction": 0.91}))     # 黑底白字的开场提示（一款 RPG Maker 游戏）
        self.assertFalse(blank(None))
        live = self.mod.liveness_signals
        shot = lambda h, d, dom=0.5: {"sha256": h, "stats": {"detail_fraction": d, "dominant_fraction": dom}}
        # 一款 NW.js 游戏 黑窗：一直重绘、噪点让三张哈希不同，但整幅近纯色 → 0 信号
        self.assertEqual(live({"frames": 2731}, [shot("a", 0.015, 0.96), shot("b", 0.015, 0.96), shot("c", 0.015, 0.96)], set()), [])
        # 一款 Wine 游戏 白窗：4 帧、三张全同、没投键、无结构 → 0 信号
        self.assertEqual(live({"frames": 4}, [shot("a", 0.047)] * 3, set()), [])
        # 一款 NW.js 游戏修前黑屏：帧数一直在刷，但没别的 → 1 信号
        self.assertEqual(live({"frames": 2319}, [shot("a", 0.0)] * 3, set()), ["frames"])
        # 一款 RPG Maker 游戏：投键后画面变了 + 截图不同 → 2 信号
        self.assertEqual(live({"frames": 5}, [shot("a", 0.01), shot("b", 0.01)], {"input_changed"}), ["changed", "input"])
        # 真游戏画面：帧数 + 结构
        self.assertEqual(live({"frames": 2712}, [shot("a", 0.6)] * 3, set()), ["frames", "detail"])

    def test_small_window_counts_as_the_game_only_when_titled_like_it(self):
        small = self.mod.small_game_window
        w = lambda t, W, H: {"title": t, "bounds": {"Width": W, "Height": H}}
        self.assertTrue(small(w("PIXELFIXTURE", 488, 304), ("PIXELFIXTURE", "PIXELFIXTURE")))              # 像素风本体窗口
        self.assertTrue(small(w("Fixture Title v1.0.08", 480, 300), ("Fixture Title",)))                 # 标题带版本号
        self.assertFalse(small(w("Wine C++ Runtime Library", 480, 300), ("Fixture Title",)))           # 运行库报错框
        self.assertFalse(small(w("Error", 400, 200), ("Fixture Title",)))
        self.assertFalse(small(w("PIXELFIXTURE", 200, 100), ("PIXELFIXTURE",)))                          # 太小
        self.assertFalse(small(w("", 480, 300), ("Game",)))
        self.assertFalse(small(w("XP", 480, 300), ("XP",)))                                          # 名字太短不认

    def test_verdict_combinations(self):
        class L:
            exit_code = None
            started = 0

            def __init__(self, signals=(), rc=None, started=None):
                self._s = list(signals); self.exit_code = rc
                import time
                self.started = time.time() - 30 if started is None else started

            def crash_signals(self):
                return self._s
        v = self.mod.verdict_of
        self.assertEqual(v("window", "alive", L()), "alive")
        self.assertEqual(v("window", "alive", L(["winedbg"])), "crashed")
        self.assertEqual(v("window", "crashed", L()), "crashed")
        self.assertEqual(v("window", "exited", L(rc=0)), "exited_early")
        self.assertEqual(v("window", "exited", L(rc=53)), "crashed")
        self.assertEqual(v("timeout", None, L()), "no_window")
        self.assertEqual(v("exited", None, L(rc=126)), "launch_failed")
        import time
        self.assertEqual(v("exited", None, L(rc=0, started=time.time() - 3)), "launch_failed")   # 10 秒内退且无窗口
        self.assertEqual(v("exited", None, L(rc=0, started=time.time() - 40)), "exited_early")
        self.assertEqual(v("user_active", None, L()), "aborted_user_active")

    def test_cleanup_deletes_only_what_this_run_created(self):
        games = self.games
        # 开跑前就有的：用户自己的下载目录 + 状态；快照之后新建的：本次运行下载、app、prefix
        (games / "Old Game").mkdir(); (games / "Old Game/.dryport-appid").write_text("777\n")
        old_state = games / ".wine-state" / "777"; (old_state / "prefix").mkdir(parents=True); (old_state / "steam_appid.txt").write_text("777")
        snap = self.mod.Snapshot()
        new_dl = games / "New Game"; new_dl.mkdir(); (new_dl / ".dryport-appid").write_text("4242\n"); (new_dl / "a.bin").write_bytes(b"x" * 100)
        app = games / "New Game.app"; (app / "Contents/Resources").mkdir(parents=True); (app / "Contents/Resources/steam_appid.txt").write_text("4242")
        state = games / ".wine-state" / "4242"; (state / "prefix").mkdir(parents=True); (state / "steam_appid.txt").write_text("4242")
        evid = Path(self.tmp.name) / "evid"; (evid / "logs").mkdir(parents=True)
        r = self.mod.cleanup("4242", app, str(new_dl), snap, evid, keep_states=False)
        self.assertFalse(new_dl.exists()); self.assertFalse(app.exists()); self.assertFalse(state.exists())
        self.assertFalse(list((games / ".uninstalled").glob("4242-smoke-*")), "归档容器应随删除一起消失")
        self.assertTrue((games / "Old Game").exists()); self.assertTrue(old_state.exists())
        self.assertEqual(r["refused"], [])
        # 同一 appid 的既有状态（开跑前就有）不删：再演一次，快照包含它
        snap2 = self.mod.Snapshot()
        r2 = self.mod.cleanup("777", None, str(games / "Old Game"), snap2, evid, keep_states=False)
        self.assertTrue((games / "Old Game").exists(), "开跑前就有的下载目录不能删")
        self.assertIn(str(games / "Old Game"), r2["skipped_existing"])

    def test_api_joins_latest_smoke_result(self):
        smoke = self.games / ".smoke"; smoke.mkdir()
        (smoke / "4242.json").write_text(json.dumps({"protocol": 1, "appid": "4242", "runs": [], "latest": {"verdict": "alive", "flags": ["static"], "at": "t"}}))
        (smoke / "ledger.json").write_text(json.dumps({"downloaded_bytes": 0, "runs": []}))
        (smoke / "bad.json").write_text("{not json")
        sys.modules.pop("api", None)
        import api
        latest = api.smoke_latest()
        self.assertEqual(latest, {"4242": {"verdict": "alive", "flags": ["static"], "at": "t"}})
        self.assertEqual(api._smoke_counts(), {"alive": 1})


if __name__ == "__main__":
    unittest.main()
