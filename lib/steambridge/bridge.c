// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* steam_api64.dll for Wine on macOS: forwards the Steamworks flat API to a native macOS helper
 * that loads Valve's libsteam_api.dylib from the user's Steam client bundle at runtime, so a game can talk to the
 * Steam client you are already logged into: identity, ownership, stats, achievements.
 *
 * Build: ./build.sh (generated files and binaries go to SN_RUNTIMES/steambridge).
 */
#include <winsock2.h>
#include <windows.h>
#include <ws2tcpip.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "proto.h"
#include "bridge.h"

/* ---------- logging ----------
 * Win32 handles only, no CRT FILE*. 2026-09-11: Unity unloads and reloads plugin DLLs, DllMain ran
 * DETACH, we fclose()d the log and left the pointer dangling, and the next log line crashed inside
 * ucrtbase's stdio (page fault reading NULL+0x30). A HANDLE plus an explicit INVALID marker is the
 * whole fix, and it keeps the CRT out of the path entirely. */
static HANDLE logh = INVALID_HANDLE_VALUE;
static CRITICAL_SECTION lock;
static int inited;
static volatile LONG sb_init_counter = 1;   /* bumps on every successful Init so cached C++ contexts refresh */

/* Diagnostics that answered entry-06's "why 2.4 RequestUserStats a second" in one line instead of an afternoon (2026-09-13):
 * every log line carries the seconds since attach, every call and delivered callback is counted, and the counts are
 * written once a minute and at detach (a game killed by wineserver -k never reaches detach). */
static ULONGLONG sb_t0, sb_last_summary;
static unsigned sb_fid_count[F_MAX + 1];
static unsigned sb_cb_count[4096], sb_cb_listen[4096];
static void sb_summary(const char *why);

void sb_log(const char *fmt, ...) {
    HANDLE h = logh;
    if (h == INVALID_HANDLE_VALUE || !h) return;
    char line[1024];
    ULONGLONG ms = GetTickCount64() - sb_t0;
    int p = _snprintf(line, 24, "[+%lu.%03lu] ", (unsigned long)(ms / 1000), (unsigned long)(ms % 1000));
    if (p < 0 || p > 23) p = 0;
    va_list ap; va_start(ap, fmt);
    int n = _vsnprintf(line + p, sizeof line - 2 - p, fmt, ap);
    va_end(ap);
    if (n < 0) n = (int)strlen(line + p);
    if (n > (int)sizeof line - 2 - p) n = (int)sizeof line - 2 - p;
    n += p;
    line[n++] = '\n';
    DWORD wrote;
    WriteFile(h, line, (DWORD)n, &wrote, NULL);
}

static void sb_summary(const char *why) {
    char buf[1000]; int n = _snprintf(buf, sizeof buf, "summary (%s) calls fid:count", why);
    for (int f = 0; f <= F_MAX && n < (int)sizeof buf - 16; f++) if (sb_fid_count[f]) n += _snprintf(buf + n, sizeof buf - n, " %d:%u", f, sb_fid_count[f]);
    sb_log("%s", buf);
    n = _snprintf(buf, sizeof buf, "summary (%s) callbacks id:delivered(listener runs)", why);
    for (int id = 0; id < 4096 && n < (int)sizeof buf - 24; id++) if (sb_cb_count[id]) n += _snprintf(buf + n, sizeof buf - n, " %d:%u(%u)", id, sb_cb_count[id], sb_cb_listen[id]);
    sb_log("%s", buf);
}

static char stub_seen[4096];
void sb_stub_hit(int idx) {
    if (idx >= 0 && idx < (int)sizeof(stub_seen) && !stub_seen[idx]) {
        stub_seen[idx] = 1;
        sb_log("stub: %s", idx < stub_count ? stub_names[idx] : "?");
    }
}

/* ---------- socket ---------- */
static SOCKET sock = INVALID_SOCKET;

static int sb_connect(void) {
    if (sock != INVALID_SOCKET) return 1;
    WSADATA w; static int wsa;
    if (!wsa) { if (WSAStartup(MAKEWORD(2,2), &w)) { sb_log("WSAStartup failed"); return 0; } wsa = 1; }
    int port = SB_PORT_DEFAULT;
    char buf[32];
    if (GetEnvironmentVariableA("STEAMBRIDGE_PORT", buf, sizeof buf)) port = atoi(buf);
    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) return 0;
    struct sockaddr_in a; memset(&a, 0, sizeof a);
    a.sin_family = AF_INET; a.sin_port = htons((u_short)port);
    a.sin_addr.s_addr = htonl(0x7f000001);
    if (connect(s, (struct sockaddr *)&a, sizeof a)) {
        sb_log("connect 127.0.0.1:%d failed (%d) - is steambridge-helper running?", port, WSAGetLastError());
        closesocket(s); return 0;
    }
    int one = 1; setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (char *)&one, sizeof one);
    /* First message on every connection: the per-launch token. The launcher sets
     * STEAMBRIDGE_TOKEN next to STEAMBRIDGE_PORT; the helper closes the connection before handling any call
     * when it does not match. Token possession does not identify the calling process;
     * another program that obtains the token can use the helper. */
    {
        char tok[256];
        DWORD toklen = GetEnvironmentVariableA("STEAMBRIDGE_TOKEN", tok, sizeof tok);
        if (toklen == 0 || toklen >= sizeof tok) {
            sb_log("STEAMBRIDGE_TOKEN missing in the Wine environment; cannot authenticate to the helper");
            closesocket(s); return 0;
        }
        const char *p = tok; int left = (int)toklen;
        while (left > 0) {
            int r = send(s, p, left, 0);
            if (r <= 0) { sb_log("sending the token failed (%d)", WSAGetLastError()); closesocket(s); return 0; }
            p += r; left -= r;
        }
    }
    sock = s; sb_log("connected to helper on port %d", port);
    return 1;
}

static int xfer_all(char *p, int n, int send_dir) {
    while (n > 0) {
        int r = send_dir ? send(sock, p, n, 0) : recv(sock, p, n, 0);
        if (r <= 0) { closesocket(sock); sock = INVALID_SOCKET; return 0; }
        p += r; n -= r;
    }
    return 1;
}

