// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* 32-bit bridge test client: flat API only (Steamworks.NET / Facepunch shape). Built with i686-w64-mingw32-gcc against
 * steam_api.dll, run under Wine's wow64 with the helper listening. Win32 conventions: flat exports are __cdecl,
 * CCallbackBase members are __thiscall (vtable @0, flags @4, id @8), callback structs are pack(4). */
#include <stdio.h>
#include <string.h>
#include <windows.h>

__declspec(dllimport) int SteamAPI_Init(void);
__declspec(dllimport) void SteamAPI_RunCallbacks(void);
__declspec(dllimport) void *SteamAPI_SteamUserStats_v012(void);
__declspec(dllimport) void *SteamAPI_SteamUser_v021(void);
__declspec(dllimport) void *SteamAPI_SteamApps_v008(void);
__declspec(dllimport) void *SteamAPI_SteamFriends_v017(void);
__declspec(dllimport) void *SteamAPI_SteamUtils_v010(void);
__declspec(dllimport) unsigned long long SteamAPI_ISteamUser_GetSteamID(void *);
__declspec(dllimport) int SteamAPI_ISteamUser_BLoggedOn(void *);
__declspec(dllimport) int SteamAPI_ISteamApps_BIsSubscribed(void *);
__declspec(dllimport) const char *SteamAPI_ISteamFriends_GetPersonaName(void *);
__declspec(dllimport) unsigned SteamAPI_ISteamUtils_GetAppID(void *);
__declspec(dllimport) int SteamAPI_ISteamUserStats_RequestCurrentStats(void *);
__declspec(dllimport) unsigned long long SteamAPI_ISteamUserStats_RequestUserStats(void *, unsigned long long);
__declspec(dllimport) int SteamAPI_ISteamUserStats_GetNumAchievements(void *);
__declspec(dllimport) const char *SteamAPI_ISteamUserStats_GetAchievementName(void *, unsigned);
__declspec(dllimport) int SteamAPI_ISteamUtils_IsAPICallCompleted(void *, unsigned long long, unsigned char *);
__declspec(dllimport) int SteamAPI_ISteamUtils_GetAPICallResult(void *, unsigned long long, void *, int, int, unsigned char *);
__declspec(dllimport) void SteamAPI_RegisterCallback(void *, int);
__declspec(dllimport) void SteamAPI_UnregisterCallback(void *);
__declspec(dllimport) void SteamAPI_RegisterCallResult(void *, unsigned long long);
__declspec(dllimport) void *SteamInternal_CreateInterface(const char *);
__declspec(dllimport) unsigned SteamAPI_ISteamMatchmaking_CreateLobby(void *, int, int);
__declspec(dllimport) void SteamAPI_ManualDispatch_Init(void);
__declspec(dllimport) void SteamAPI_ManualDispatch_RunFrame(int);
__declspec(dllimport) int SteamAPI_ManualDispatch_GetNextCallback(int, void *);
__declspec(dllimport) void SteamAPI_ManualDispatch_FreeLastCallback(int);
__declspec(dllimport) int SteamAPI_GetHSteamPipe(void);
__declspec(dllimport) unsigned long long SteamAPI_ISteamApps_GetAppOwner(void *);
__declspec(dllimport) void *SteamClient(void);
__declspec(dllimport) void *SteamUserStats(void);
__declspec(dllimport) void *SteamScreenshots(void);
__declspec(dllimport) void *SteamUGC(void);
__declspec(dllimport) void Steam_RunCallbacks(void);
__declspec(dllimport) int Steam_GetHSteamUserCurrent(void);
__declspec(dllimport) int SteamAPI_GetHSteamUser(void);
__declspec(dllimport) void *SteamAPI_ISteamClient_GetISteamScreenshots(void *, int, int, const char *);
__declspec(dllimport) void *SteamAPI_SteamMatchmaking_v009(void);
static void **sb_inert_vtable_probe(void) { return *(void ***)SteamAPI_SteamMatchmaking_v009(); }   /* matchmaking009 has a table too; compare by identity only */

/* x86 member functions are __thiscall: this in ecx, arguments on the stack, cleaned by the callee */
typedef int (__attribute__((thiscall)) *tc_int)(void *);
typedef unsigned (__attribute__((thiscall)) *tc_uint)(void *);
typedef void *(__attribute__((thiscall)) *tc_hidden_u64)(void *, unsigned long long *);
typedef unsigned long long (__attribute__((thiscall)) *tc_u64_u64)(void *, unsigned long long);
typedef void *(__attribute__((thiscall)) *tc_getiface)(void *, int, int, const char *);
typedef unsigned (__attribute__((thiscall)) *tc_ticket)(void *, void *, int, unsigned *);   /* SteamUser021: 3 arguments (the identity pointer came with SteamUser023) */
typedef int (__attribute__((thiscall)) *tc_ach)(void *, const char *, unsigned char *);
typedef int (__attribute__((thiscall)) *tc_int_bool)(void *, int);

