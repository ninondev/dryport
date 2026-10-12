// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* steambridge-helper: native macOS side. Loads Valve's libsteam_api.dylib from the Steam client's own
 * bundle at runtime and serves the Wine-side steam_api64.dll over a loopback socket. Runs as the same
 * user, talks only to 127.0.0.1.
 *
 * Why runtime loading from the Steam bundle and nowhere else: no Valve binary is shipped, built against,
 * or accepted from a path the user names. Without Steam installed the helper cannot start; without Steam
 * running and logged in, Valve's own init refuses. Every answer the bridge gives comes from that library.
 * The helper keeps no identity, ownership or achievement state of its own and has no offline mode.
 *
 * The bundled library is whatever SDK the current Steam client was built with, so nothing here depends
 * on one interface version: accessors are found by scanning SteamAPI_Steam<Family>_vNNN for the newest
 * NNN, and entry points that newer SDKs dropped (RequestCurrentStats, gone in 1.61) are optional.
 *
 * Build: cc -O2 -arch arm64 helper.c -o steambridge-helper
 * Run:   steambridge-helper <appid> [port]
 *
 * The first bytes of every connection must be the per-launch token from STEAMBRIDGE_TOKEN.
 * This verifies possession of that token, not process identity or isolation from other same-user programs.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <stdint.h>
#include <errno.h>
#include <fcntl.h>
#include <ctype.h>
#include <unistd.h>
#include <dlfcn.h>
#include <pwd.h>
#include <pthread.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <arpa/inet.h>
#include "proto.h"
#include "cbconv.h"

#include <signal.h>
#include <stdarg.h>
#include <sys/time.h>
#include <time.h>

/* Timestamp and count requests so repeated calls and callback delivery can be diagnosed. */
static void hlog(const char *fmt, ...) {
    struct timeval tv; gettimeofday(&tv, NULL); struct tm tm; localtime_r(&tv.tv_sec, &tm);
    char ts[32]; snprintf(ts, sizeof ts, "%02d:%02d:%02d.%03d", tm.tm_hour, tm.tm_min, tm.tm_sec, (int)(tv.tv_usec / 1000));
    va_list ap; va_start(ap, fmt);
    flockfile(stderr); fprintf(stderr, "[helper %s] ", ts); vfprintf(stderr, fmt, ap); funlockfile(stderr); fflush(stderr);
    va_end(ap);
}
static unsigned h_fid_count[64], h_fid_since_summary;
static unsigned h_cb_pushed;
static void h_summary(const char *why);


#define STEAM_API_IN_BUNDLE "/Library/Application Support/Steam/Steam.AppBundle/Steam/Contents/MacOS/" \
                            "Frameworks/Steam Helper.app/Contents/MacOS/libsteam_api.dylib"

typedef void *H;
static void *lib;

/* Function pointers carry the real names so call sites read like direct calls. */
static int (*SteamAPI_Init)(void);                                   /* SDK <= 1.58 */
static int (*SteamAPI_InitFlat)(char *);                             /* SDK >= 1.59, 0 = ok */
static int (*SteamInternal_SteamAPI_Init)(const char *, char *);     /* same, internal spelling */
static void (*SteamAPI_Shutdown)(void);
static void (*SteamAPI_RunCallbacks)(void);
static int (*SteamAPI_IsSteamRunning)(void);
static int (*SteamAPI_RestartAppIfNecessary)(uint32_t);
static int32_t (*SteamAPI_GetHSteamUser)(void);
static int32_t (*SteamAPI_GetHSteamPipe)(void);
static H (*acc_userstats)(void), (*acc_user)(void), (*acc_apps)(void), (*acc_friends)(void),
         (*acc_utils)(void), (*acc_remotestorage)(void);
static int (*SteamAPI_ISteamUserStats_RequestCurrentStats)(H);       /* NULL on SDK >= 1.61: stats arrive on their own */
static int (*SteamAPI_ISteamUserStats_StoreStats)(H);
static uint32_t (*SteamAPI_ISteamUserStats_GetNumAchievements)(H);
static const char *(*SteamAPI_ISteamUserStats_GetAchievementName)(H, uint32_t);
static int (*SteamAPI_ISteamUserStats_GetAchievement)(H, const char *, int *);
static int (*SteamAPI_ISteamUserStats_SetAchievement)(H, const char *);
static int (*SteamAPI_ISteamUserStats_ClearAchievement)(H, const char *);
static const char *(*SteamAPI_ISteamUserStats_GetAchievementDisplayAttribute)(H, const char *, const char *);
static int (*SteamAPI_ISteamUserStats_GetAchievementAndUnlockTime)(H, const char *, int *, uint32_t *);
static int (*SteamAPI_ISteamUserStats_IndicateAchievementProgress)(H, const char *, uint32_t, uint32_t);
static int (*SteamAPI_ISteamUserStats_GetStatInt32)(H, const char *, int32_t *);
static int (*SteamAPI_ISteamUserStats_GetStatFloat)(H, const char *, float *);
static int (*SteamAPI_ISteamUserStats_SetStatInt32)(H, const char *, int32_t);
static int (*SteamAPI_ISteamUserStats_SetStatFloat)(H, const char *, float);
static uint64_t (*SteamAPI_ISteamUser_GetSteamID)(H);
static int (*SteamAPI_ISteamUser_BLoggedOn)(H);
static int (*SteamAPI_ISteamApps_BIsSubscribed)(H);
static int (*SteamAPI_ISteamApps_BIsSubscribedFromFamilySharing)(H);
static uint64_t (*SteamAPI_ISteamApps_GetAppOwner)(H);
static const char *(*SteamAPI_ISteamApps_GetCurrentGameLanguage)(H);
static const char *(*SteamAPI_ISteamFriends_GetPersonaName)(H);
static int (*SteamAPI_ISteamFriends_SetRichPresence)(H, const char *, const char *);
static uint32_t (*SteamAPI_ISteamUtils_GetAppID)(H);
static int (*SteamAPI_ISteamUtils_IsOverlayEnabled)(H);
/* Text filtering (SDK >= 1.51): optional, older client libraries may lack them. Forwarded so a game that turns
 * filtering on gets Steam's real answer instead of a stub that silently drops every string. */
