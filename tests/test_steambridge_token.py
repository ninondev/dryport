# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""客户端回归：连接的第一条消息必须是启动器发的随机令牌；验证持有者，不验证游戏进程身份。
错令牌或缺令牌的连接被关闭且零次调用进入 Steam 表；对令牌的连接照常工作，helper 不因被拒连接退出。
思路沿用 sbtest：本账号用 cc 现编真 helper.c，配一个记录每次调用的假 libsteam_api.dylib（mock 表）；
没有 cc 就跳过并写明原因，真机复核走 real-check。"""
import os
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "lib" / "steambridge"
CC = shutil.which("cc")

# 假 libsteam_api.dylib：helper 按生产路径从 $HOME 下的 Steam bundle 里 dlopen 它（RTLD_NOW，符号必须齐）。
# 每次调用把名字追加进 $SB_MOCK_LOG（helper 进程环境），测试据此断言“零次调用进入表”。
MOCK_C = r"""
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
typedef void *H;
static void hit(const char *name) {
    const char *p = getenv("SB_MOCK_LOG");
    if (!p) return;
    FILE *f = fopen(p, "a");
    if (!f) return;
    fprintf(f, "%s\n", name);
    fclose(f);
}
int SteamAPI_InitFlat(char *err) { (void)err; hit("SteamAPI_InitFlat"); return 0; }
void SteamAPI_Shutdown(void) { hit("SteamAPI_Shutdown"); }
void SteamAPI_RunCallbacks(void) { hit("SteamAPI_RunCallbacks"); }
int SteamAPI_IsSteamRunning(void) { hit("SteamAPI_IsSteamRunning"); return 1; }
int SteamAPI_RestartAppIfNecessary(uint32_t app) { (void)app; hit("SteamAPI_RestartAppIfNecessary"); return 0; }
int SteamAPI_GetHSteamUser(void) { hit("SteamAPI_GetHSteamUser"); return 1; }
int SteamAPI_GetHSteamPipe(void) { hit("SteamAPI_GetHSteamPipe"); return 1; }
H SteamAPI_SteamUserStats_v012(void) { hit("SteamAPI_SteamUserStats_v012"); return (H)0x1001; }
H SteamAPI_SteamUser_v021(void) { hit("SteamAPI_SteamUser_v021"); return (H)0x1002; }
H SteamAPI_SteamApps_v008(void) { hit("SteamAPI_SteamApps_v008"); return (H)0x1003; }
H SteamAPI_SteamFriends_v017(void) { hit("SteamAPI_SteamFriends_v017"); return (H)0x1004; }
H SteamAPI_SteamUtils_v010(void) { hit("SteamAPI_SteamUtils_v010"); return (H)0x1005; }
H SteamAPI_SteamRemoteStorage_v016(void) { hit("SteamAPI_SteamRemoteStorage_v016"); return (H)0x1006; }
int SteamAPI_ISteamUserStats_StoreStats(H h) { (void)h; hit("SteamAPI_ISteamUserStats_StoreStats"); return 1; }
uint32_t SteamAPI_ISteamUserStats_GetNumAchievements(H h) { (void)h; hit("SteamAPI_ISteamUserStats_GetNumAchievements"); return 0; }
const char *SteamAPI_ISteamUserStats_GetAchievementName(H h, uint32_t i) { (void)h; (void)i; hit("SteamAPI_ISteamUserStats_GetAchievementName"); return ""; }
int SteamAPI_ISteamUserStats_GetAchievement(H h, const char *a, int *got) { (void)h; (void)a; hit("SteamAPI_ISteamUserStats_GetAchievement"); if (got) *got = 0; return 1; }
int SteamAPI_ISteamUserStats_SetAchievement(H h, const char *a) { (void)h; (void)a; hit("SteamAPI_ISteamUserStats_SetAchievement"); return 1; }
int SteamAPI_ISteamUserStats_ClearAchievement(H h, const char *a) { (void)h; (void)a; hit("SteamAPI_ISteamUserStats_ClearAchievement"); return 1; }
const char *SteamAPI_ISteamUserStats_GetAchievementDisplayAttribute(H h, const char *a, const char *k) { (void)h; (void)a; (void)k; hit("SteamAPI_ISteamUserStats_GetAchievementDisplayAttribute"); return ""; }
int SteamAPI_ISteamUserStats_GetAchievementAndUnlockTime(H h, const char *a, int *got, uint32_t *t) { (void)h; (void)a; hit("SteamAPI_ISteamUserStats_GetAchievementAndUnlockTime"); if (got) *got = 0; if (t) *t = 0; return 1; }
int SteamAPI_ISteamUserStats_IndicateAchievementProgress(H h, const char *a, uint32_t c, uint32_t m) { (void)h; (void)a; (void)c; (void)m; hit("SteamAPI_ISteamUserStats_IndicateAchievementProgress"); return 1; }
int SteamAPI_ISteamUserStats_GetStatInt32(H h, const char *n, int32_t *v) { (void)h; (void)n; hit("SteamAPI_ISteamUserStats_GetStatInt32"); if (v) *v = 0; return 1; }
int SteamAPI_ISteamUserStats_GetStatFloat(H h, const char *n, float *v) { (void)h; (void)n; hit("SteamAPI_ISteamUserStats_GetStatFloat"); if (v) *v = 0; return 1; }
int SteamAPI_ISteamUserStats_SetStatInt32(H h, const char *n, int32_t v) { (void)h; (void)n; (void)v; hit("SteamAPI_ISteamUserStats_SetStatInt32"); return 1; }
int SteamAPI_ISteamUserStats_SetStatFloat(H h, const char *n, float v) { (void)h; (void)n; (void)v; hit("SteamAPI_ISteamUserStats_SetStatFloat"); return 1; }
uint64_t SteamAPI_ISteamUser_GetSteamID(H h) { (void)h; hit("SteamAPI_ISteamUser_GetSteamID"); return 0; }
int SteamAPI_ISteamUser_BLoggedOn(H h) { (void)h; hit("SteamAPI_ISteamUser_BLoggedOn"); return 1; }
int SteamAPI_ISteamApps_BIsSubscribed(H h) { (void)h; hit("SteamAPI_ISteamApps_BIsSubscribed"); return 1; }
int SteamAPI_ISteamApps_BIsSubscribedFromFamilySharing(H h) { (void)h; hit("SteamAPI_ISteamApps_BIsSubscribedFromFamilySharing"); return 0; }
uint64_t SteamAPI_ISteamApps_GetAppOwner(H h) { (void)h; hit("SteamAPI_ISteamApps_GetAppOwner"); return 0; }
const char *SteamAPI_ISteamApps_GetCurrentGameLanguage(H h) { (void)h; hit("SteamAPI_ISteamApps_GetCurrentGameLanguage"); return ""; }
const char *SteamAPI_ISteamFriends_GetPersonaName(H h) { (void)h; hit("SteamAPI_ISteamFriends_GetPersonaName"); return ""; }
int SteamAPI_ISteamFriends_SetRichPresence(H h, const char *k, const char *v) { (void)h; (void)k; (void)v; hit("SteamAPI_ISteamFriends_SetRichPresence"); return 1; }
uint32_t SteamAPI_ISteamUtils_GetAppID(H h) { (void)h; hit("SteamAPI_ISteamUtils_GetAppID"); return 1201270; }
int SteamAPI_ISteamUtils_IsOverlayEnabled(H h) { (void)h; hit("SteamAPI_ISteamUtils_IsOverlayEnabled"); return 0; }
"""

F_HELLO, F_INIT, F_ISRUNNING = 0, 1, 5


def frame(fid, payload=b""):
    return struct.pack("<HI", fid, len(payload)) + payload


class TokenClient:
    """裸 TCP 客户端：连接后可先发任意字节（当令牌），再发 fid 请求。"""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.sock.settimeout(10)

    def send(self, data):
        self.sock.sendall(data)

    def shutdown_write(self):
        self.sock.shutdown(socket.SHUT_WR)

    def read_reply(self):
        header = self._read_exact(4)
        length = struct.unpack("<I", header)[0]
        return self._read_exact(length)

    def expect_closed(self):
        """helper 关闭连接：EOF 或复位都算；拿到数据就是失败。"""
        try:
            data = self.sock.recv(64)
        except ConnectionResetError:
            return
        if data:
            raise AssertionError(f"被拒连接竟收到数据：{data!r}")

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("连接在应答前被关闭")
            buf += chunk
        return buf

    def close(self):
        self.sock.close()


@unittest.skipUnless(CC, "本账号没有 cc，编不了 helper.c；真机复核走 real-check 请求")
class HelperTokenTest(unittest.TestCase):
    APPID = 480

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="sn-bridge-token-")
        base = Path(cls.temp.name)
        cls.helper = base / "steambridge-helper"
        subprocess.run([CC, "-O2", "-arch", "arm64", str(BRIDGE / "helper.c"), "-o", str(cls.helper)],
                       check=True, capture_output=True, timeout=120)
        # 假 Steam 表放进 $HOME 下的生产加载路径（带空格，顺带验证路径拼接）
        cls.home = base / "mock home"
        dylib = cls.home / "Library/Application Support/Steam/Steam.AppBundle/Steam/Contents/MacOS" \
                           "/Frameworks/Steam Helper.app/Contents/MacOS/libsteam_api.dylib"
        dylib.parent.mkdir(parents=True)
        source = base / "mock.c"
        source.write_text(MOCK_C)
        subprocess.run([CC, "-dynamiclib", "-O2", str(source), "-o", str(dylib)],
                       check=True, capture_output=True, timeout=120)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="client-", dir=self.temp.name))
        self.counter = 0
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            # 不发信号优先靠 LINGER 自退；退不掉再 terminate（沙箱里可能被拒，忽略）
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass

    def start_helper(self, token):
        """起一个带令牌的 helper，等它监听；返回 (进程, 端口, mock 调用记录文件)。"""
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        self.counter += 1
        log = self.base / f"helper-{self.counter}.log"
        calls = self.base / f"calls-{self.counter}.log"
        env = dict(os.environ, HOME=str(self.home), STEAMBRIDGE_LINGER="1", SB_MOCK_LOG=str(calls))
        if token is not None:
            env["STEAMBRIDGE_TOKEN"] = token
        with log.open("wb") as output:
            process = subprocess.Popen([str(self.helper), str(self.APPID), str(port)],
                                       env=env, stdout=output, stderr=subprocess.STDOUT)
        self.processes.append(process)
        until = time.monotonic() + 15
        while time.monotonic() < until:
            if process.poll() is not None:
                if token is None:
                    return process, port, calls      # 无令牌拒启正是这一分支的预期
                self.fail(f"helper 提前退出 rc={process.returncode}：\n{log.read_text(errors='replace')}")
            if b"listening on 127.0.0.1:" in log.read_bytes():
                return process, port, calls
            time.sleep(0.05)
        self.fail(f"helper 15 秒内既没监听也没退出：\n{log.read_text(errors='replace')}")

    def mock_calls(self, calls):
        return calls.read_text() if calls.exists() else ""

    def test_helper_refuses_to_start_without_token(self):
        process, port, calls = self.start_helper(token=None)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.fail("没有 STEAMBRIDGE_TOKEN 时 helper 应当拒绝启动，它却在运行")
        output = (self.base / "helper-1.log").read_text(errors="replace")
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("STEAMBRIDGE_TOKEN", output)
        self.assertNotIn("listening on 127.0.0.1:", output)

    def test_rejected_connections_make_zero_calls_and_right_token_works(self):
        token = secrets.token_hex(32)
        wrong = secrets.token_hex(32)
        while wrong == token:
            wrong = secrets.token_hex(32)
        process, port, calls = self.start_helper(token=token)

        # 缺令牌：直接发请求（模拟本机其他程序），连接应被关闭且零调用
        client = TokenClient(port)
        client.send(frame(F_INIT, struct.pack("<I", self.APPID)))
        client.shutdown_write()
        client.expect_closed()
        client.close()
        self.assertEqual(self.mock_calls(calls), "")
        self.assertIsNone(process.poll(), "被拒连接不应让 helper 退出")

        # 错令牌（等长）：helper 应在处理任何调用前关闭连接
        client = TokenClient(port)
        client.send(wrong.encode() + frame(F_INIT, struct.pack("<I", self.APPID)))
        client.shutdown_write()
        client.expect_closed()
        client.close()
        self.assertEqual(self.mock_calls(calls), "")
        self.assertIsNone(process.poll(), "被拒连接不应让 helper 退出")

        # 对令牌：连接照常工作，调用进入表
        client = TokenClient(port)
        client.send(token.encode())
        client.send(frame(F_HELLO))
        self.assertEqual(client.read_reply(), b"\x01")
        client.send(frame(F_INIT, struct.pack("<I", self.APPID)))
        self.assertEqual(client.read_reply(), b"\x01")
        client.send(frame(F_ISRUNNING))
        self.assertEqual(client.read_reply(), b"\x01")
        client.close()
        calls_seen = self.mock_calls(calls)
        self.assertIn("SteamAPI_InitFlat", calls_seen)
        self.assertIn("SteamAPI_IsSteamRunning", calls_seen)
        # 收尾连接触发 LINGER 自退，测试路径不依赖信号
        TokenClient(port).close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.fail("最后一个客户端断开后 helper 应按 LINGER 自退")


if __name__ == "__main__":
    unittest.main()
