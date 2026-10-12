// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* The inert side of the bridge: interface handles, the never-forwarded families, and typed no-op bodies.
 * This file has no socket code on purpose. Nothing here can reach the helper or Steam, which is what lets
 * gen.py accept never-implement names in INERT_HANDWRITTEN: they are bodies that fail, not forwards. */
#include <windows.h>
#include <string.h>
#include <ctype.h>
#include "bridge.h"
#include "proto.h"
#include "inert_gen.h"

/* "SteamUserStats013", "STEAMUSERSTATS_INTERFACE_VERSION013" and "userstats" all reduce to "userstats".
 * Kept identical to family_token() in helper.c: DLL and helper must agree on what is served. */
unsigned sb_family_token(const char *ver, char *fam, size_t cap) {
    static const struct { const char *family; unsigned which; } served[] = {
        { "client", 0xF0 },
        { "userstats", IF_USERSTATS }, { "user", IF_USER }, { "apps", IF_APPS },
        { "friends", IF_FRIENDS }, { "utils", IF_UTILS }, { "remotestorage", IF_REMOTESTORAGE },
    };
    const char *p = ver ? ver : ""; size_t i = 0;
    if (_strnicmp(p, "steam", 5) == 0) p += 5;
    for (; *p && isalpha((unsigned char)*p) && i + 1 < cap; p++) fam[i++] = (char)tolower((unsigned char)*p);
    fam[i] = 0;
    for (size_t k = 0; k < sizeof served / sizeof *served; k++)
        if (!strcmp(fam, served[k].family)) return served[k].which;
    return 0;
}

/* ---- handles ---- */
#define SERVED(w, name) { sb_inert_vtable, (w), 0, (name), 0 }
static sb_handle served_handles[8] = {
    SERVED(0, "none"), SERVED(IF_USERSTATS, "userstats"), SERVED(IF_USER, "user"), SERVED(IF_APPS, "apps"),
    SERVED(IF_FRIENDS, "friends"), SERVED(IF_UTILS, "utils"), SERVED(IF_REMOTESTORAGE, "remotestorage"),
    SERVED(0xF0, "client"),
};
static sb_handle inert_handles[SB_INERT_FAMILY_COUNT];
static sb_handle inert_unknown = { sb_inert_vtable, 0, 1, "unknown", 0 };

void sb_inert_init(void) {
    for (int k = 0; k < SB_INERT_FAMILY_COUNT; k++) {
        inert_handles[k].vtable = sb_inert_vtable; inert_handles[k].which = 0; inert_handles[k].inert = 1;
        inert_handles[k].family = sb_inert_families[k]; inert_handles[k].logged = 0;
    }
}

void *sb_served_handle(unsigned which) {
    unsigned i = which == 0xF0 ? 7 : which;
    if (i < 1 || i > 7) return NULL;
    return &served_handles[i];
}

void *sb_inert_handle(const char *family) {
    char key[64]; size_t i = 0;
    for (const char *p = family ? family : ""; *p && i + 1 < sizeof key; p++)
        if (isalpha((unsigned char)*p)) key[i++] = (char)tolower((unsigned char)*p);
    key[i] = 0;
    sb_handle *h = &inert_unknown;
    for (int k = 0; k < SB_INERT_FAMILY_COUNT; k++)
        if (!strcmp(key, sb_inert_families[k])) { h = &inert_handles[k]; break; }
    if (!InterlockedExchange(&h->logged, 1))
        sb_log("inert handle for \"%s\" (asked as \"%s\"): never forwarded, every call fails", h->family, family ? family : "?");
    return h;
}

void *sb_inert_by_version(const char *ver) {
    char fam[64]; sb_family_token(ver, fam, sizeof fam);
    return sb_inert_handle(fam);
}

void sb_inert_hit(int slot) {
    static volatile long seen[SB_INERT_SLOTS];
    if (slot >= 0 && slot < SB_INERT_SLOTS && !InterlockedExchange(&seen[slot], 1))
        sb_log("inert vtable slot %d called through a C++ interface: returned 0 (no C++ forwarding yet)", slot);
}

/* ---- typed no-op bodies (INERT_HANDWRITTEN in gen.py) ----
 * Game-server interfaces are never served; the getter still hands out an inert object so a C++ caller
 * does not dereference NULL. */
API void *SteamInternal_FindOrCreateGameServerInterface(int user, const char *ver) { (void)user; return sb_inert_by_version(ver); }

/* MS x64: an aggregate wider than 8 bytes is returned through a hidden pointer passed as the FIRST
 * argument of a non-member function, so `self` is the second one. Zero exactly the struct and hand the
 * pointer back in RAX, as the convention requires. Sizes: InputAnalogActionData_t / ControllerAnalogActionData_t
 * 16 (mode, x, y, active + padding), InputMotionData_t / ControllerMotionData_t 40 (ten floats),
 * SteamIPAddress_t 24 (16-byte address union + type + padding). */
API void *SteamAPI_ISteamInput_GetAnalogActionData(void *out, void *self, uint64_t c, uint64_t a) { (void)self; (void)c; (void)a; return memset(out, 0, 16); }
API void *SteamAPI_ISteamInput_GetMotionData(void *out, void *self, uint64_t c) { (void)self; (void)c; return memset(out, 0, 40); }
API void *SteamAPI_ISteamController_GetAnalogActionData(void *out, void *self, uint64_t c, uint64_t a) { (void)self; (void)c; (void)a; return memset(out, 0, 16); }
API void *SteamAPI_ISteamController_GetMotionData(void *out, void *self, uint64_t c) { (void)self; (void)c; return memset(out, 0, 40); }
API void *SteamAPI_ISteamGameServer_GetPublicIP(void *out, void *self) { (void)self; return memset(out, 0, 24); }

/* Float returns travel in XMM0; a bare `return 0` would leave it untouched. */
API float SteamAPI_ISteamMusic_GetVolume(void *self) { (void)self; return 0.0f; }
API float SteamAPI_ISteamNetworkingUtils_GetLocalPingLocation(void *self, void *loc) { (void)self; (void)loc; return -1.0f; }   /* < 0: no data */

/* Callers read these buffers as strings afterwards; leave a valid empty one behind. */
static void empty_string(char *buf, long long cch) { if (buf && cch > 0) buf[0] = 0; }
API void SteamAPI_ISteamNetworkingUtils_ConvertPingLocationToString(void *self, const void *loc, char *buf, int cch) { (void)self; (void)loc; empty_string(buf, cch); }
API void SteamAPI_ISteamNetworkingUtils_SteamNetworkingIPAddr_ToString(void *self, const void *addr, char *buf, unsigned cch, int port) { (void)self; (void)addr; (void)port; empty_string(buf, cch); }
API void SteamAPI_ISteamNetworkingUtils_SteamNetworkingIdentity_ToString(void *self, const void *id, char *buf, unsigned cch) { (void)self; (void)id; empty_string(buf, cch); }
API void SteamAPI_SteamNetworkingIPAddr_ToString(void *self, char *buf, unsigned cch, int port) { (void)self; (void)port; empty_string(buf, cch); }
API void SteamAPI_SteamNetworkingIdentity_ToString(void *self, char *buf, unsigned cch) { (void)self; empty_string(buf, cch); }