/* ---------- payload buffers ---------- */
typedef struct { unsigned char b[4096]; int n; int rp; } buf_t;
static void bw_u8(buf_t *b, unsigned v) { if (b->n < (int)sizeof b->b) b->b[b->n++] = (unsigned char)v; }
static void bw_u32(buf_t *b, unsigned v) { for (int i = 0; i < 4; i++) bw_u8(b, (v >> (8*i)) & 0xff); }
static void bw_u64(buf_t *b, unsigned long long v) { for (int i = 0; i < 8; i++) bw_u8(b, (unsigned)((v >> (8*i)) & 0xff)); }
static void bw_f32(buf_t *b, float f) { unsigned u; memcpy(&u, &f, 4); bw_u32(b, u); }
static void bw_str(buf_t *b, const char *s) {
    unsigned n = s ? (unsigned)strlen(s) : 0;
    if (n > 2048) n = 2048;
    bw_u32(b, n);
    for (unsigned i = 0; i < n; i++) bw_u8(b, (unsigned char)s[i]);
}
static unsigned br_u8(buf_t *b) { return b->rp < b->n ? b->b[b->rp++] : 0; }
static unsigned br_u32(buf_t *b) { unsigned v = 0; for (int i = 0; i < 4; i++) v |= br_u8(b) << (8*i); return v; }
static unsigned long long br_u64(buf_t *b) { unsigned long long v = 0; for (int i = 0; i < 8; i++) v |= (unsigned long long)br_u8(b) << (8*i); return v; }
static float br_f32(buf_t *b) { unsigned u = br_u32(b); float f; memcpy(&f, &u, 4); return f; }
static const char *br_str(buf_t *b, char *out, int cap) {
    unsigned n = br_u32(b); int i = 0;
    for (unsigned k = 0; k < n; k++) { unsigned c = br_u8(b); if (i < cap - 1) out[i++] = (char)c; }
    out[i] = 0; return out;
}

/* one call at a time; returns 0 if the helper is unreachable */
static int sb_calls;
static int sb_call(int fid, buf_t *req, buf_t *resp) {
    int ok = 0;
    if (sb_calls++ < 300) sb_log("-> fid %d", fid);
    if (fid >= 0 && fid <= F_MAX) sb_fid_count[fid]++;
    { ULONGLONG now = GetTickCount64(); if (now - sb_last_summary >= 60000) { sb_last_summary = now; sb_summary("60 s"); } }
    EnterCriticalSection(&lock);
    if (sb_connect()) {
        unsigned char hdr[6];
        hdr[0] = fid & 0xff; hdr[1] = (fid >> 8) & 0xff;
        unsigned n = req ? (unsigned)req->n : 0;
        for (int i = 0; i < 4; i++) hdr[2+i] = (n >> (8*i)) & 0xff;
        if (xfer_all((char *)hdr, 6, 1) && (n == 0 || xfer_all((char *)req->b, n, 1))) {
            unsigned char lh[4];
            if (xfer_all((char *)lh, 4, 0)) {
                unsigned rn = lh[0] | (lh[1]<<8) | (lh[2]<<16) | ((unsigned)lh[3]<<24);
                if (rn <= sizeof resp->b && (rn == 0 || xfer_all((char *)resp->b, rn, 0))) {
                    resp->n = (int)rn; resp->rp = 0; ok = 1;
                }
            }
        }
        if (!ok) sb_log("call %d failed", fid);
    }
    LeaveCriticalSection(&lock);
    return ok;
}

/* rotating buffers for the const char* returns, same lifetime rule Steamworks itself uses */
static char retbuf[8][1024];
static int retidx;
static char *next_retbuf(void) { retidx = (retidx + 1) & 7; return retbuf[retidx]; }

#define CALL0(fid) buf_t q = {0}, r = {0}; int ok = sb_call(fid, &q, &r); (void)ok;