static int failures;
static void check(int ok, const char *what) { printf("%s: %s\n", ok ? "PASS" : "FAIL", what); if (!ok) failures++; }

/* CCallbackBase on Win32 */
typedef struct { void **vt; unsigned char flags; int id; } cbbase;
static int got_stats; static unsigned char stats_param[64];
static void __attribute__((thiscall)) run3(void *self, void *param, int failed, unsigned long long h) { (void)self; (void)param; (void)failed; (void)h; }
static void __attribute__((thiscall)) run1(void *self, void *param) { (void)self; memcpy(stats_param, param, 20); got_stats++; }
static int __attribute__((thiscall)) size20(void *self) { (void)self; return 20; }   /* UserStatsReceived_t is 20 bytes under pack(4) */
static void *cb_vtable[] = { (void *)run3, (void *)run1, (void *)size20 };

static int cr_ran; static unsigned char cr_param[64]; static int cr_failed;
static void __attribute__((thiscall)) cr_run3(void *self, void *param, int failed, unsigned long long h) { (void)self; (void)h; memcpy(cr_param, param, 20); cr_failed = failed; cr_ran++; }
static void *cr_vtable[] = { (void *)cr_run3, (void *)run1, (void *)size20 };

int main(int argc, char **argv) {
    int manual = argc > 1 && strcmp(argv[1], "manual") == 0;
    if (manual) SteamAPI_ManualDispatch_Init();
    check(sizeof(void *) == 4, "this client is 32-bit");
    int init = SteamAPI_Init();
    printf("SteamAPI_Init: %d\n", init);
    check(init == 1, "32-bit SteamAPI_Init -> 1 (helper reached through steam_api.dll)");
    void *usr = SteamAPI_SteamUser_v021(), *apps = SteamAPI_SteamApps_v008(), *fr = SteamAPI_SteamFriends_v017();
    void *us = SteamAPI_SteamUserStats_v012(), *ut = SteamAPI_SteamUtils_v010();
    unsigned long long steamid = SteamAPI_ISteamUser_GetSteamID(usr);
    printf("steamid=%llu persona=%s subscribed=%d appid=%u\n", steamid, SteamAPI_ISteamFriends_GetPersonaName(fr),
           SteamAPI_ISteamApps_BIsSubscribed(apps), SteamAPI_ISteamUtils_GetAppID(ut));
    check(steamid != 0 && SteamAPI_ISteamUser_BLoggedOn(usr) == 1, "identity: steamid and logged-on through the flat API");
    check(SteamAPI_ISteamUtils_GetAppID(ut) == 1201270u, "GetAppID -> 1201270");
    check(SteamAPI_ISteamUserStats_GetNumAchievements(us) > 0, "achievement count > 0 (read-only)");
    void *mm = SteamInternal_CreateInterface("SteamMatchMaking009");
    check(mm != NULL && SteamAPI_ISteamMatchmaking_CreateLobby(mm, 0, 4) == 0, "inert handle for matchmaking: non-NULL, CreateLobby -> invalid");
    if (manual) {
        struct { int user; int id; unsigned char *param; int len; } msg;   /* CallbackMsg_t on Win32: len at 12 */
        int pipe = SteamAPI_GetHSteamPipe(), got = 0;
        for (int i = 0; i < 80 && !got; i++) {
            SteamAPI_ManualDispatch_RunFrame(pipe);
            while (SteamAPI_ManualDispatch_GetNextCallback(pipe, &msg)) {
                if (msg.id == 1101 && msg.len == 20) { unsigned long long who; memcpy(&who, msg.param + 12, 8); if (who == steamid) got++; }
                SteamAPI_ManualDispatch_FreeLastCallback(pipe);
            }
            if (!got) Sleep(100);
        }
        check(got >= 1, "ManualDispatch: UserStatsReceived_t arrives as 20 pack(4) bytes with our steamid (Win32 CallbackMsg_t layout)");
        printf("failures: %d\n", failures); return failures ? 1 : 0;
    }
    cbbase cb = { cb_vtable, 0, 0 };
    SteamAPI_RegisterCallback(&cb, 1101);
    check((cb.flags & 1) && cb.id == 1101, "RegisterCallback writes flags@4 and id@8 (Win32 CCallbackBase)");
    printf("RequestCurrentStats=%d\n", SteamAPI_ISteamUserStats_RequestCurrentStats(us));
    for (int i = 0; i < 80 && !got_stats; i++) { SteamAPI_RunCallbacks(); Sleep(100); }
    unsigned long long who = 0; int result = 0; memcpy(&result, stats_param + 8, 4); memcpy(&who, stats_param + 12, 8);
    check(got_stats >= 1 && result == 1 && who == steamid, "UserStatsReceived_t delivered via __thiscall Run with pack(4) layout: result 1, our steamid at offset 12");
    unsigned long long call = SteamAPI_ISteamUserStats_RequestUserStats(us, steamid);
    check(call != 0, "RequestUserStats -> real call handle");
    unsigned char failed = 1; int done = 0;
    for (int i = 0; i < 50 && !done; i++) { done = SteamAPI_ISteamUtils_IsAPICallCompleted(ut, call, &failed); if (!done) Sleep(100); }
    unsigned char res[20] = {0}; unsigned long long rwho = 0;
    check(done && !failed && SteamAPI_ISteamUtils_GetAPICallResult(ut, call, res, 20, 1101, &failed), "IsAPICallCompleted + GetAPICallResult (20 pack(4) bytes)");
    memcpy(&rwho, res + 12, 8); check(rwho == steamid, "call result carries our steamid at the pack(4) offset");
    cbbase cr = { cr_vtable, 0, 1101 };
    SteamAPI_RegisterCallResult(&cr, SteamAPI_ISteamUserStats_RequestUserStats(us, steamid));
    for (int i = 0; i < 100 && !cr_ran; i++) { SteamAPI_RunCallbacks(); Sleep(100); }
    unsigned long long cwho = 0; memcpy(&cwho, cr_param + 12, 8);
    check(cr_ran == 1 && !cr_failed && cwho == steamid, "CCallResult driven through __thiscall Run(param, bIOFailure, handle)");
    SteamAPI_UnregisterCallback(&cb);
    /* C++ path on 32 bits: __thiscall vtables generated from the lib/steambridge/vtables JSON tables, stack sizes from argsizes-i386.json.
     * A wrong N would drift the stack by N per call, so stubs are hammered 1000 times. */
    void *cu = SteamInternal_CreateInterface("SteamUser021"); void **uvt = *(void ***)cu;
    check(((tc_int)uvt[1])(cu) == 1, "C++ SteamUser021 slot 1 BLoggedOn -> 1 (thiscall -> cdecl forward)");
    unsigned long long idout = 0; void *hret = ((tc_hidden_u64)uvt[2])(cu, &idout);
    check(hret == &idout && idout == steamid && idout != 0, "C++ SteamUser021 slot 2 GetSteamID: u64 written through the hidden result pointer, pointer back in eax");
    unsigned char tk[64]; unsigned tn = 0; unsigned tsum = 0;
    for (int i = 0; i < 1000; i++) tsum += ((tc_ticket)uvt[13])(cu, tk, (int)sizeof tk, &tn);
    check(tsum == 0, "C++ SteamUser021 slot 13 GetAuthSessionTicket stub -> 0 through 1000 calls (ret 12 keeps the stack balanced)");
    void *cs = SteamInternal_CreateInterface("STEAMUSERSTATS_INTERFACE_VERSION012"); void **svt = *(void ***)cs;
    int n2 = SteamAPI_ISteamUserStats_GetNumAchievements(us);
    check(((tc_int)svt[14])(cs) == n2, "C++ UserStats012 slot 14 GetNumAchievements == flat");
    check(((tc_u64_u64)svt[16])(cs, steamid) != 0, "C++ UserStats012 slot 16 RequestUserStats(u64 on the stack) -> real call handle");
    unsigned char achieved = 0x5A; const char *first = n2 > 0 ? SteamAPI_ISteamUserStats_GetAchievementName(us, 0) : "";
    int gotach = ((tc_ach)svt[6])(cs, first, &achieved);
    check(n2 == 0 || (gotach == 1 && achieved != 0x5A), "C++ UserStats012 slot 6 GetAchievement(name, bool*) forwards and fills the out parameter");
    int rsum = 0; for (int i = 0; i < 1000; i++) rsum += ((tc_int_bool)svt[21])(cs, 1);
    check(rsum == 0, "C++ UserStats012 slot 21 ResetAllStats stub -> 0 through 1000 calls (ret 4, never forwarded)");
    void *ca = SteamInternal_CreateInterface("STEAMAPPS_INTERFACE_VERSION008"); void **avt = *(void ***)ca;
    unsigned long long owner = 0; ((tc_hidden_u64)avt[20])(ca, &owner);
    check(owner == SteamAPI_ISteamApps_GetAppOwner(apps) && owner != 0, "C++ Apps008 slot 20 GetAppOwner via hidden result pointer == flat");
    void *cut = SteamInternal_CreateInterface("SteamUtils010"); void **utv = *(void ***)cut;
    check(((tc_uint)utv[9])(cut) == 1201270u, "C++ SteamUtils010 slot 9 GetAppID -> 1201270");
    /* Old version strings (entry-07 asks for these) now have exact tables read from a 2015 32-bit DLL; unknown ones get NULL on 32-bit */
    void *cu18 = SteamInternal_CreateInterface("SteamUser018");
    check(cu18 && ((tc_int)(*(void ***)cu18)[1])(cu18) == 1, "C++ SteamUser018 (own table) slot 1 BLoggedOn -> 1");
    void *cut7 = SteamInternal_CreateInterface("SteamUtils007");
    check(cut7 && ((tc_uint)(*(void ***)cut7)[9])(cut7) == 1201270u, "C++ SteamUtils007 (own table) slot 9 GetAppID -> 1201270");
    void *ca7 = SteamInternal_CreateInterface("STEAMAPPS_INTERFACE_VERSION007");
    check(ca7 && ((tc_int)(*(void ***)ca7)[0])(ca7) == 1, "C++ STEAMAPPS 007 (own table) slot 0 BIsSubscribed -> 1 (entry-07's first crash site)");
    check(SteamInternal_CreateInterface("STEAMREMOTESTORAGE_INTERFACE_VERSION013") != NULL, "C++ RemoteStorage013 (aliased to 014: same 55 slots in SDK 1.36 and 1.4x DLLs) -> handle");
    check(SteamInternal_CreateInterface("SteamUser011") == NULL, "C++ SteamUser011 (no table, no alias) -> NULL on 32-bit, never a guessed layout");
    /* Inert families by version: all-stub tables with the right ret N. entry-07 hit ISteamScreenshots::HookScreenshots(bool) (slot 3, 4 bytes). */
    void *shot = SteamInternal_CreateInterface("STEAMSCREENSHOTS_INTERFACE_VERSION002");
    check(shot != NULL && *(void ***)shot != sb_inert_vtable_probe(), "C++ STEAMSCREENSHOTS 002 -> versioned all-stub table, not the anonymous inert vtable");
    int hs = 0; for (int i = 0; i < 1000; i++) hs += ((tc_int_bool)(*(void ***)shot)[3])(shot, 1);
    check(hs == 0, "C++ ISteamScreenshots::HookScreenshots(bool) stub -> 0 through 1000 calls (ret 4 keeps the stack balanced)");
    void *shot3 = SteamAPI_ISteamClient_GetISteamScreenshots(SteamClient(), 1, 1, "STEAMSCREENSHOTS_INTERFACE_VERSION003");
    check(shot3 != NULL && shot3 != shot, "typed GetISteamScreenshots(client, user, pipe, version) -> the 003 table");
    /* SDK <= 1.3x bare accessor exports: served families get the oldest table, inert families their oldest versioned table */
    void *bus = SteamUserStats();
    check(bus && ((tc_int)(*(void ***)bus)[14])(bus) == n2, "bare SteamUserStats() export -> UserStats011 table, slot 14 GetNumAchievements == flat");
    void *bshot = SteamScreenshots();
    check(bshot && ((tc_int_bool)(*(void ***)bshot)[3])(bshot, 0) == 0, "bare SteamScreenshots() export -> STEAMSCREENSHOTS 002 table, HookScreenshots -> 0");
    check(SteamUGC() != NULL, "bare SteamUGC() export -> STEAMUGC 003 all-stub table (entry-07 got NULL from a missing export before)");
    Steam_RunCallbacks();
    check(Steam_GetHSteamUserCurrent() == SteamAPI_GetHSteamUser(), "Steam_GetHSteamUserCurrent (old name) == SteamAPI_GetHSteamUser");
    void *bare = SteamClient(); void **bvt = *(void ***)bare;
    check(bare && ((tc_int)bvt[22])(bare) == 0, "bare SteamClient() export serves the SteamClient017 layout: slot 22 is BShutdownIfAllPipesClosed (stub 0), not slot 23");
    printf("failures: %d\n", failures);
    return failures ? 1 : 0;
}
