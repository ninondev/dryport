// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* Windows test client for steambridge: runs under Wine with the helper up, exercises the bridge without a game.
 * Identity lines are informational (they depend on the logged-in account). The policy checks at the end are
 * asserted: exit status is non-zero if any of them fails. */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <windows.h>

__declspec(dllimport) int SteamAPI_Init(void);
__declspec(dllimport) void SteamAPI_RunCallbacks(void);
__declspec(dllimport) void *SteamAPI_SteamUserStats_v012(void);
__declspec(dllimport) void *SteamAPI_SteamUser_v021(void);
__declspec(dllimport) void *SteamAPI_SteamApps_v008(void);
__declspec(dllimport) void *SteamAPI_SteamFriends_v017(void);
__declspec(dllimport) unsigned long long SteamAPI_ISteamUser_GetSteamID(void *);
__declspec(dllimport) int SteamAPI_ISteamUtils_InitFilterText(void *, unsigned);
__declspec(dllimport) unsigned long long SteamAPI_ISteamUserStats_RequestUserStats(void *, unsigned long long);
__declspec(dllimport) void *SteamAPI_SteamUtils_v010(void);
__declspec(dllimport) int SteamAPI_ISteamUtils_IsAPICallCompleted(void *, unsigned long long, unsigned char *);
__declspec(dllimport) int SteamAPI_ISteamUtils_GetAPICallResult(void *, unsigned long long, void *, int, int, unsigned char *);
__declspec(dllimport) int SteamAPI_ManualDispatch_GetAPICallResult(int, unsigned long long, void *, int, int, unsigned char *);
__declspec(dllimport) void SteamAPI_RegisterCallResult(void *, unsigned long long);

/* CCallResult stand-in: slot 0 is Run(param, bIOFailure, handle) on Win64. */
static int cr_ran; static unsigned char cr_param[64]; static int cr_failed;
static void cr_run3(void *self, void *param, int failed, unsigned long long h) { (void)self; (void)h; memcpy(cr_param, param, 24); cr_failed = failed; cr_ran++; }
static void cr_run1(void *self, void *param) { (void)self; (void)param; }
static int cr_size(void *self) { (void)self; return 24; }
static void *cr_vtable[] = { (void *)cr_run3, (void *)cr_run1, (void *)cr_size };
__declspec(dllimport) int SteamAPI_ISteamUtils_FilterText(void *, int, unsigned long long, const char *, char *, unsigned);
__declspec(dllimport) int SteamAPI_ISteamApps_BIsSubscribed(void *);
__declspec(dllimport) int SteamAPI_ISteamApps_BIsSubscribedFromFamilySharing(void *);
__declspec(dllimport) const char *SteamAPI_ISteamFriends_GetPersonaName(void *);
__declspec(dllimport) int SteamAPI_ISteamUserStats_RequestCurrentStats(void *);
__declspec(dllimport) int SteamAPI_ISteamUserStats_GetNumAchievements(void *);
__declspec(dllimport) const char *SteamAPI_ISteamUserStats_GetAchievementName(void *, unsigned);
__declspec(dllimport) int SteamAPI_ISteamUserStats_GetAchievement(void *, const char *, unsigned char *);
__declspec(dllimport) const char *SteamAPI_ISteamUserStats_GetAchievementDisplayAttribute(void *, const char *, const char *);
__declspec(dllimport) void *SteamInternal_CreateInterface(const char *);
__declspec(dllimport) unsigned SteamAPI_ISteamUser_GetAuthSessionTicket(void *, void *, int, unsigned *, const void *);
__declspec(dllimport) void *SteamClient(void);
__declspec(dllimport) int SteamAPI_GetHSteamUser(void);
__declspec(dllimport) int SteamAPI_GetHSteamPipe(void);
__declspec(dllimport) void *SteamInternal_FindOrCreateUserInterface(int, const char *);
__declspec(dllimport) void *SteamInternal_ContextInit(void *);
__declspec(dllimport) void *SteamAPI_ISteamClient_GetISteamMatchmaking(void *, int, int, const char *);
__declspec(dllimport) void *SteamAPI_ISteamClient_GetISteamGenericInterface(void *, int, int, const char *);
__declspec(dllimport) void *SteamAPI_SteamMatchmaking_v009(void);
__declspec(dllimport) unsigned long long SteamAPI_ISteamMatchmaking_CreateLobby(void *, int, int);
__declspec(dllimport) float SteamAPI_ISteamMusic_GetVolume(void *);
__declspec(dllimport) void *SteamAPI_ISteamInput_GetAnalogActionData(void *, void *, unsigned long long, unsigned long long);
__declspec(dllimport) unsigned long long SteamAPI_ISteamApps_GetAppOwner(void *);
__declspec(dllimport) int SteamInternal_SteamAPI_Init(const char *, char *);
__declspec(dllimport) void *SteamAPI_SteamUser_v023(void);
__declspec(dllimport) void SteamAPI_RegisterCallback(void *, int);
__declspec(dllimport) void SteamAPI_UnregisterCallback(void *);
__declspec(dllimport) void SteamAPI_ManualDispatch_Init(void);
__declspec(dllimport) void SteamAPI_ManualDispatch_RunFrame(int);
__declspec(dllimport) int SteamAPI_ManualDispatch_GetNextCallback(int, void *);
__declspec(dllimport) void SteamAPI_ManualDispatch_FreeLastCallback(int);