/* ---------- lifecycle ---------- */
API int SteamAPI_Init(void) {
    buf_t q = {0}, r = {0};
    char app[32] = {0};
    GetEnvironmentVariableA("SteamAppId", app, sizeof app);
    if (!app[0]) {
        HANDLE f = CreateFileA("steam_appid.txt", GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
        if (f != INVALID_HANDLE_VALUE) {
            DWORD got = 0; ReadFile(f, app, sizeof app - 1, &got, NULL); app[got] = 0; CloseHandle(f);
        }
    }
    for (char *c = app; *c; c++) if (*c < '0' || *c > '9') { *c = 0; break; }
    bw_u32(&q, (unsigned)atoi(app));
    if (!sb_call(F_INIT, &q, &r)) return 0;
    int ok = (int)br_u8(&r);
    if (ok) InterlockedIncrement(&sb_init_counter);
    sb_log("SteamAPI_Init appid=%s -> %d", app[0] ? app : "(none)", ok);
    return ok;
}
API int SteamAPI_InitSafe(void) { return SteamAPI_Init(); }
/* SDK 1.59+ entry points: ESteamAPIInitResult 0 = OK, 2 = NoSteamClient; pOutErrMsg is a 1024-byte SteamErrMsg. */
static int init_result(char *err) {
    if (SteamAPI_Init()) { if (err) err[0] = 0; return 0; }
    if (err) { const char *m = "steambridge: Steam client not reachable through the helper"; size_t n = strlen(m); memcpy(err, m, n + 1); }
    return 2;
}
API int SteamAPI_InitFlat(char *err) { return init_result(err); }
API int SteamInternal_SteamAPI_Init(const char *versions, char *err) { (void)versions; return init_result(err); }
API void SteamAPI_Shutdown(void) { CALL0(F_SHUTDOWN) }
API int SteamAPI_IsSteamRunning(void) { CALL0(F_ISRUNNING) return (int)br_u8(&r); }
API int SteamAPI_RestartAppIfNecessary(unsigned appid) {
    buf_t q = {0}, r = {0}; bw_u32(&q, appid);
    if (!sb_call(F_RESTART, &q, &r)) return 0;
    return (int)br_u8(&r);
}
/* Steam's own minidump hooks: no-ops here, games call them unconditionally */
API void SteamAPI_WriteMiniDump(unsigned a, void *b, unsigned c) { (void)a;(void)b;(void)c; }
API void SteamAPI_SetMiniDumpComment(const char *c) { (void)c; }

/* ---------- callbacks ----------
 * The helper drains Valve's callbacks with manual dispatch, converts them to the Win64 layout and hands them over on
 * F_CB_DRAIN. Two consumers on this side, mirroring steam_api.dll:
 *   SteamAPI_RunCallbacks()            games that registered CCallbackBase objects (C++ SDK, Steamworks.NET)
 *   SteamAPI_ManualDispatch_RunFrame() games that pull callbacks themselves (Facepunch.Steamworks)
 * CCallbackBase on Win64: vtable at 0, uint8 m_nCallbackFlags at 8, int m_iCallback at 12. MSVC lays overloaded
 * virtuals out in reverse declaration order, so the table Valve's own DLL calls is
 *   slot 0 = Run(void *pvParam, bool bIOFailure, SteamAPICall_t)   (call results; the bridge issues none)
 *   slot 1 = Run(void *pvParam)                                     (callbacks)
 *   slot 2 = GetCallbackSizeBytes()
 * Steamworks.NET builds its native vtable in exactly that order on Windows. The size slot is used as a guard: the game
 * always receives a buffer of the size it declared, zero-padded, never a shorter one. Call results are not forwarded. */
#if defined(__i386__)
/* Win32: CCallbackBase vtable at 0, uint8 m_nCallbackFlags at 4, int m_iCallback at 8; member calls are __thiscall;
 * CallbackMsg_t is user@0 id@4 param@8 (4 bytes) len@12. Callback structs are pack(4), the same layout as macOS. */
typedef void (__attribute__((thiscall)) *cb_run_fn)(void *self, void *param);
typedef int (__attribute__((thiscall)) *cb_size_fn)(void *self);
#define CB_FLAGS_OFF 4
#define CB_ID_OFF 8
#define CBMSG_LEN_OFF 12
#define SB_DLL_NAME "steam_api.dll"
#include "cbconv.h"
#else
typedef void (*cb_run_fn)(void *self, void *param);
typedef int (*cb_size_fn)(void *self);
#define CB_FLAGS_OFF 8
#define CB_ID_OFF 12
#define CBMSG_LEN_OFF 16
#define SB_DLL_NAME "steam_api64.dll"
#endif
typedef struct { int32_t id; int32_t len; uint8_t data[256]; } cb_item;
static struct { void *cb; int id; } cb_regs[1024]; static int cb_nregs;
static cb_item cb_queue[64]; static int cbq_head, cbq_count;      /* ManualDispatch consumers */
static int cb_manual, cb_hsteamuser;
static char cb_logged[4096];

API void SteamAPI_RegisterCallback(void *cb, int iCallback) {
    if (!cb) return;
    EnterCriticalSection(&lock);
    ((uint8_t *)cb)[CB_FLAGS_OFF] |= 1; *(int *)((uint8_t *)cb + CB_ID_OFF) = iCallback;   /* k_ECallbackFlagsRegistered, m_iCallback */
    int i; for (i = 0; i < cb_nregs; i++) if (cb_regs[i].cb == cb) { cb_regs[i].id = iCallback; break; }
    if (i == cb_nregs && cb_nregs < (int)(sizeof cb_regs / sizeof cb_regs[0])) { cb_regs[cb_nregs].cb = cb; cb_regs[cb_nregs].id = iCallback; cb_nregs++; }
    LeaveCriticalSection(&lock);
    sb_log("RegisterCallback %d (%p)", iCallback, cb);
}
API void SteamAPI_UnregisterCallback(void *cb) {
    if (!cb) return;
    EnterCriticalSection(&lock);
    for (int i = 0; i < cb_nregs; i++) if (cb_regs[i].cb == cb) { cb_regs[i] = cb_regs[--cb_nregs]; break; }
    ((uint8_t *)cb)[CB_FLAGS_OFF] &= (uint8_t)~1u;
    LeaveCriticalSection(&lock);
}
/* ---- call results (2026-09-13) ----
 * Since RequestUserStats is forwarded the game holds real SteamAPICall_t handles. Polling goes straight to the helper;
 * registered CCallResult objects are driven from RunCallbacks / RunFrame: when the helper says the call completed, the
 * result struct (relaid out like a callback) is handed to slot 0, Run(param, bIOFailure, handle), and the entry is dropped. */
#if defined(__i386__)
typedef void (__attribute__((thiscall)) *cb_run3_fn)(void *self, void *param, int failed, unsigned long long h);
#else
typedef void (*cb_run3_fn)(void *self, void *param, int failed, unsigned long long h);
#endif
static struct { void *cb; unsigned long long h; } cr_regs[64]; static int cr_nregs;

API int SteamAPI_ISteamUtils_IsAPICallCompleted(void *self, unsigned long long h, unsigned char *failed) {
    (void)self; buf_t q = {0}, r = {0}; bw_u64(&q, h);
    if (failed) *failed = 0;
    if (!h || !sb_call(F_UT_ISCALLDONE, &q, &r)) return 0;
    int done = (int)br_u8(&r); unsigned f = br_u8(&r);
    if (failed) *failed = (unsigned char)f;
    return done;
}
API int SteamAPI_ISteamUtils_GetAPICallFailureReason(void *self, unsigned long long h) {
    (void)self; buf_t q = {0}, r = {0}; bw_u64(&q, h);
    if (!sb_call(F_UT_CALLFAIL, &q, &r)) return -1;
    return (int)br_u32(&r);
}
API int SteamAPI_ISteamUtils_GetAPICallResult(void *self, unsigned long long h, void *out, int size, int expected, unsigned char *failed) {
    (void)self; buf_t q = {0}, r = {0}; bw_u64(&q, h); bw_u32(&q, (unsigned)expected);
    if (failed) *failed = 0;
    if (!h || !out || size <= 0 || !sb_call(F_UT_CALLRESULT, &q, &r)) return 0;
    int ok = (int)br_u8(&r); unsigned f = br_u8(&r); unsigned n = br_u32(&r);
    if (failed) *failed = (unsigned char)f;
    uint8_t buf[256]; if (n > sizeof buf) n = sizeof buf;
    for (unsigned i = 0; i < n; i++) buf[i] = (uint8_t)br_u8(&r);
#if defined(__i386__)
    { uint8_t p4[256]; int n4 = sb_cb_to_pack4(expected, buf, (int)n, p4, (int)sizeof p4); if (n4 > 0) { memcpy(buf, p4, (size_t)n4); n = (unsigned)n4; } }
#endif
    if (!ok) return 0;
    memset(out, 0, (size_t)size); memcpy(out, buf, n < (unsigned)size ? n : (unsigned)size);
    if (expected >= 0 && expected < 4096 && !cb_logged[expected]) { cb_logged[expected] = 1; sb_log("call result %d: %u bytes (caller buffer %d)", expected, n, size); }
    return 1;
}
API int SteamAPI_ManualDispatch_GetAPICallResult(int pipe, unsigned long long h, void *out, int size, int expected, unsigned char *failed) {
    (void)pipe; return SteamAPI_ISteamUtils_GetAPICallResult(NULL, h, out, size, expected, failed);
}
API void SteamAPI_RegisterCallResult(void *cb, unsigned long long h) {
    if (!cb || !h) return;
    EnterCriticalSection(&lock);
    int i; for (i = 0; i < cr_nregs; i++) if (cr_regs[i].cb == cb) { cr_regs[i].h = h; break; }
    if (i == cr_nregs && cr_nregs < (int)(sizeof cr_regs / sizeof cr_regs[0])) { cr_regs[cr_nregs].cb = cb; cr_regs[cr_nregs].h = h; cr_nregs++; }
    LeaveCriticalSection(&lock);
    sb_log("RegisterCallResult %p for call %llu", cb, h);
}
API void SteamAPI_UnregisterCallResult(void *cb, unsigned long long h) {
    (void)h; if (!cb) return;
    EnterCriticalSection(&lock);
    for (int i = 0; i < cr_nregs; i++) if (cr_regs[i].cb == cb) { cr_regs[i] = cr_regs[--cr_nregs]; break; }
    LeaveCriticalSection(&lock);
}
static void drive_call_results(void) {
    void *cbs[64]; unsigned long long hs[64]; int n = 0;
    EnterCriticalSection(&lock);
    for (int i = 0; i < cr_nregs && n < 64; i++) { cbs[n] = cr_regs[i].cb; hs[n] = cr_regs[i].h; n++; }
    LeaveCriticalSection(&lock);
    for (int i = 0; i < n; i++) {
        unsigned char failed = 0;
        if (!SteamAPI_ISteamUtils_IsAPICallCompleted(NULL, hs[i], &failed)) continue;
        void **vt = *(void ***)cbs[i];
        int want = ((cb_size_fn)vt[2])(cbs[i]);
        int expected = *(int *)((uint8_t *)cbs[i] + CB_ID_OFF);
        uint8_t buf[512]; memset(buf, 0, sizeof buf);
        int ok = 0;
        if (want > 0 && want <= (int)sizeof buf && !failed) ok = SteamAPI_ISteamUtils_GetAPICallResult(NULL, hs[i], buf, want, expected, &failed);
        SteamAPI_UnregisterCallResult(cbs[i], hs[i]);            /* Valve's library also unregisters before Run */
        ((cb_run3_fn)vt[0])(cbs[i], buf, (failed || !ok) ? 1 : 0, hs[i]);
    }
}

/* Games poll callbacks once per frame; Unity in batch mode (no vsync) did it 2700 times a second (anonymous-title 2026-09-13), each a
 * loopback round trip. One drain per 4 ms is plenty: a callback is delivered at most 4 ms later than it would have been. */
static ULONGLONG last_drain;
static int fetch_callbacks(cb_item *out, int cap) {
    ULONGLONG now = GetTickCount64();
    if (last_drain && now - last_drain < 4) return 0;
    last_drain = now ? now : 1;
    buf_t q = {0}, r = {0};
    if (!sb_call(F_CB_DRAIN, &q, &r)) return 0;
    unsigned n = br_u8(&r); int got = 0;
    for (unsigned i = 0; i < n; i++) {
        int32_t id = (int32_t)br_u32(&r); uint32_t len = br_u32(&r);
        if (got >= cap || len > sizeof out[0].data) { for (uint32_t k = 0; k < len; k++) br_u8(&r); continue; }
        out[got].id = id; out[got].len = (int32_t)len;
        for (uint32_t k = 0; k < len; k++) out[got].data[k] = (uint8_t)br_u8(&r);
#if defined(__i386__)
        {   /* the helper ships Win64 layout; Win32 wants pack(4) */
            uint8_t p4[256]; int n4 = sb_cb_to_pack4(id, out[got].data, (int)len, p4, (int)sizeof p4);
            if (n4 > 0) { memcpy(out[got].data, p4, (size_t)n4); out[got].len = n4; }
            else if (id >= 0 && id < 4096 && !cb_logged[id]) { cb_logged[id] = 1; sb_log("callback %d: no pack(4) relayout (%d), delivered as Win64 bytes", id, n4); }
        }
#endif
        got++;
    }
    return got;
}

void sb_persona_changed(void);
static void deliver(const cb_item *it) {
    void *targets[64]; int n = 0;
    if (it->id == 304) sb_persona_changed();   /* PersonaStateChange_t: drop the cached persona name */
    EnterCriticalSection(&lock);
    for (int i = 0; i < cb_nregs && n < 64; i++) if (cb_regs[i].id == it->id) targets[n++] = cb_regs[i].cb;
    LeaveCriticalSection(&lock);
    if (it->id >= 0 && it->id < 4096) { sb_cb_count[it->id]++; sb_cb_listen[it->id] += (unsigned)n; }
    if (it->id >= 0 && it->id < 4096 && !cb_logged[it->id]) { cb_logged[it->id] = 1; sb_log("callback %d: %d bytes, %d listener(s)", it->id, it->len, n); }
    for (int i = 0; i < n; i++) {
        void **vt = *(void ***)targets[i];
        int want = ((cb_size_fn)vt[2])(targets[i]);
        uint8_t buf[512];
        if (want <= 0 || want > (int)sizeof buf) { sb_log("callback %d: listener declares %d bytes, skipped", it->id, want); continue; }
        memset(buf, 0, (size_t)want); memcpy(buf, it->data, (size_t)(it->len < want ? it->len : want));
        if (want != it->len) sb_log("callback %d: listener expects %d bytes, bridge has %d (zero-padded or truncated)", it->id, want, it->len);
        ((cb_run_fn)vt[1])(targets[i], buf);   /* outside the lock: Run may (un)register callbacks */
    }
}

API void SteamAPI_RunCallbacks(void) {
    cb_item batch[32]; int n = fetch_callbacks(batch, 32);
    for (int i = 0; i < n; i++) deliver(&batch[i]);
    if (cr_nregs) drive_call_results();
}

API void SteamAPI_ManualDispatch_Init(void) { cb_manual = 1; sb_log("ManualDispatch_Init"); }
API void SteamAPI_ManualDispatch_RunFrame(int pipe) {
    (void)pipe; cb_item batch[32]; int n = fetch_callbacks(batch, 32);
    EnterCriticalSection(&lock);
    for (int i = 0; i < n; i++) {
        if (cbq_count == 64) { sb_log("manual dispatch queue full, callback %d dropped", batch[i].id); continue; }
        cb_queue[(cbq_head + cbq_count) % 64] = batch[i]; cbq_count++;
    }
    LeaveCriticalSection(&lock);
    if (cr_nregs) drive_call_results();
}
/* CallbackMsg_t on Win64: HSteamUser at 0, int m_iCallback at 4, uint8 *m_pubParam at 8, int m_cubParam at 16. */
API int SteamAPI_ManualDispatch_GetNextCallback(int pipe, void *msg) {
    (void)pipe; if (!msg) return 0;
    EnterCriticalSection(&lock);
    if (!cbq_count) { LeaveCriticalSection(&lock); return 0; }
    cb_item *it = &cb_queue[cbq_head];   /* stays put until FreeLastCallback */
    LeaveCriticalSection(&lock);
    if (it->id >= 0 && it->id < 4096) sb_cb_count[it->id]++;
    if (it->id >= 0 && it->id < 4096 && !cb_logged[it->id]) { cb_logged[it->id] = 1; sb_log("callback %d: %d bytes, handed to manual dispatch", it->id, it->len); }
    uint8_t *m = msg;
    *(int32_t *)m = cb_hsteamuser; *(int32_t *)(m + 4) = it->id; *(uint8_t **)(m + 8) = it->data; *(int32_t *)(m + CBMSG_LEN_OFF) = it->len;
    return 1;
}
API void SteamAPI_ManualDispatch_FreeLastCallback(int pipe) {
    (void)pipe;
    EnterCriticalSection(&lock);
    if (cbq_count) { cbq_head = (cbq_head + 1) % 64; cbq_count--; }
    LeaveCriticalSection(&lock);
}

/* ---------- ISteamClient path ----------
 * Games built on the C++ header (Steamworks.NET included) do not call the SteamAPI_Steam<X>_vNNN
 * accessors. They take a user handle and a pipe handle, ask for ISteamClient, then fetch each interface
 * by version string. 2026-09-11: A Space for the Unbound under Wine called exactly this, which the stub
 * log caught. Both handles come from the helper so they mean something to the real client.
 * Handles are real objects (inert_stubs.c) rather than integer tokens, so a C++ caller reaches a vtable. */

/* Per-frame chatter caches (entry-05 2026-09-13: GetPersonaName 236/s, IsOverlayEnabled 118/s, GetHSteamPipe 60/s, each a
 * loopback round trip). Handles are constant for one helper session (keyed by the Init generation); the persona name is held
 * 2 s and dropped on PersonaStateChange_t; the overlay flag is held 1 s. Values still come from Steam, just less often. */
static LONG hpipe_gen = -1, huser_gen = -1; static int hpipe_cache, huser_cache;
API int SteamAPI_GetHSteamUser(void) {
    if (huser_gen == sb_init_counter) return huser_cache;
    CALL0(F_HSTEAMUSER) if (!ok) return 0;
    cb_hsteamuser = huser_cache = (int)br_u32(&r); huser_gen = sb_init_counter; return cb_hsteamuser;
}
API int SteamAPI_GetHSteamPipe(void) {
    if (hpipe_gen == sb_init_counter) return hpipe_cache;
    CALL0(F_HSTEAMPIPE) if (!ok) return 0;
    hpipe_cache = (int)br_u32(&r); hpipe_gen = sb_init_counter; return hpipe_cache;
}
API int GetHSteamUser(void) { return SteamAPI_GetHSteamUser(); }
API int GetHSteamPipe(void) { return SteamAPI_GetHSteamPipe(); }
/* The bare SteamClient() export is only called by SDK <= 1.36 games (later headers inline SteamInternal_CreateInterface with an
 * explicit version string), so it must hand out the oldest ISteamClient layout: SteamClient020 inserted a slot at 20, and entry-07
 * (compiled against 017) took slot 23 for GetISteamHTTP and landed on BShutdownIfAllPipesClosed (2026-09-13). */
API void *SteamClient(void) { return sb_vtable_handle(0xF0, "SteamClient017"); }
/* Same era, same rule: SDK <= 1.3x exported the interface accessors as functions and games import them by name (a 2015
 * steam_api.dll exports SteamUser, SteamUserStats, SteamApps ...). Missing exports mean the DLL does not load at all. */
API void *SteamUser(void) { return sb_vtable_handle(IF_USER, "SteamUser018"); }
API void *SteamUserStats(void) { return sb_vtable_handle(IF_USERSTATS, "STEAMUSERSTATS_INTERFACE_VERSION011"); }
API void *SteamApps(void) { return sb_vtable_handle(IF_APPS, "STEAMAPPS_INTERFACE_VERSION007"); }
API void *SteamFriends(void) { return sb_vtable_handle(IF_FRIENDS, "SteamFriends015"); }
API void *SteamUtils(void) { return sb_vtable_handle(IF_UTILS, "SteamUtils007"); }
API void *SteamRemoteStorage(void) { return sb_vtable_handle(IF_REMOTESTORAGE, "STEAMREMOTESTORAGE_INTERFACE_VERSION012"); }
API void Steam_RunCallbacks(void) { SteamAPI_RunCallbacks(); }
API int Steam_GetHSteamUserCurrent(void) { return SteamAPI_GetHSteamUser(); }
/* Pre-1.4x flat overload names (the JSON tables keep them as renamed_from) */
API int SteamAPI_ISteamUserStats_GetStatFloat(void *self, const char *name, float *out);
API int SteamAPI_ISteamUserStats_GetStatInt32(void *self, const char *name, int *out);
API int SteamAPI_ISteamUserStats_SetStatFloat(void *self, const char *name, float v);
API int SteamAPI_ISteamUserStats_SetStatInt32(void *self, const char *name, int v);
API int SteamAPI_ISteamUserStats_GetStat0(void *s, const char *n, float *v) { return SteamAPI_ISteamUserStats_GetStatFloat(s, n, v); }
API int SteamAPI_ISteamUserStats_GetStat(void *s, const char *n, int *v) { return SteamAPI_ISteamUserStats_GetStatInt32(s, n, v); }
API int SteamAPI_ISteamUserStats_SetStat0(void *s, const char *n, float v) { return SteamAPI_ISteamUserStats_SetStatFloat(s, n, v); }
API int SteamAPI_ISteamUserStats_SetStat(void *s, const char *n, int v) { return SteamAPI_ISteamUserStats_SetStatInt32(s, n, v); }

/* Served families are asked of the helper by version string, so the real client answers; a family the
 * helper cannot see stays NULL. Everything else is decided here and never reaches the helper: the game
 * gets an inert handle whose every method fails . The split happens before any socket traffic,
 * so a matchmaking or ticket request is not even a log line on the helper side. */
static void *iface_by_version(const char *ver) {
    char fam[64]; unsigned which = sb_family_token(ver, fam, sizeof fam);
    if (!which) return sb_inert_versioned(ver, fam);   /* inert family: exact-version all-stub table when we have one */
    buf_t q = {0}, r = {0}; bw_str(&q, ver);
    if (!sb_call(F_IFACE_BY_VERSION, &q, &r)) return NULL;
    if (!br_u8(&r)) { sb_log("interface \"%s\" not served", ver ? ver : "?"); return NULL; }
    unsigned got = br_u8(&r);
    sb_log("interface \"%s\" -> served handle %u", ver ? ver : "?", got);
    return sb_vtable_handle(got, ver);
}
API void *SteamInternal_CreateInterface(const char *ver) { return iface_by_version(ver); }
API void *SteamInternal_FindOrCreateUserInterface(int user, const char *ver) { (void)user; return iface_by_version(ver); }
/* steam_api.h since SDK 1.42: every inline accessor (SteamUser(), SteamUserStats(), ...) goes through here.
 * The argument is { init function, counter, storage... }. When the counter is stale the init function runs
 * once (it calls back into SteamInternal_FindOrCreateUserInterface for the interfaces it wants), then the
 * storage address is returned and the caller reads its interface pointer out of it. Returning NULL here
 * crashed every C++ SDK game at its first accessor call. */
API void *SteamInternal_ContextInit(void *pContextInitData) {
    void **d = (void **)pContextInitData;
    if (!d) return NULL;
    uintptr_t counter = (uintptr_t)(unsigned long)sb_init_counter;
    if ((uintptr_t)d[1] != counter) {
        void (*init)(void *) = (void (*)(void *))d[0];
        if (init) init(&d[2]);
        d[1] = (void *)counter;
    }
    return &d[2];
}
API void *SteamAPI_ISteamClient_GetISteamUserStats(void *c, int u, int p, const char *v) { (void)c;(void)u;(void)p; return iface_by_version(v); }
API void *SteamAPI_ISteamClient_GetISteamUser(void *c, int u, int p, const char *v) { (void)c;(void)u;(void)p; return iface_by_version(v); }
API void *SteamAPI_ISteamClient_GetISteamApps(void *c, int u, int p, const char *v) { (void)c;(void)u;(void)p; return iface_by_version(v); }
API void *SteamAPI_ISteamClient_GetISteamFriends(void *c, int u, int p, const char *v) { (void)c;(void)u;(void)p; return iface_by_version(v); }
/* Utils 只接 pipe，没有 user 参数。Win64 的版本字符串在 R8；误取 R9 会在 strlen 崩溃。
 * 签名依据与实际 Win64 回归见 tests/test_steambridge_abi.py。 */
API void *SteamAPI_ISteamClient_GetISteamUtils(void *c, int p, const char *v) { (void)c;(void)p; return iface_by_version(v); }
API void *SteamAPI_ISteamClient_GetISteamRemoteStorage(void *c, int u, int p, const char *v) { (void)c;(void)u;(void)p; return iface_by_version(v); }
/* The generic getter carries any version string (Steamworks.NET uses it for the networking interfaces);
 * it goes through the same served-or-inert split. */
API void *SteamAPI_ISteamClient_GetISteamGenericInterface(void *c, int u, int p, const char *v) { (void)c;(void)u;(void)p; return iface_by_version(v); }

/* ---------- interface accessors ----------
 * The real pointers live in the helper. We hand back a distinct non-null token per interface. */
static void *iface(int which, const char *version) {
    buf_t q = {0}, r = {0}; bw_u8(&q, (unsigned)which);
    if (!sb_call(F_IFACE, &q, &r)) return NULL;
    if (!br_u8(&r)) return NULL;
    return sb_vtable_handle((unsigned)which, version);
}
API void *SteamAPI_SteamUserStats_v012(void) { return iface(IF_USERSTATS, "STEAMUSERSTATS_INTERFACE_VERSION012"); }
API void *SteamAPI_SteamUser_v021(void) { return iface(IF_USER, "SteamUser021"); }
API void *SteamAPI_SteamApps_v008(void) { return iface(IF_APPS, "STEAMAPPS_INTERFACE_VERSION008"); }
API void *SteamAPI_SteamFriends_v017(void) { return iface(IF_FRIENDS, "SteamFriends017"); }
API void *SteamAPI_SteamUtils_v010(void) { return iface(IF_UTILS, "SteamUtils010"); }
API void *SteamAPI_SteamRemoteStorage_v016(void) { return iface(IF_REMOTESTORAGE, "STEAMREMOTESTORAGE_INTERFACE_VERSION016"); }
/* SDK 1.59+ accessors: the helper serves by family, so these forward the same way; a version without a C++ table
 * gets the family's inert vtable (flat calls still work). */
API void *SteamAPI_SteamUserStats_v013(void) { return iface(IF_USERSTATS, "STEAMUSERSTATS_INTERFACE_VERSION013"); }
API void *SteamAPI_SteamUser_v023(void) { return iface(IF_USER, "SteamUser023"); }
API void *SteamAPI_SteamApps_v009(void) { return iface(IF_APPS, "STEAMAPPS_INTERFACE_VERSION009"); }
API void *SteamAPI_SteamFriends_v018(void) { return iface(IF_FRIENDS, "SteamFriends018"); }
API void *SteamAPI_SteamUtils_v011(void) { return iface(IF_UTILS, "SteamUtils011"); }

/* ---------- ISteamUserStats ---------- */
API int SteamAPI_ISteamUserStats_RequestCurrentStats(void *self) { (void)self; CALL0(F_US_REQSTATS) return (int)br_u8(&r); }
/* Old SDKs (UserStats011, entry-06 2026-09-13) call this from the main menu. The real SteamAPICall_t is returned; its completion
 * reaches the game as the broadcast UserStatsReceived_t callback (CallResult objects are not driven by the bridge). */
API unsigned long long SteamAPI_ISteamUserStats_RequestUserStats(void *self, unsigned long long who) {
    (void)self; buf_t q = {0}, r = {0}; bw_u64(&q, who);
    if (!sb_call(F_US_REQUSERSTATS, &q, &r)) return 0;
    unsigned long long h = br_u64(&r);
    static int logged; if (!logged) { logged = 1; sb_log("RequestUserStats forwarded (call %llu); poll IsAPICallCompleted/GetAPICallResult or register a CCallResult, both are driven", h); }
    return h;
}
API int SteamAPI_ISteamUserStats_StoreStats(void *self) { (void)self; CALL0(F_US_STORE) return (int)br_u8(&r); }
API int SteamAPI_ISteamUserStats_GetNumAchievements(void *self) { (void)self; CALL0(F_US_NUMACH) return (int)br_u32(&r); }

API int SteamAPI_ISteamUserStats_GetAchievement(void *self, const char *name, unsigned char *achieved) {
    (void)self; buf_t q = {0}, res = {0}; bw_str(&q, name);
    if (!sb_call(F_US_GETACH, &q, &res)) return 0;
    int ok = (int)br_u8(&res); unsigned a = br_u8(&res);
    if (achieved) *achieved = (unsigned char)a;
    return ok;
}
API int SteamAPI_ISteamUserStats_SetAchievement(void *self, const char *name) {
    (void)self; buf_t q = {0}, r = {0}; bw_str(&q, name);
    if (!sb_call(F_US_SETACH, &q, &r)) return 0;
    int ok = (int)br_u8(&r); sb_log("SetAchievement %s -> %d", name ? name : "?", ok); return ok;
}
API int SteamAPI_ISteamUserStats_ClearAchievement(void *self, const char *name) {
    (void)self; buf_t q = {0}, r = {0}; bw_str(&q, name);
    if (!sb_call(F_US_CLEARACH, &q, &r)) return 0;
    return (int)br_u8(&r);
}
API const char *SteamAPI_ISteamUserStats_GetAchievementName(void *self, unsigned i) {
    (void)self; buf_t q = {0}, r = {0}; bw_u32(&q, i);
    char *out = next_retbuf(); out[0] = 0;
    if (sb_call(F_US_ACHNAME, &q, &r)) br_str(&r, out, 1024);
    return out;
}
API const char *SteamAPI_ISteamUserStats_GetAchievementDisplayAttribute(void *self, const char *name, const char *key) {
    (void)self; buf_t q = {0}, r = {0}; bw_str(&q, name); bw_str(&q, key);
    char *out = next_retbuf(); out[0] = 0;
    if (sb_call(F_US_ACHATTR, &q, &r)) br_str(&r, out, 1024);
    return out;
}
API int SteamAPI_ISteamUserStats_GetAchievementAndUnlockTime(void *self, const char *name, unsigned char *achieved, unsigned *when) {
    (void)self; buf_t q = {0}, r = {0}; bw_str(&q, name);
    if (!sb_call(F_US_ACHTIME, &q, &r)) return 0;
    int ok = (int)br_u8(&r); unsigned a = br_u8(&r), t = br_u32(&r);
    if (achieved) *achieved = (unsigned char)a;
    if (when) *when = t;
    return ok;
}
API int SteamAPI_ISteamUserStats_IndicateAchievementProgress(void *self, const char *name, unsigned cur, unsigned max) {
    (void)self; buf_t q = {0}, r = {0}; bw_str(&q, name); bw_u32(&q, cur); bw_u32(&q, max);
    if (!sb_call(F_US_PROGRESS, &q, &r)) return 0;
    return (int)br_u8(&r);
}
API int SteamAPI_ISteamUserStats_GetStatInt32(void *self, const char *name, int *out) {
    (void)self; buf_t q = {0}, r = {0}; bw_str(&q, name);
    if (!sb_call(F_US_GETSTATI, &q, &r)) return 0;
    int ok = (int)br_u8(&r); int v = (int)br_u32(&r);
    if (out) *out = v;
    return ok;
}
API int SteamAPI_ISteamUserStats_GetStatFloat(void *self, const char *name, float *out) {
    (void)self; buf_t q = {0}, r = {0}; bw_str(&q, name);
    if (!sb_call(F_US_GETSTATF, &q, &r)) return 0;
    int ok = (int)br_u8(&r); float v = br_f32(&r);
    if (out) *out = v;
    return ok;
}
API int SteamAPI_ISteamUserStats_SetStatInt32(void *self, const char *name, int v) {
    (void)self; buf_t q = {0}, r = {0}; bw_str(&q, name); bw_u32(&q, (unsigned)v);
    if (!sb_call(F_US_SETSTATI, &q, &r)) return 0;
    return (int)br_u8(&r);
}
API int SteamAPI_ISteamUserStats_SetStatFloat(void *self, const char *name, float v) {
    (void)self; buf_t q = {0}, r = {0}; bw_str(&q, name); bw_f32(&q, v);
    if (!sb_call(F_US_SETSTATF, &q, &r)) return 0;
    return (int)br_u8(&r);
}

/* ---------- ISteamUser / ISteamApps / ISteamFriends / ISteamUtils ---------- */
API unsigned long long SteamAPI_ISteamUser_GetSteamID(void *self) { (void)self; CALL0(F_USER_STEAMID) return br_u64(&r); }
API int SteamAPI_ISteamUser_BLoggedOn(void *self) { (void)self; CALL0(F_USER_LOGGEDON) return (int)br_u8(&r); }
API int SteamAPI_ISteamApps_BIsSubscribed(void *self) { (void)self; CALL0(F_APPS_SUBSCRIBED) return (int)br_u8(&r); }
API int SteamAPI_ISteamApps_BIsSubscribedFromFamilySharing(void *self) { (void)self; CALL0(F_APPS_FAMILY) return (int)br_u8(&r); }
API unsigned long long SteamAPI_ISteamApps_GetAppOwner(void *self) { (void)self; CALL0(F_APPS_OWNER) return br_u64(&r); }
API const char *SteamAPI_ISteamApps_GetCurrentGameLanguage(void *self) {
    (void)self; CALL0(F_APPS_LANG) char *o = next_retbuf(); o[0] = 0; if (ok) br_str(&r, o, 1024); return o;
}
static char persona_cache[1024]; static ULONGLONG persona_at; static LONG persona_gen = -1;
void sb_persona_changed(void) { persona_at = 0; }
API const char *SteamAPI_ISteamFriends_GetPersonaName(void *self) {
    (void)self;
    ULONGLONG now = GetTickCount64();
    if (persona_gen == sb_init_counter && persona_at && now - persona_at < 2000) return persona_cache;
    CALL0(F_FR_PERSONA) if (!ok) { char *o = next_retbuf(); o[0] = 0; return o; }
    br_str(&r, persona_cache, sizeof persona_cache); persona_at = now ? now : 1; persona_gen = sb_init_counter;
    return persona_cache;
}
API int SteamAPI_ISteamFriends_SetRichPresence(void *self, const char *k, const char *v) {
    (void)self; buf_t q = {0}, r = {0}; bw_str(&q, k); bw_str(&q, v);
    if (!sb_call(F_FR_RICH, &q, &r)) return 0;
    return (int)br_u8(&r);
}
API unsigned SteamAPI_ISteamUtils_GetAppID(void *self) { (void)self; CALL0(F_UTILS_APPID) return br_u32(&r); }
static ULONGLONG overlay_at; static LONG overlay_gen = -1; static int overlay_cache;
API int SteamAPI_ISteamUtils_IsOverlayEnabled(void *self) {
    (void)self;
    ULONGLONG now = GetTickCount64();
    if (overlay_gen == sb_init_counter && overlay_at && now - overlay_at < 1000) return overlay_cache;
    CALL0(F_UTILS_OVERLAY) if (!ok) return 0;
    overlay_cache = (int)br_u8(&r); overlay_at = now ? now : 1; overlay_gen = sb_init_counter; return overlay_cache;
}
/* Debug-message hook: nothing to forward, accepting it keeps the C++ slot quiet (was a logged stub on entry-06). */
API void SteamAPI_ISteamUtils_SetWarningMessageHook(void *self, void *hook) { (void)self; (void)hook; }
/* Text filtering, SDK >= 1.51. entry-05 (UE4) calls InitFilterText at startup; the pair is forwarded together so a
 * game that sees "filter ready" also gets real filtered text. If the helper is gone the input is passed through. */
API int SteamAPI_ISteamUtils_InitFilterText(void *self, unsigned opts) {
    (void)self; buf_t q = {0}, r = {0}; bw_u32(&q, opts);
    if (!sb_call(F_UTILS_INITFILTER, &q, &r)) return 0;
    int ok = (int)br_u8(&r); sb_log("InitFilterText(%u) -> %d", opts, ok); return ok;
}
API int SteamAPI_ISteamUtils_FilterText(void *self, int ctx, unsigned long long src, const char *in, char *out, unsigned cap) {
    (void)self;
    if (!out || cap == 0) return 0;
    buf_t q = {0}, r = {0}; bw_u32(&q, (unsigned)ctx); bw_u64(&q, src); bw_str(&q, in); bw_u32(&q, cap);
    char tmp[2304];
    unsigned n = 0;
    if (sb_call(F_UTILS_FILTERTEXT, &q, &r)) { n = br_u32(&r); br_str(&r, tmp, (int)sizeof tmp); }
    else { size_t k = in ? strlen(in) : 0; if (k >= sizeof tmp) k = sizeof tmp - 1; memcpy(tmp, in ? in : "", k); tmp[k] = 0; }
    size_t len = strlen(tmp); if (len >= cap) len = cap - 1;
    memcpy(out, tmp, len); out[len] = 0;
    return (int)n;
}

/* ---------- dll lifecycle ---------- */
BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID res) {
    (void)h; (void)res;
    if (reason == DLL_PROCESS_ATTACH) {
        InitializeCriticalSection(&lock);
        sb_t0 = sb_last_summary = GetTickCount64();
        sb_inert_init();
        sb_vtables_init();
        char path[MAX_PATH];
        DWORD n = GetEnvironmentVariableA("TEMP", path, sizeof path);
        if (!n || n >= sizeof path - 20) strcpy(path, "C:\\windows\\temp");
        strcat(path, "\\steambridge.log");
        logh = CreateFileA(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                           OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        sb_log("---- " SB_DLL_NAME " (steambridge) attached, pid %lu", (unsigned long)GetCurrentProcessId());
#if defined(__i386__)
        sb_log("32-bit bridge: flat API plus __thiscall C++ vtables (stack sizes from argsizes-i386.json; slots without one are logged as unsafe)");
#endif
        inited = 1;
    } else if (reason == DLL_PROCESS_DETACH && inited) {
        if (sock != INVALID_SOCKET) closesocket(sock);
        sb_summary("detach");
        sb_log("---- detached");
        HANDLE h = logh; logh = INVALID_HANDLE_VALUE;   /* mark closed before closing: no dangling handle */
        if (h != INVALID_HANDLE_VALUE) CloseHandle(h);
    }
    return TRUE;
}