static int (*SteamAPI_ISteamUtils_InitFilterText)(H, uint32_t);
static int (*SteamAPI_ISteamUtils_FilterText)(H, int, uint64_t, const char *, char *, uint32_t);
/* Callback delivery (SDK >= 1.51). With manual dispatch Valve's library hands us every callback as raw bytes instead
 * of calling C++ objects, so the helper can forward them over the wire. All optional: without them callbacks stay
 * where they were, inside the helper. */
static void (*SteamAPI_ManualDispatch_Init)(void);
static void (*SteamAPI_ManualDispatch_RunFrame)(int32_t);
static int (*SteamAPI_ManualDispatch_GetNextCallback)(int32_t, void *);
static void (*SteamAPI_ManualDispatch_FreeLastCallback)(int32_t);
static int (*SteamAPI_ManualDispatch_GetAPICallResult)(int32_t, uint64_t, void *, int, int, int *);
static uint64_t (*SteamAPI_ISteamUserStats_RequestUserStats)(H, uint64_t);   /* the async stand-in for RequestCurrentStats */
/* Call results (2026-09-13): the game polls or registers a CCallResult for the handles we now return. */
static int (*SteamAPI_ISteamUtils_IsAPICallCompleted)(H, uint64_t, uint8_t *);
static int (*SteamAPI_ISteamUtils_GetAPICallFailureReason)(H, uint64_t);
static int (*SteamAPI_ISteamUtils_GetAPICallResult)(H, uint64_t, void *, int, int, uint8_t *);
static int manual_dispatch, manual_dispatch_on;