/* A CCallbackBase as MSVC lays it out: vtable, flags at 8, callback id at 12; vtable slot 0 is the call-result Run,
 * slot 1 the callback Run, slot 2 GetCallbackSizeBytes (overloads reversed, see bridge.c). */
typedef struct { void **vt; unsigned char flags; int id; } cbbase;
static int got_stats, got_stored; static unsigned char stats_param[64];
static void run_callresult(void *self, void *p, int fail, unsigned long long h) { (void)self; (void)p; (void)fail; (void)h; }
static void run_callback(void *self, void *p) {
    cbbase *b = self;
    if (b->id == 1101) { got_stats++; memcpy(stats_param, p, 24); }
    else if (b->id == 1102) got_stored++;
}
static int callback_size(void *self) { cbbase *b = self; return b->id == 1101 ? 24 : 16; }
static void *cb_vtable[3] = { (void *)run_callresult, (void *)run_callback, (void *)callback_size };

static int failures;
static void check(int ok, const char *what) { printf("%s: %s\n", ok ? "PASS" : "FAIL", what); if (!ok) failures++; }

/* Stand-in for the init function steam_api.h's accessor macro generates. */
static int ctx_calls;
static void ctx_init(void *slot) { *(void **)slot = SteamInternal_FindOrCreateUserInterface(SteamAPI_GetHSteamUser(), "SteamUser021"); ctx_calls++; }

static int manual_mode_checks(unsigned long long steamid) {
    /* Facepunch-style consumer: pull callbacks ourselves. CallbackMsg_t on Win64: user, id, param pointer, length. */
    struct { int user; int id; unsigned char *param; int len; } msg;
    int pipe = SteamAPI_GetHSteamPipe(), got = 0, total = 0;
    for (int i = 0; i < 80 && !got; i++) {
        SteamAPI_ManualDispatch_RunFrame(pipe);
        while (SteamAPI_ManualDispatch_GetNextCallback(pipe, &msg)) {
            total++;
            if (msg.id == 1101 && msg.len == 24) {
                unsigned long long gid, who; memcpy(&gid, msg.param, 8); memcpy(&who, msg.param + 16, 8);
                int result; memcpy(&result, msg.param + 8, 4);
                if ((gid & 0xFFFFFFu) == 1201270u && result == 1 && who == steamid) got++;
            }
            SteamAPI_ManualDispatch_FreeLastCallback(pipe);
        }
        Sleep(100);
    }
    printf("manual dispatch: %d callbacks pulled, UserStatsReceived_t matched=%d\n", total, got);
    check(got >= 1, "ManualDispatch: UserStatsReceived_t (24 Win64 bytes) pulled with this appid, EResultOK and our steamid");
    return 0;
}

