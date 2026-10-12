// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* C++ interface path for the families the bridge serves. Each interface version a game may ask for gets a real vtable
 * (generated into vtables_gen.c from the lib/steambridge/vtables JSON tables): slots the flat API implements forward straight to
 * those functions, the two methods that return through a hidden result pointer go through the typed thunks below, and
 * every other slot is a stub that returns 0 and logs the version, slot and method once. Versions without a table fall
 * back to the family's inert handle, so a C++ caller never dereferences garbage. */
#include <windows.h>
#include <string.h>
#include "bridge.h"
#include "proto.h"
#include "vtables_gen.h"

unsigned long long SteamAPI_ISteamUser_GetSteamID(void *self);
unsigned long long SteamAPI_ISteamApps_GetAppOwner(void *self);

/* MS x64 member function returning a class with a constructor (CSteamID): this in RCX, hidden result pointer in RDX,
 * the pointer comes back in RAX. The flat function returns the same value in RAX as a plain uint64. */
void *sb_thunk_ISteamUser_GetSteamID(void *self, unsigned long long *out) { *out = SteamAPI_ISteamUser_GetSteamID(self); return out; }
void *sb_thunk_ISteamApps_GetAppOwner(void *self, unsigned long long *out) { *out = SteamAPI_ISteamApps_GetAppOwner(self); return out; }

static sb_handle vt_handles[SB_VTABLE_COUNT];
static const struct { const char *family; unsigned which; } family_ids[] = {
    { "client", 0xF0 }, { "userstats", IF_USERSTATS }, { "user", IF_USER }, { "apps", IF_APPS },
    { "friends", IF_FRIENDS }, { "utils", IF_UTILS }, { "remotestorage", IF_REMOTESTORAGE },
};

void sb_vtables_init(void) {
    for (int i = 0; i < SB_VTABLE_COUNT; i++) {
        unsigned which = 0;
        for (size_t k = 0; k < sizeof family_ids / sizeof *family_ids; k++)
            if (!strcmp(family_ids[k].family, sb_vtables[i].family)) which = family_ids[k].which;
        vt_handles[i].vtable = sb_vtables[i].table; vt_handles[i].which = which; vt_handles[i].inert = which == 0;   /* inert family: all-stub table */
        vt_handles[i].family = sb_vtables[i].version; vt_handles[i].logged = 0;
    }
}

/* Inert families (never forwarded) asked for by version string: a table with that exact version gives named, stack-clean
 * stubs (ret N on 32-bit) instead of the anonymous inert slots. Anything else falls back to the family's inert handle. */
void *sb_inert_versioned(const char *version, const char *family) {
    for (int i = 0; i < SB_VTABLE_COUNT; i++)
        if (version && vt_handles[i].which == 0 && !strcmp(sb_vtables[i].version, version)) {
            if (!InterlockedExchange(&vt_handles[i].logged, 1))
                sb_log("C++ vtable %s: %d slots (inert family %s: every method returns 0, arguments cleaned)", sb_vtables[i].version, sb_vtables[i].count, sb_vtables[i].family);
            return &vt_handles[i];
        }
    return sb_inert_handle(family);
}

/* Exact version string match ("SteamUser021", "STEAMUSERSTATS_INTERFACE_VERSION012"); anything else gets the family's
 * inert-vtable handle and a one-line log so the missing table is visible in steambridge.log. */
void *sb_vtable_handle(unsigned which, const char *version) {
    for (int i = 0; i < SB_VTABLE_COUNT; i++)
        if (version && !strcmp(sb_vtables[i].version, version) && vt_handles[i].which == which) {
            if (!InterlockedExchange(&vt_handles[i].logged, 1))
                sb_log("C++ vtable %s: %d slots", sb_vtables[i].version, sb_vtables[i].count);
            return &vt_handles[i];
        }
    /* Older version strings that only ever had methods appended after them borrow the newer table (gen.py VTABLE_ALIASES). */
    for (int a = 0; a < SB_VTABLE_ALIAS_COUNT; a++)
        if (version && !strcmp(sb_vtable_aliases[a].version, version))
            for (int i = 0; i < SB_VTABLE_COUNT; i++)
                if (!strcmp(sb_vtables[i].version, sb_vtable_aliases[a].alias) && vt_handles[i].which == which) {
                    if (!InterlockedExchange(&vt_handles[i].logged, 1))
                        sb_log("C++ vtable %s served for %s (same slots, methods only appended after it)", sb_vtables[i].version, version);
                    return &vt_handles[i];
                }
    sb_handle *h = sb_served_handle(which);
#if defined(__i386__)
    /* 32-bit: the inert table's __thiscall stubs cannot clean a caller's arguments, so a C++ caller would corrupt its stack on the
     * first call. Guessing a neighbouring version's table is worse: SteamClient020 inserted a slot at 20 and RemoteStorage014
     * reshuffled 50 of 52 slots (2026-09-13), so a wrong table sends the game into garbage. NULL fails like Steam not running;
     * games that skip the null check crash at address 0 with the missing version named in this log, which is what a new table needs. */
    if (h && !InterlockedExchange(&h->logged, 1))
        sb_log("no C++ vtable for \"%s\" (family %s) on the 32-bit bridge: returning NULL; add a table (tools/steam-flat-argsizes.py --tables-out) or a verified alias for this version", version ? version : "?", h->family);
    return NULL;
#else
    if (h && !InterlockedExchange(&h->logged, 1))
        sb_log("no C++ vtable for \"%s\" (family %s): C++ calls on it return 0, flat calls still forward", version ? version : "?", h->family);
    return h;
#endif
}

/* 32-bit only: a slot whose __thiscall stack size is not in argsizes-i386.json cannot clean the caller's arguments, so the
 * caller's stack is off by that many bytes after the call. Logged once per slot so the next DLL census knows what to add. */
void sb_vt_stub_hit_unknown(int table, int slot) {
    static volatile long seen[SB_VTABLE_COUNT][160];
    if (table < 0 || table >= SB_VTABLE_COUNT || slot < 0) return;
    if (slot < 160 && InterlockedExchange(&seen[table][slot], 1)) return;
    const sb_vtable *t = &sb_vtables[table];
    sb_log("C++ %s slot %d (%s): stack size unknown on 32-bit, returned 0 without cleaning the caller's arguments; the stack may be corrupted",
           t->version, slot, slot < t->count ? t->names[slot] : "beyond known slots");
}

void sb_vt_stub_hit(int table, int slot) {
    static volatile long seen[SB_VTABLE_COUNT][128];
    if (table < 0 || table >= SB_VTABLE_COUNT || slot < 0) return;
    if (slot < 128 && InterlockedExchange(&seen[table][slot], 1)) return;
    const sb_vtable *t = &sb_vtables[table];
    if (slot < t->count)
        sb_log("C++ %s slot %d (%s) not forwarded: returned 0", t->version, slot, t->names[slot]);
    else
        sb_log("C++ %s slot %d beyond the %d known slots (newer SDK appended methods to this version string): padded stub returned 0",
               t->version, slot, t->count);
}