#define LOAD(name) do { *(void **)&name = dlsym(lib, #name); \
    if (!name) { hlog("Steam's libsteam_api.dylib lacks %s; this Steam build is not supported yet\n", #name); exit(3); } } while (0)
#define LOAD_OPT(name) do { *(void **)&name = dlsym(lib, #name); } while (0)

static H (*accessor(const char *family, int *ver))(void) {
    char n[96];
    for (int v = 99; v >= 1; v--) {
        snprintf(n, sizeof n, "SteamAPI_Steam%s_v%03d", family, v);
        void *p = dlsym(lib, n);
        if (p) { *ver = v; return (H (*)(void))p; }
    }
    hlog("no SteamAPI_Steam%s_vNNN accessor in Steam's libsteam_api.dylib\n", family);
    exit(3);
}

static void load_steam_api(void) {
    const char *home = getenv("HOME");
    if (!home || !*home) { struct passwd *pw = getpwuid(getuid()); home = pw ? pw->pw_dir : ""; }
    char path[1024]; snprintf(path, sizeof path, "%s%s", home, STEAM_API_IN_BUNDLE);
    lib = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!lib) {
        hlog("cannot load Steam's libsteam_api.dylib: %s\n[helper] expected at %s\n"
                        "         the bridge only works with the Steam client installed; it ships no copy of its own\n",
                dlerror(), path);
        exit(2);
    }
    LOAD_OPT(SteamAPI_Init); LOAD_OPT(SteamAPI_InitFlat); LOAD_OPT(SteamInternal_SteamAPI_Init);
    if (!SteamAPI_Init && !SteamAPI_InitFlat && !SteamInternal_SteamAPI_Init) {
        hlog("no init entry point in %s\n", path); exit(3);
    }
    LOAD(SteamAPI_Shutdown); LOAD(SteamAPI_RunCallbacks); LOAD(SteamAPI_IsSteamRunning);
    LOAD(SteamAPI_RestartAppIfNecessary); LOAD(SteamAPI_GetHSteamUser); LOAD(SteamAPI_GetHSteamPipe);
    int v_us, v_usr, v_apps, v_fr, v_ut, v_rs;
    acc_userstats = accessor("UserStats", &v_us);   acc_user = accessor("User", &v_usr);
    acc_apps = accessor("Apps", &v_apps);           acc_friends = accessor("Friends", &v_fr);
    acc_utils = accessor("Utils", &v_ut);           acc_remotestorage = accessor("RemoteStorage", &v_rs);
    LOAD_OPT(SteamAPI_ISteamUserStats_RequestCurrentStats);
    LOAD(SteamAPI_ISteamUserStats_StoreStats); LOAD(SteamAPI_ISteamUserStats_GetNumAchievements);
    LOAD(SteamAPI_ISteamUserStats_GetAchievementName); LOAD(SteamAPI_ISteamUserStats_GetAchievement);
    LOAD(SteamAPI_ISteamUserStats_SetAchievement); LOAD(SteamAPI_ISteamUserStats_ClearAchievement);
    LOAD(SteamAPI_ISteamUserStats_GetAchievementDisplayAttribute);
    LOAD(SteamAPI_ISteamUserStats_GetAchievementAndUnlockTime);
    LOAD(SteamAPI_ISteamUserStats_IndicateAchievementProgress);
    LOAD(SteamAPI_ISteamUserStats_GetStatInt32); LOAD(SteamAPI_ISteamUserStats_GetStatFloat);
    LOAD(SteamAPI_ISteamUserStats_SetStatInt32); LOAD(SteamAPI_ISteamUserStats_SetStatFloat);
    LOAD(SteamAPI_ISteamUser_GetSteamID); LOAD(SteamAPI_ISteamUser_BLoggedOn);
    LOAD(SteamAPI_ISteamApps_BIsSubscribed); LOAD(SteamAPI_ISteamApps_BIsSubscribedFromFamilySharing);
    LOAD(SteamAPI_ISteamApps_GetAppOwner); LOAD(SteamAPI_ISteamApps_GetCurrentGameLanguage);
    LOAD(SteamAPI_ISteamFriends_GetPersonaName); LOAD(SteamAPI_ISteamFriends_SetRichPresence);
    LOAD(SteamAPI_ISteamUtils_GetAppID); LOAD(SteamAPI_ISteamUtils_IsOverlayEnabled);
    LOAD_OPT(SteamAPI_ISteamUtils_InitFilterText); LOAD_OPT(SteamAPI_ISteamUtils_FilterText);
    LOAD_OPT(SteamAPI_ManualDispatch_Init); LOAD_OPT(SteamAPI_ManualDispatch_RunFrame);
    LOAD_OPT(SteamAPI_ManualDispatch_GetNextCallback); LOAD_OPT(SteamAPI_ManualDispatch_FreeLastCallback);
    LOAD_OPT(SteamAPI_ManualDispatch_GetAPICallResult); LOAD_OPT(SteamAPI_ISteamUserStats_RequestUserStats);
    LOAD_OPT(SteamAPI_ISteamUtils_IsAPICallCompleted); LOAD_OPT(SteamAPI_ISteamUtils_GetAPICallFailureReason); LOAD_OPT(SteamAPI_ISteamUtils_GetAPICallResult);
    manual_dispatch = SteamAPI_ManualDispatch_Init && SteamAPI_ManualDispatch_RunFrame &&
                      SteamAPI_ManualDispatch_GetNextCallback && SteamAPI_ManualDispatch_FreeLastCallback;
    /* SteamAPI_ManualDispatch_Init is switched on after a successful SteamAPI_Init (Valve's library refuses it
     * earlier: "must init library first"), see F_INIT. */
    hlog("Valve's libsteam_api.dylib loaded from the Steam client bundle: "
                    "UserStats v%03d User v%03d Apps v%03d Friends v%03d Utils v%03d RemoteStorage v%03d, "
                    "init via %s, RequestCurrentStats %s\n",
            v_us, v_usr, v_apps, v_fr, v_ut, v_rs,
            SteamAPI_InitFlat ? "SteamAPI_InitFlat" : SteamInternal_SteamAPI_Init ? "SteamInternal_SteamAPI_Init" : "SteamAPI_Init",
            SteamAPI_ISteamUserStats_RequestCurrentStats ? "present" : "absent (SDK >= 1.61)");
    fflush(stderr);
}

static int steam_init(void) {
    char err[1024] = {0}; int rc;
    if (SteamAPI_InitFlat) rc = SteamAPI_InitFlat(err);
    else if (SteamInternal_SteamAPI_Init) rc = SteamInternal_SteamAPI_Init(NULL, err);
    else return SteamAPI_Init();
    if (rc) { hlog("Steam refused init (%d): %s\n", rc, err); fflush(stderr); }
    return rc == 0;
}

/* Interfaces the bridge serves, by family name. Anything not in this table is refused by name:
 * matchmaking, networking, game server, inventory, auth tickets and the rest never get a handle. */
static const struct { const char *family; unsigned which; } served[] = {
    { "client", 0xF0 },                  /* ISteamClient itself: every call through it is intercepted in the DLL */
    { "userstats", IF_USERSTATS }, { "user", IF_USER }, { "apps", IF_APPS },
    { "friends", IF_FRIENDS }, { "utils", IF_UTILS }, { "remotestorage", IF_REMOTESTORAGE },
};

/* "SteamUserStats013" and "STEAMUSERSTATS_INTERFACE_VERSION013" both reduce to "userstats". */
static unsigned family_token(const char *ver, char *fam, size_t cap) {
    const char *p = ver; size_t i = 0;
    if (strncasecmp(p, "steam", 5) == 0) p += 5;
    for (; *p && isalpha((unsigned char)*p) && i < cap - 1; p++) fam[i++] = (char)tolower((unsigned char)*p);
    fam[i] = 0;
    for (size_t k = 0; k < sizeof served / sizeof *served; k++)
        if (!strcmp(fam, served[k].family)) return served[k].which;
    return 0;
}

static H us, usr, apps, fr, ut, rs;
static int steam_ready;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;

typedef struct { unsigned char b[4096]; int n, rp; } buf_t;
static void w8(buf_t *b, unsigned v) { if (b->n < (int)sizeof b->b) b->b[b->n++] = (unsigned char)v; }
static void w32(buf_t *b, uint32_t v) { for (int i = 0; i < 4; i++) w8(b, (v >> (8*i)) & 0xff); }
static void w64(buf_t *b, uint64_t v) { for (int i = 0; i < 8; i++) w8(b, (unsigned)((v >> (8*i)) & 0xff)); }
static void wf(buf_t *b, float f) { uint32_t u; memcpy(&u, &f, 4); w32(b, u); }
static void wstr(buf_t *b, const char *s) {
    uint32_t n = s ? (uint32_t)strlen(s) : 0; if (n > 2048) n = 2048;
    w32(b, n); for (uint32_t i = 0; i < n; i++) w8(b, (unsigned char)s[i]);
}
static unsigned r8(buf_t *b) { return b->rp < b->n ? b->b[b->rp++] : 0; }
static uint32_t r32(buf_t *b) { uint32_t v = 0; for (int i = 0; i < 4; i++) v |= (uint32_t)r8(b) << (8*i); return v; }
static uint64_t r64(buf_t *b) { uint64_t v = 0; for (int i = 0; i < 8; i++) v |= (uint64_t)r8(b) << (8*i); return v; }
static float rf(buf_t *b) { uint32_t u = r32(b); float f; memcpy(&f, &u, 4); return f; }
static char *rstr(buf_t *b, char *out, int cap) {
    uint32_t n = r32(b); int i = 0;
    for (uint32_t k = 0; k < n; k++) { unsigned c = r8(b); if (i < cap - 1) out[i++] = (char)c; }
    out[i] = 0; return out;
}

/* ---- callback queue ----
 * Drained from Valve's library on the pump thread and on every F_CB_DRAIN, converted to the Win64 layout right here
 * (cbconv.h), handed to the DLL when it asks. Callbacks without a known layout are dropped and logged once; the queue
 * is bounded and cleared on every Init so one game never sees another's leftovers. */
#define CBQ 64
typedef struct { int32_t id; int32_t len; uint8_t data[256]; } cb_item;
static cb_item cbq[CBQ]; static int cbq_head, cbq_count; static unsigned cb_dropped;
/* RequestUserStats handles (ours from init plus the game's own) whose completion becomes a UserStatsReceived_t callback.
 * CallResults are not delivered to the game (bridge design); the broadcast callback is what games register for. */
/* Steam broadcasts RequestUserStats results and retains them for game polling.
 * Do not synthesize a duplicate callback or consume the result here. */
static int cb_logged[4096];

static void cb_push(int32_t id, const uint8_t *win, int len) {
    if (cbq_count == CBQ) { cb_dropped++; return; }
    h_cb_pushed++;
    cb_item *it = &cbq[(cbq_head + cbq_count) % CBQ]; it->id = id; it->len = len; memcpy(it->data, win, (size_t)len); cbq_count++;
}

static void cb_convert_push(int32_t id, const uint8_t *mac, int maclen) {
    uint8_t win[256]; int n = sb_cb_convert(id, mac, maclen, win, sizeof win);
    if (n < 0) {
        if (id >= 0 && id < 4096 && !cb_logged[id]) {
            cb_logged[id] = 1;
            hlog("callback %d (%d bytes) not forwarded: %s\n", id, maclen,
                    n == -1 ? "no layout descriptor" : n == -3 ? "size does not match descriptor" : "too large");
            fflush(stderr);
        }
        return;
    }
    if (id >= 0 && id < 4096 && !cb_logged[id]) {
        const sb_cb_desc *d = sb_cb_find(id); cb_logged[id] = 1;
        hlog("callback %d %s: %d macOS bytes -> %d Win64 bytes, forwarding\n", id, d ? d->name : "?", maclen, n); fflush(stderr);
    }
    cb_push(id, win, n);
}

static void request_stats_async(void) {
    /* SDK 1.61 dropped RequestCurrentStats; games still wait for UserStatsReceived_t. Ask for the current user's stats
     * the modern way and turn the completed call into that callback. Read-only, data comes from Steam. */
    if (!us || !usr || !SteamAPI_ISteamUserStats_RequestUserStats) return;
    SteamAPI_ISteamUserStats_RequestUserStats(us, SteamAPI_ISteamUser_GetSteamID(usr));   /* Steam broadcasts the 1101 itself */
}

static void drain_callbacks(void) {                  /* caller holds mu */
    if (!steam_ready) return;
    if (!manual_dispatch_on) { SteamAPI_RunCallbacks(); return; }
    int32_t pipe = SteamAPI_GetHSteamPipe();
    SteamAPI_ManualDispatch_RunFrame(pipe);
    struct { int32_t user; int32_t id; uint8_t *param; int32_t len; } msg;   /* CallbackMsg_t, same offsets under pack 4 or 8 */
    while (SteamAPI_ManualDispatch_GetNextCallback(pipe, &msg)) {
        /* 703 (SteamAPICallCompleted_t) is forwarded like any other callback; the result itself stays with Steam until the
         * game polls GetAPICallResult or its CCallResult is driven. Fetching it here would consume it. */
        cb_convert_push(msg.id, msg.param, msg.len);
        SteamAPI_ManualDispatch_FreeLastCallback(pipe);
    }
}

static void h_summary(const char *why) {
    char buf[1000]; int n = snprintf(buf, sizeof buf, "summary (%s) calls fid:count", why);
    for (int f = 0; f < 64 && n < (int)sizeof buf - 16; f++) if (h_fid_count[f]) n += snprintf(buf + n, sizeof buf - n, " %d:%u", f, h_fid_count[f]);
    hlog("%s\n", buf);
    hlog("summary (%s) callbacks pushed %u dropped %u\n", why, h_cb_pushed, cb_dropped);
    h_fid_since_summary = 0;
}

static void on_term(int sig) { h_summary(sig == SIGTERM ? "SIGTERM" : "SIGINT"); _exit(0); }

static void *pump(void *arg) {                 /* keep Steam's callbacks flowing */
    (void)arg;
    int ticks = 0;
    for (;;) {
        if (steam_ready) { pthread_mutex_lock(&mu); drain_callbacks(); pthread_mutex_unlock(&mu); }
        if (++ticks >= 600) { ticks = 0; if (h_fid_since_summary) h_summary("60 s"); }
        usleep(100000);
    }
}

/* Constant-time equality: the token is a shared secret, so the comparison must not exit early and leak
 * how many bytes matched. */
static int ct_eq(const char *a, const char *b, size_t n) {
    unsigned char d = 0;
    for (size_t i = 0; i < n; i++) d |= (unsigned char)a[i] ^ (unsigned char)b[i];
    return d == 0;
}

static void handle(int fid, buf_t *q, buf_t *r) {
    char a[2304], b[2304];
    int ok; uint32_t u; int32_t i32; float f; int ach;
    if (fid >= 0 && fid < 64) { h_fid_count[fid]++; h_fid_since_summary++; }
    switch (fid) {
    case F_HELLO: w8(r, 1); break;
    case F_INIT: {
        u = r32(q);
        if (u) { char s[32]; snprintf(s, sizeof s, "%u", u); setenv("SteamAppId", s, 1); setenv("SteamGameId", s, 1); }
        ok = steam_init();
        if (ok) {
            steam_ready = 1;
            us = acc_userstats(); usr = acc_user(); apps = acc_apps();
            fr = acc_friends(); ut = acc_utils(); rs = acc_remotestorage();
            if (manual_dispatch && !manual_dispatch_on) { SteamAPI_ManualDispatch_Init(); manual_dispatch_on = 1; }
            cbq_head = cbq_count = 0; cb_dropped = 0;   /* no leftovers from a previous game */
            if (us && SteamAPI_ISteamUserStats_RequestCurrentStats) SteamAPI_ISteamUserStats_RequestCurrentStats(us);
            else request_stats_async();
        }
        hlog("SteamAPI_Init appid=%u -> %d (callback forwarding %s)\n", u, ok,
                manual_dispatch_on ? "on" : "off: this Steam build lacks manual dispatch"); fflush(stderr);
        w8(r, ok ? 1 : 0); break;
    }
    case F_SHUTDOWN: break;                    /* keep the helper alive for the next launch */
    case F_RUNCB: drain_callbacks(); break;    /* old DLLs: pump only; new DLLs use F_CB_DRAIN */
    case F_ISRUNNING: w8(r, SteamAPI_IsSteamRunning() ? 1 : 0); break;
    case F_RESTART:                            /* Valve's answer; with SteamAppId set (main does) it is "no" */
        u = r32(q); w8(r, u && SteamAPI_RestartAppIfNecessary(u) ? 1 : 0); break;
    case F_IFACE: {
        unsigned which = r8(q);
        H h = which == IF_USERSTATS ? us : which == IF_USER ? usr : which == IF_APPS ? apps :
              which == IF_FRIENDS ? fr : which == IF_UTILS ? ut : rs;
        w8(r, h ? 1 : 0); break;
    }
    case F_US_REQSTATS:                        /* absent on new SDKs: fetch asynchronously and answer with the callback */
        if (us && !SteamAPI_ISteamUserStats_RequestCurrentStats) request_stats_async();
        w8(r, us && (SteamAPI_ISteamUserStats_RequestCurrentStats ? SteamAPI_ISteamUserStats_RequestCurrentStats(us) : 1)); break;
    case F_US_REQUSERSTATS: {                  /* the game's own request; completion becomes a UserStatsReceived_t callback */
        uint64_t who = r64(q), h = 0;
        /* Steam broadcasts UserStatsReceived_t itself when this completes and keeps the result for the game's own polling;
         * the helper neither fetches nor re-broadcasts it to avoid duplicate notifications. */
        if (us && SteamAPI_ISteamUserStats_RequestUserStats) h = SteamAPI_ISteamUserStats_RequestUserStats(us, who);
        hlog("RequestUserStats(%llu) -> call %llu\n", (unsigned long long)who, (unsigned long long)h); fflush(stderr);
        w64(r, h); break; }
    case F_US_STORE:    w8(r, us && SteamAPI_ISteamUserStats_StoreStats(us)); break;
    case F_US_NUMACH:   w32(r, us ? SteamAPI_ISteamUserStats_GetNumAchievements(us) : 0); break;
    case F_US_GETACH:
        rstr(q, a, sizeof a); ach = 0;
        ok = us && SteamAPI_ISteamUserStats_GetAchievement(us, a, &ach);
        w8(r, ok ? 1 : 0); w8(r, ach ? 1 : 0); break;
    case F_US_SETACH:
        rstr(q, a, sizeof a);
        ok = us && SteamAPI_ISteamUserStats_SetAchievement(us, a);
        if (ok) SteamAPI_ISteamUserStats_StoreStats(us);
        hlog("SetAchievement %s -> %d\n", a, ok); fflush(stderr);
        w8(r, ok ? 1 : 0); break;
    case F_US_CLEARACH:
        rstr(q, a, sizeof a);
        ok = us && SteamAPI_ISteamUserStats_ClearAchievement(us, a);
        if (ok) SteamAPI_ISteamUserStats_StoreStats(us);
        w8(r, ok ? 1 : 0); break;
    case F_US_ACHNAME:
        u = r32(q); wstr(r, us ? SteamAPI_ISteamUserStats_GetAchievementName(us, u) : ""); break;
    case F_US_ACHATTR:
        rstr(q, a, sizeof a); rstr(q, b, sizeof b);
        wstr(r, us ? SteamAPI_ISteamUserStats_GetAchievementDisplayAttribute(us, a, b) : ""); break;
    case F_US_ACHTIME: {
        rstr(q, a, sizeof a); ach = 0; uint32_t t = 0;
        ok = us && SteamAPI_ISteamUserStats_GetAchievementAndUnlockTime(us, a, &ach, &t);
        w8(r, ok ? 1 : 0); w8(r, ach ? 1 : 0); w32(r, t); break;
    }
    case F_US_PROGRESS: {
        rstr(q, a, sizeof a); uint32_t c = r32(q), m = r32(q);
        w8(r, us && SteamAPI_ISteamUserStats_IndicateAchievementProgress(us, a, c, m)); break;
    }
    case F_US_GETSTATI:
        rstr(q, a, sizeof a); i32 = 0;
        ok = us && SteamAPI_ISteamUserStats_GetStatInt32(us, a, &i32);
        w8(r, ok ? 1 : 0); w32(r, (uint32_t)i32); break;
    case F_US_GETSTATF:
        rstr(q, a, sizeof a); f = 0;
        ok = us && SteamAPI_ISteamUserStats_GetStatFloat(us, a, &f);
        w8(r, ok ? 1 : 0); wf(r, f); break;
    case F_US_SETSTATI:
        rstr(q, a, sizeof a); i32 = (int32_t)r32(q);
        w8(r, us && SteamAPI_ISteamUserStats_SetStatInt32(us, a, i32)); break;
    case F_US_SETSTATF:
        rstr(q, a, sizeof a); f = rf(q);
        w8(r, us && SteamAPI_ISteamUserStats_SetStatFloat(us, a, f)); break;
    case F_USER_STEAMID:  w64(r, usr ? SteamAPI_ISteamUser_GetSteamID(usr) : 0); break;
    case F_USER_LOGGEDON: w8(r, usr && SteamAPI_ISteamUser_BLoggedOn(usr)); break;
    case F_APPS_SUBSCRIBED: w8(r, apps && SteamAPI_ISteamApps_BIsSubscribed(apps)); break;
    case F_APPS_FAMILY:     w8(r, apps && SteamAPI_ISteamApps_BIsSubscribedFromFamilySharing(apps)); break;
    case F_APPS_OWNER:      w64(r, apps ? SteamAPI_ISteamApps_GetAppOwner(apps) : 0); break;
    case F_APPS_LANG:       wstr(r, apps ? SteamAPI_ISteamApps_GetCurrentGameLanguage(apps) : ""); break;
    case F_FR_PERSONA:      wstr(r, fr ? SteamAPI_ISteamFriends_GetPersonaName(fr) : ""); break;
    case F_FR_RICH:
        rstr(q, a, sizeof a); rstr(q, b, sizeof b);
        w8(r, fr && SteamAPI_ISteamFriends_SetRichPresence(fr, a, b)); break;
    case F_UTILS_APPID:   w32(r, ut ? SteamAPI_ISteamUtils_GetAppID(ut) : 0); break;
    case F_UTILS_OVERLAY: w8(r, ut && SteamAPI_ISteamUtils_IsOverlayEnabled(ut)); break;
    case F_UT_ISCALLDONE: {
        uint64_t h = r64(q); uint8_t failed = 0; int done = 0;
        if (ut && SteamAPI_ISteamUtils_IsAPICallCompleted && h) done = SteamAPI_ISteamUtils_IsAPICallCompleted(ut, h, &failed);
        w8(r, done ? 1 : 0); w8(r, failed ? 1 : 0); break; }
    case F_UT_CALLFAIL: {
        uint64_t h = r64(q);
        w32(r, (ut && SteamAPI_ISteamUtils_GetAPICallFailureReason && h) ? (uint32_t)SteamAPI_ISteamUtils_GetAPICallFailureReason(ut, h) : 0xFFFFFFFFu); break; }
    case F_UT_CALLRESULT: {
        /* Result structs need the same relayout as callbacks; ids without a descriptor are refused, not guessed. */
        uint64_t h = r64(q); int expected = (int)r32(q); uint8_t failed = 0; int ok = 0;
        int macsz = sb_cb_size(expected, 0); uint8_t res[256], win[256]; int n = 0;
        if (ut && SteamAPI_ISteamUtils_GetAPICallResult && h && macsz > 0 && macsz <= (int)sizeof res) {
            ok = SteamAPI_ISteamUtils_GetAPICallResult(ut, h, res, macsz, expected, &failed);
            if (ok && !failed) n = sb_cb_convert(expected, res, macsz, win, (int)sizeof win);
            if (n < 0) { ok = 0; n = 0; }
        } else if (expected >= 0 && expected < 4096 && !cb_logged[expected]) {
            cb_logged[expected] = 1;
            hlog("call result %d not forwarded: %s\n", expected, macsz > 0 ? "utils interface unavailable" : "no layout descriptor"); fflush(stderr);
        }
        w8(r, ok ? 1 : 0); w8(r, failed ? 1 : 0); w32(r, (uint32_t)n); for (int i = 0; i < n; i++) w8(r, win[i]); break; }
    case F_UTILS_INITFILTER:
        u = r32(q);
        ok = ut && SteamAPI_ISteamUtils_InitFilterText && SteamAPI_ISteamUtils_InitFilterText(ut, u);
        hlog("InitFilterText(%u) -> %d\n", u, ok); fflush(stderr);
        w8(r, ok ? 1 : 0); break;
    case F_UTILS_FILTERTEXT: {
        /* Without the client-side filter the text goes back unchanged: never blank a game's strings. */
        u = r32(q); uint64_t src = r64(q); rstr(q, a, sizeof a); uint32_t cap = r32(q);
        char out[2304]; int n = 0;
        if (cap > sizeof out) cap = sizeof out;
        if (ut && SteamAPI_ISteamUtils_FilterText && cap) {
            out[0] = 0;
            n = SteamAPI_ISteamUtils_FilterText(ut, (int)u, src, a, out, cap);
            if (n < 0) n = 0;
        } else {
            snprintf(out, sizeof out, "%s", a);
        }
        w32(r, (uint32_t)n); wstr(r, out); break; }
    case F_CB_DRAIN: {                         /* hand over what fits; the rest waits for the next call */
        drain_callbacks();
        int n = 0; int save = r->n; w8(r, 0);
        while (cbq_count && n < 255) {
            cb_item *it = &cbq[cbq_head];
            if (r->n + 8 + it->len > (int)sizeof r->b) break;
            w32(r, (uint32_t)it->id); w32(r, (uint32_t)it->len);
            for (int k = 0; k < it->len; k++) w8(r, it->data[k]);
            cbq_head = (cbq_head + 1) % CBQ; cbq_count--; n++;
        }
        r->b[save] = (unsigned char)n;
        break;
    }
    case F_HSTEAMUSER: w32(r, (uint32_t)SteamAPI_GetHSteamUser()); break;
    case F_HSTEAMPIPE: w32(r, (uint32_t)SteamAPI_GetHSteamPipe()); break;
    case F_IFACE_BY_VERSION: {
        /* Games built on the C++ header fetch interfaces through ISteamClient with a version string.
         * Reduce it to the family and serve only an exact match from the table, so a request for
         * "SteamNetworkingUtils004" gets NULL, never a mislabeled ISteamUtils. */
        rstr(q, a, sizeof a);
        char fam[64]; unsigned which = family_token(a, fam, sizeof fam);
        H h = which == 0xF0 ? (H)1 : which == IF_USERSTATS ? us : which == IF_USER ? usr : which == IF_APPS ? apps :
              which == IF_FRIENDS ? fr : which == IF_UTILS ? ut : which == IF_REMOTESTORAGE ? rs : NULL;
        hlog("interface \"%s\" (%s) -> %s\n", a, fam, h ? "ok" : "refused"); fflush(stderr);
        w8(r, h ? 1 : 0); w8(r, which); break;
    }
    default: hlog("unknown fid %d\n", fid); fflush(stderr); break;
    }
}

/* Socket work is bounded independently of Steam calls. Pending tokens are read concurrently;
 * at most four unauthenticated peers and eight total peers are retained. */
#define MAX_PEERS 8
#define MAX_PENDING 4
#define IO_DEADLINE_MS 2000
#define IDLE_DEADLINE_MS 30000
enum phase { TOKEN, HEADER, PAYLOAD, REPLY };
typedef struct {
    int fd, authenticated, used, wanted, fid, output_used, output_size;
    enum phase phase;
    int64_t deadline, born;
    unsigned char input[4096], output[4100];
} peer_t;

static int64_t monotonic_ms(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) { perror("clock_gettime"); exit(1); }
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static void close_peer(peer_t *peer, int64_t *last_active) {
    close(peer->fd); peer->fd = -1;
    if (peer->authenticated) {
        *last_active = monotonic_ms();
        pthread_mutex_lock(&mu); h_summary("client gone"); pthread_mutex_unlock(&mu);
        hlog("client gone\n");
    }
}

static void answer_peer(peer_t *peer) {
    buf_t q = {{0}, 0, 0}, r = {{0}, 0, 0};
    q.n = peer->used;
    memcpy(q.b, peer->input, (size_t)q.n);
    pthread_mutex_lock(&mu); handle(peer->fid, &q, &r); pthread_mutex_unlock(&mu);
    for (int i = 0; i < 4; i++) peer->output[i] = (unsigned char)((r.n >> (8*i)) & 0xff);
    memcpy(peer->output + 4, r.b, (size_t)r.n);
    peer->output_size = r.n + 4; peer->output_used = 0;
    peer->phase = REPLY; peer->deadline = monotonic_ms() + IO_DEADLINE_MS;
}

static int read_peer(peer_t *peer, const char *token, size_t token_len, int *had_client) {
    ssize_t got = recv(peer->fd, peer->input + peer->used, (size_t)(peer->wanted - peer->used), MSG_DONTWAIT);
    if (got < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) return 1;
    if (got <= 0) return 0;
    if (peer->phase == HEADER && peer->used == 0)
        peer->deadline = monotonic_ms() + IO_DEADLINE_MS;
    peer->used += (int)got;
    if (peer->used != peer->wanted) return 1;
    if (peer->phase == TOKEN) {
        if (!ct_eq((const char *)peer->input, token, token_len)) {
            hlog("client rejected: token missing or wrong, connection closed\n"); return 0;
        }
        peer->authenticated = 1; *had_client = 1;
        hlog("client connected with a valid token\n");
        peer->phase = HEADER; peer->used = 0; peer->wanted = 6;
        peer->deadline = monotonic_ms() + IO_DEADLINE_MS;
    } else if (peer->phase == HEADER) {
        peer->fid = peer->input[0] | (peer->input[1] << 8);
        uint32_t n = peer->input[2] | (peer->input[3] << 8) | (peer->input[4] << 16) | ((uint32_t)peer->input[5] << 24);
        if (n > sizeof peer->input) return 0;
        peer->phase = PAYLOAD; peer->used = 0; peer->wanted = (int)n;
        /* The header and payload share one deadline; trickled bytes never refresh it. */
        if (!n) answer_peer(peer);
    } else {
        answer_peer(peer);
    }
    return 1;
}

static int write_peer(peer_t *peer) {
    ssize_t sent = send(peer->fd, peer->output + peer->output_used,
                        (size_t)(peer->output_size - peer->output_used), MSG_DONTWAIT);
    if (sent < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) return 1;
    if (sent <= 0) return 0;
    peer->output_used += (int)sent;
    if (peer->output_used == peer->output_size) {
        peer->phase = HEADER; peer->used = 0; peer->wanted = 6;
        /* A completed client may idle, but a partial next frame still has only two seconds. */
        peer->deadline = monotonic_ms() + IDLE_DEADLINE_MS;
    }
    return 1;
}

static void serve(int ls, const char *token, size_t token_len, int linger) {
    peer_t peers[MAX_PEERS];
    for (int i = 0; i < MAX_PEERS; i++) peers[i].fd = -1;
    int had_client = 0;
    int64_t last_active = monotonic_ms();
    for (;;) {
        fd_set reads, writes; FD_ZERO(&reads); FD_ZERO(&writes); FD_SET(ls, &reads);
        int max_fd = ls, active = 0;
        int64_t now = monotonic_ms();
        for (int i = 0; i < MAX_PEERS; i++) {
            peer_t *peer = &peers[i];
            if (peer->fd < 0) continue;
            if (now >= peer->deadline) { close_peer(peer, &last_active); continue; }
            active += peer->authenticated;
            FD_SET(peer->fd, peer->phase == REPLY ? &writes : &reads);
            if (peer->fd > max_fd) max_fd = peer->fd;
        }
        if (had_client && !active && linger > 0 && now - last_active >= (int64_t)linger * 1000) {
            hlog("no client for %d s, exiting\n", linger); return;
        }
        struct timeval timeout = {0, 50000};
        int ready = select(max_fd + 1, &reads, &writes, NULL, &timeout);
        if (ready < 0) { if (errno == EINTR) continue; perror("select"); return; }
        /* Existing clients get service before accepting more arrivals. */
        for (int i = 0; i < MAX_PEERS; i++) {
            peer_t *peer = &peers[i];
            if (peer->fd < 0) continue;
            int ok = 1;
            if (FD_ISSET(peer->fd, &reads)) ok = read_peer(peer, token, token_len, &had_client);
            else if (FD_ISSET(peer->fd, &writes)) ok = write_peer(peer);
            if (!ok) close_peer(peer, &last_active);
        }
        if (!FD_ISSET(ls, &reads)) continue;
        /* A fixed accept budget prevents an arrival stream from starving ready clients. */
        for (int accepted = 0; accepted < MAX_PEERS; accepted++) {
            int fd = accept(ls, NULL, NULL);
            if (fd < 0) break;
            if (fd >= FD_SETSIZE) { close(fd); continue; }
            int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
            int pending = 0, oldest = -1, slot = -1;
            for (int i = 0; i < MAX_PEERS; i++) {
                if (peers[i].fd < 0) { if (slot < 0) slot = i; continue; }
                if (!peers[i].authenticated) {
                    pending++;
                    if (oldest < 0 || peers[i].born < peers[oldest].born) oldest = i;
                }
            }
            if (pending >= MAX_PENDING || (slot < 0 && oldest >= 0)) {
                close_peer(&peers[oldest], &last_active); slot = oldest;
            }
            if (slot < 0) { close(fd); continue; }
            peer_t *peer = &peers[slot]; memset(peer, 0, sizeof *peer);
            peer->fd = fd; peer->phase = TOKEN; peer->wanted = (int)token_len;
            peer->born = monotonic_ms(); peer->deadline = peer->born + IO_DEADLINE_MS;
        }
    }
}

int main(int argc, char **argv) {
    uint32_t appid = argc > 1 ? (uint32_t)atoi(argv[1]) : 0;
    int port = argc > 2 ? atoi(argv[2]) : SB_PORT_DEFAULT;
    if (!appid) { fprintf(stderr, "usage: steambridge-helper <appid> [port]\n"); return 1; }
    /* 启动器把同一个随机令牌放进 helper 与 Wine 的环境。连接须先证明持有令牌，
     * 这不验证游戏进程身份或同用户隔离。先复制出来，避免后面的 setenv 移动环境块。 */
    static char token[256];
    const char *env_token = getenv("STEAMBRIDGE_TOKEN");
    size_t token_len = env_token ? strlen(env_token) : 0;
    if (token_len == 0) {
        hlog("no STEAMBRIDGE_TOKEN in the environment: refusing to start\n");
        fflush(stderr);
        return 1;
    }
    if (token_len >= sizeof token) { hlog("STEAMBRIDGE_TOKEN is %zu bytes, too long; refusing to start\n", token_len); fflush(stderr); return 1; }
    memcpy(token, env_token, token_len + 1);
    load_steam_api();
    { char s[32]; snprintf(s, sizeof s, "%u", appid); setenv("SteamAppId", s, 1); setenv("SteamGameId", s, 1); }

    int ls = socket(AF_INET, SOCK_STREAM, 0);
    int one = 1; setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in sa; memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET; sa.sin_port = htons((uint16_t)port);
    sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);   /* loopback only */
    if (bind(ls, (struct sockaddr *)&sa, sizeof sa) || listen(ls, 4)) { perror("bind/listen"); return 1; }
    hlog("listening on 127.0.0.1:%d appid=%u\n", port, appid); fflush(stderr);

    pthread_t th; pthread_create(&th, NULL, pump, NULL); pthread_detach(th);
    signal(SIGTERM, on_term); signal(SIGINT, on_term);

    /* One helper per game; rejected clients do not arm or refresh the linger timer. */
    int linger = getenv("STEAMBRIDGE_LINGER") ? atoi(getenv("STEAMBRIDGE_LINGER")) : 15;
    if (fcntl(ls, F_SETFL, O_NONBLOCK) < 0) { perror("fcntl"); return 1; }
    signal(SIGPIPE, SIG_IGN);
    serve(ls, token, token_len, linger);
    return 0;
}