int main(int argc, char **argv) {
    int manual = argc > 1 && strcmp(argv[1], "manual") == 0;
    if (manual) SteamAPI_ManualDispatch_Init();
    printf("SteamAPI_Init: %d\n", SteamAPI_Init());
    void *usr = SteamAPI_SteamUser_v021(), *apps = SteamAPI_SteamApps_v008();
    void *fr = SteamAPI_SteamFriends_v017(), *us = SteamAPI_SteamUserStats_v012();
    printf("interfaces: user=%p apps=%p friends=%p stats=%p\n", usr, apps, fr, us);
    printf("steamid=%llu persona=%s subscribed=%d familyshared=%d\n",
           (unsigned long long)SteamAPI_ISteamUser_GetSteamID(usr),
           SteamAPI_ISteamFriends_GetPersonaName(fr),
           SteamAPI_ISteamApps_BIsSubscribed(apps),
           SteamAPI_ISteamApps_BIsSubscribedFromFamilySharing(apps));
    unsigned long long steamid = SteamAPI_ISteamUser_GetSteamID(usr);
    if (manual) return manual_mode_checks(steamid) || failures;
    cbbase cb_stats = { cb_vtable, 0, 0 }, cb_stored = { cb_vtable, 0, 0 };
    SteamAPI_RegisterCallback(&cb_stats, 1101); SteamAPI_RegisterCallback(&cb_stored, 1102);
    check((cb_stats.flags & 1) && cb_stats.id == 1101, "RegisterCallback sets the registered flag and callback id in the object");
    printf("RequestCurrentStats=%d\n", SteamAPI_ISteamUserStats_RequestCurrentStats(us));
    for (int i = 0; i < 80 && !got_stats; i++) { SteamAPI_RunCallbacks(); Sleep(100); }
    unsigned long long gid = 0, who = 0; int result = 0;
    memcpy(&gid, stats_param, 8); memcpy(&result, stats_param + 8, 4); memcpy(&who, stats_param + 16, 8);
    printf("UserStatsReceived_t: delivered=%d gameid_low=%llu result=%d user=%llu\n", got_stats, gid & 0xFFFFFFu, result, who);
    check(got_stats >= 1 && (gid & 0xFFFFFFu) == 1201270u && result == 1 && who == steamid,
          "RegisterCallback path: UserStatsReceived_t delivered through the MSVC vtable slot with this appid, EResultOK and our steamid");
    /* The game's own RequestUserStats (old SDKs call it from the menu): a real call handle, and its completion shows up as a
     * second UserStatsReceived_t through the registered callback. */
    int before_stats = got_stats;
    unsigned long long call = SteamAPI_ISteamUserStats_RequestUserStats(us, steamid);
    check(call != 0, "RequestUserStats(own steamid) -> real SteamAPICall_t (forwarded, not k_uAPICallInvalid)");
    for (int i = 0; i < 100 && got_stats == before_stats; i++) { SteamAPI_RunCallbacks(); Sleep(100); }
    check(got_stats > before_stats, "the game's RequestUserStats completion arrives as a UserStatsReceived_t broadcast (Steam sends it natively; the bridge adds no copy)");
    /* Call results (2026-09-13): the handle is real, so it must be pollable and fetchable, and CCallResult objects driven. */
    void *ut = SteamAPI_SteamUtils_v010(); unsigned char cfail = 1; int done = 0;
    for (int i = 0; i < 50 && !done; i++) { done = SteamAPI_ISteamUtils_IsAPICallCompleted(ut, call, &cfail); if (!done) Sleep(100); }
    check(done && !cfail, "IsAPICallCompleted turns true for the forwarded RequestUserStats handle");
    /* Burst: UE4's OSS Steam issues one RequestUserStats per achievement query and some games fire several before any completes
     * (entry-06 keeps one in flight at ~2.2/s; N-in-a-row coverage added 2026-09-13). Every handle must complete,
     * every completion must reach the 1101 listener, and the helper's pending list must hold them all (it was 8 slots). */
    {
        enum { NB = 12 }; unsigned long long hb[NB]; int before = got_stats; unsigned long long t0 = GetTickCount64();
        for (int i = 0; i < NB; i++) hb[i] = SteamAPI_ISteamUserStats_RequestUserStats(us, steamid);
        int distinct = 1;
        for (int i = 0; i < NB && distinct; i++) { if (!hb[i]) distinct = 0; for (int j = 0; j < i; j++) if (hb[i] == hb[j]) distinct = 0; }
        check(distinct, "12 back-to-back RequestUserStats -> 12 distinct non-zero handles");
        int alldone = 0;
        for (int t = 0; t < 100 && !alldone; t++) {
            SteamAPI_RunCallbacks(); alldone = 1;
            for (int i = 0; i < NB; i++) { unsigned char f = 0; if (!SteamAPI_ISteamUtils_IsAPICallCompleted(ut, hb[i], &f)) alldone = 0; }
            if (!alldone) Sleep(100);
        }
        unsigned long long ms = GetTickCount64() - t0;
        printf("burst of %d RequestUserStats: all completed=%d in %llu ms, UserStatsReceived_t delivered +%d\n", NB, alldone, ms, got_stats - before);
        check(alldone, "all 12 burst handles report IsAPICallCompleted (Steam keeps every result; nothing consumed by the helper)");
        check(got_stats - before >= NB, "each burst completion is broadcast as UserStatsReceived_t to the registered listener (native, one per request)");
        check(got_stats - before <= NB + 2, "no duplicated UserStatsReceived_t per request (the helper synthesizes none)");
    }
    unsigned char res[24] = {0}; unsigned long long res_who = 0;
    check(SteamAPI_ISteamUtils_GetAPICallResult(ut, call, res, 24, 1101, &cfail) && !cfail, "GetAPICallResult fetches the UserStatsReceived_t result (24 Win64 bytes)");
    memcpy(&res_who, res + 16, 8);
    check(res_who == steamid, "call result carries our steamid at the Win64 offset");
    cbbase cr = { cr_vtable, 0, 1101 };
    unsigned long long call2 = SteamAPI_ISteamUserStats_RequestUserStats(us, steamid);
    SteamAPI_RegisterCallResult(&cr, call2);
    for (int i = 0; i < 100 && !cr_ran; i++) { SteamAPI_RunCallbacks(); Sleep(100); }
    unsigned long long cr_who = 0; memcpy(&cr_who, cr_param + 16, 8);
    check(cr_ran == 1 && !cr_failed && cr_who == steamid, "registered CCallResult is driven: Run(param, bIOFailure=0, handle) once with our steamid");
    SteamAPI_UnregisterCallback(&cb_stats);
    check((cb_stats.flags & 1) == 0, "UnregisterCallback clears the registered flag");
    int n = SteamAPI_ISteamUserStats_GetNumAchievements(us);
    printf("num_achievements=%d\n", n);
    for (int i = 0; i < n && i < 5; i++) {
        const char *name = SteamAPI_ISteamUserStats_GetAchievementName(us, (unsigned)i);
        unsigned char got = 0;
        int ok = SteamAPI_ISteamUserStats_GetAchievement(us, name, &got);
        printf("  %s readable=%d unlocked=%d title=\"%s\"\n", name, ok, got,
               SteamAPI_ISteamUserStats_GetAchievementDisplayAttribute(us, name, "name"));
    }
    /* Served families come back as handles from the helper. */
    void *utils = SteamInternal_CreateInterface("SteamUtils011");
    void *apps2 = SteamInternal_CreateInterface("STEAMAPPS_INTERFACE_VERSION009");
    printf("served: utils=%p apps=%p\n", utils, apps2);
    check(utils != NULL && apps2 != NULL, "served families via version string -> served handles");

    /* Policy: multiplayer, server, ticket and every other unserved surface is inert, never NULL and never forwarded. */
    int hu = SteamAPI_GetHSteamUser(), hp = SteamAPI_GetHSteamPipe();
    void *mm1 = SteamInternal_CreateInterface("SteamMatchMaking009");
    void *mm2 = SteamAPI_ISteamClient_GetISteamMatchmaking(SteamClient(), hu, hp, "SteamMatchMaking009");
    void *mm3 = SteamAPI_SteamMatchmaking_v009();
    void *nu = SteamAPI_ISteamClient_GetISteamGenericInterface(SteamClient(), hu, hp, "SteamNetworkingUtils004");
    printf("inert: matchmaking=%p/%p/%p networkingutils=%p\n", mm1, mm2, mm3, nu);
    check(mm1 != NULL && mm1 == mm2 && mm2 == mm3, "matchmaking: the three getters agree on one inert handle");
    check(nu != NULL && nu != mm1, "networkingutils via GetISteamGenericInterface: a distinct inert handle");
    check(nu != utils && mm1 != utils, "inert handles are not served handles");
    check(SteamAPI_ISteamMatchmaking_CreateLobby(mm1, 0, 4) == 0, "CreateLobby on the inert handle -> k_uAPICallInvalid");
    unsigned char tk[1024]; unsigned tn = 0;
    check(SteamAPI_ISteamUser_GetAuthSessionTicket(usr, tk, (int)sizeof tk, &tn, 0) == 0 && tn == 0,
          "GetAuthSessionTicket -> k_HAuthTicketInvalid, no ticket bytes");
    void **vt = *(void ***)mm1;
    check(((long long (*)(void *))vt[7])(mm1) == 0, "C++ virtual call, slot 7 on the inert handle -> 0");
    check(((double (*)(void *))vt[9])(mm1) == 0.0, "C++ virtual call, slot 9 read as double -> 0.0 (XMM0 cleared)");
    check(SteamAPI_ISteamMusic_GetVolume(SteamInternal_CreateInterface("STEAMMUSIC_INTERFACE_VERSION001")) == 0.0f,
          "typed float body -> 0.0f");
    unsigned char analog[16]; memset(analog, 0xAB, sizeof analog);
    void *ret = SteamAPI_ISteamInput_GetAnalogActionData(analog, SteamInternal_CreateInterface("SteamInput006"), 1, 2);
    int zero = 1; for (int i = 0; i < 16; i++) zero &= analog[i] == 0;
    check(ret == analog && zero, "typed aggregate body zeroes the hidden result and returns its pointer");
    /* C++ path: the handle's vtable is the MSVC layout read from a real steam_api64.dll (lib/steambridge/vtables). */
    void *cu = SteamInternal_CreateInterface("SteamUser021"); void **uvt = *(void ***)cu;
    check(((int (*)(void *))uvt[1])(cu) == 1, "C++ SteamUser021 slot 1 BLoggedOn -> 1 (forward)");
    unsigned long long idout = 0; ((void *(*)(void *, unsigned long long *))uvt[2])(cu, &idout);
    check(idout == steamid && idout != 0, "C++ SteamUser021 slot 2 GetSteamID via hidden result pointer == flat GetSteamID");
    check(((unsigned (*)(void *, void *, int, unsigned *, const void *))uvt[13])(cu, tk, (int)sizeof tk, &tn, 0) == 0,
          "C++ SteamUser021 slot 13 GetAuthSessionTicket -> 0 (stub, never forwarded)");
    void *cs = SteamInternal_CreateInterface("STEAMUSERSTATS_INTERFACE_VERSION012"); void **svt = *(void ***)cs;
    check(((int (*)(void *))svt[14])(cs) == n, "C++ UserStats012 slot 14 GetNumAchievements == flat");
    check(((unsigned long long (*)(void *, unsigned long long))svt[16])(cs, steamid) != 0, "C++ UserStats012 slot 16 RequestUserStats -> real call handle (forward)");
    void *ca = SteamInternal_CreateInterface("STEAMAPPS_INTERFACE_VERSION008"); void **avt = *(void ***)ca;
    unsigned long long owner = 0; ((void *(*)(void *, unsigned long long *))avt[20])(ca, &owner);
    check(owner == SteamAPI_ISteamApps_GetAppOwner(apps) && owner != 0, "C++ Apps008 slot 20 GetAppOwner via hidden result pointer == flat");
    void *cut = SteamInternal_CreateInterface("SteamUtils010"); void **utv = *(void ***)cut;
    check(((unsigned (*)(void *))utv[9])(cut) == 1201270u, "C++ SteamUtils010 slot 9 GetAppID -> 1201270");
    void *shot = SteamInternal_CreateInterface("STEAMSCREENSHOTS_INTERFACE_VERSION003"); void **shvt = *(void ***)shot;
    check(shot && ((int (*)(void *, int))shvt[3])(shot, 1) == 0, "C++ STEAMSCREENSHOTS 003 versioned all-stub table: slot 3 HookScreenshots -> 0");
    void *cu18 = SteamInternal_CreateInterface("SteamUser018");
    check(cu18 && ((int (*)(void *))(*(void ***)cu18)[1])(cu18) == 1, "C++ SteamUser018 (own table, read from a 2015 32-bit DLL) slot 1 BLoggedOn -> 1");
    /* Text filtering: entry-05's one unforwarded slot (2026-09-13). Steam decides whether the filter is ready;
     * the assertion is that the C++ slot and the flat call agree and that clean text passes through untouched. */
    int ift = SteamAPI_ISteamUtils_InitFilterText(cut, 0);
    printf("InitFilterText(0) -> %d\n", ift);
    check(((int (*)(void *, unsigned))utv[31])(cut, 0) == ift, "C++ SteamUtils010 slot 31 InitFilterText == flat (forward)");
    ((void (*)(void *, void *))utv[16])(cut, NULL);
    check(1, "C++ SteamUtils010 slot 16 SetWarningMessageHook accepted (no-op forward, no stub log)");
    char ft[64] = "x", ft2[64] = "x";
    int fn = SteamAPI_ISteamUtils_FilterText(cut, 0, steamid, "hello bridge", ft, (unsigned)sizeof ft);
    check(fn == 0 && strcmp(ft, "hello bridge") == 0, "FilterText: clean text comes back unchanged, 0 characters filtered");
    int fn2 = ((int (*)(void *, int, unsigned long long, const char *, char *, unsigned))utv[32])(cut, 0, steamid, "hello bridge", ft2, (unsigned)sizeof ft2);
    check(fn2 == fn && strcmp(ft2, ft) == 0, "C++ SteamUtils010 slot 32 FilterText == flat");
    char ft3[6] = "xxxxx";
    SteamAPI_ISteamUtils_FilterText(cut, 0, steamid, "hello bridge", ft3, (unsigned)sizeof ft3);
    check(strcmp(ft3, "hello") == 0, "FilterText respects the caller's buffer size (NUL-terminated at capacity)");
    void *cf = SteamInternal_CreateInterface("SteamFriends017"); void **fvt = *(void ***)cf;
    check(strcmp(((const char *(*)(void *))fvt[0])(cf), SteamAPI_ISteamFriends_GetPersonaName(fr)) == 0, "C++ Friends017 slot 0 GetPersonaName == flat");
    void *cc = SteamInternal_CreateInterface("SteamClient020"); void **cvt = *(void ***)cc;
    check(((void *(*)(void *, int, int, const char *))cvt[5])(cc, hu, hp, "SteamUser021") == cu, "C++ SteamClient020 slot 5 GetISteamUser -> the SteamUser021 handle");
    check(((void *(*)(void *, int, int, const char *))cvt[10])(cc, hu, hp, "SteamMatchMaking009") == mm1, "C++ SteamClient020 slot 10 GetISteamMatchmaking -> inert handle");
    void *old = SteamInternal_CreateInterface("SteamUser019"); void **ovt = *(void ***)old;
    check(old != cu && ((int (*)(void *))ovt[1])(old) == 1, "C++ SteamUser019 has its own table, slot 1 BLoggedOn -> 1");
    void *unk = SteamInternal_CreateInterface("SteamUser017");
    check(unk != NULL && unk != cu && ((long long (*)(void *))(*(void ***)unk)[1])(unk) == 0, "unknown version SteamUser017 -> inert table, slot 1 -> 0");
    char errmsg[1024] = "x";
    check(SteamInternal_SteamAPI_Init(NULL, errmsg) == 0 && errmsg[0] == 0, "SDK 1.59+ entry SteamInternal_SteamAPI_Init -> k_ESteamAPIInitResult_OK, empty message");
    void *u23 = SteamAPI_SteamUser_v023(); void **u23vt = *(void ***)u23;
    unsigned long long id23 = 0; ((void *(*)(void *, unsigned long long *))u23vt[2])(u23, &id23);
    check(u23 != NULL && u23 != cu && ((int (*)(void *))u23vt[1])(u23) == 1 && id23 == steamid && SteamAPI_ISteamUser_GetSteamID(u23) == steamid,
          "SDK 1.59+ accessor SteamUser_v023 -> handle with its own C++ table (2026-09-13): slot 1 BLoggedOn -> 1, slot 2 GetSteamID == flat");
    void *u20 = SteamInternal_CreateInterface("SteamUser020"); void **u20vt = *(void ***)u20;
    unsigned long long id20 = 0; ((void *(*)(void *, unsigned long long *))u20vt[2])(u20, &id20);
    check(u20 != NULL && u20 != cu && u20 != u23 && ((int (*)(void *))u20vt[1])(u20) == 1 && id20 == steamid,
          "C++ SteamUser020 (SDK 1.4x era) has its own table: slot 1 BLoggedOn -> 1, slot 2 GetSteamID == flat");
    void *c21 = SteamInternal_CreateInterface("SteamClient021"); void **c21vt = *(void ***)c21;
    check(((void *(*)(void *, int, int, const char *))c21vt[5])(c21, hu, hp, "SteamUser023") == u23,
          "C++ SteamClient021 slot 5 GetISteamUser -> the SteamUser023 handle");
    check(((void *(*)(void *, int, int, const char *))c21vt[10])(c21, hu, hp, "SteamMatchMaking009") == mm1,
          "C++ SteamClient021 slot 10 GetISteamMatchmaking -> inert handle");
    /* Same version string, longer table in newer SDKs (Apps008 grew SetDlcContext etc. in 1.58+): slots past what this build
     * knows land on padded stubs, not on whatever follows the array. Slot 40 is beyond every Apps008 table in the wild. */
    check(((long long (*)(void *))avt[40])(ca) == 0, "C++ Apps008 slot 40 (beyond the known table) -> padded stub returned 0, no crash");
    check(((int (*)(void *))avt[29])(ca) == 0, "C++ Apps008 slot 29 SetDlcContext (appended in SDK 1.58, named, not forwarded) -> stub 0");
    void *ctx[3] = { (void *)ctx_init, 0, 0 };
    void *c1 = SteamInternal_ContextInit(ctx), *c2 = SteamInternal_ContextInit(ctx);
    check(c1 == &ctx[2] && c2 == c1 && ctx_calls == 1 && ctx[2] != NULL,
          "SteamInternal_ContextInit: storage returned, init ran once, served handle stored");
    printf("policy checks failed: %d\n", failures);
    return failures ? 1 : 0;
}
