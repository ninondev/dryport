// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* Callback struct layout conversion: macOS (VALVE_CALLBACK_PACK_SMALL, pack 4) -> Win64 (pack 8, natural alignment).
 *
 * Steam's headers pack callback structs to 4 bytes on macOS/Linux and 8 bytes on Windows, so the raw bytes the helper
 * drains from Valve's library cannot be handed to a Windows game unchanged whenever an 8-byte field follows a smaller
 * one (UserStatsReceived_t is 20 bytes on macOS and 24 on Windows). Each forwarded callback carries a field descriptor:
 *   q = 8-byte   i = 4-byte   h = 2-byte   b = 1-byte   cN = char[N]
 * A callback without a descriptor is dropped by the caller and logged once; layouts are never guessed.
 * Descriptors come from the struct definitions in the public Steamworks headers; the header files themselves are not
 * in this repository. Shared by helper.c (macOS) and the unit test driver. */
#ifndef STEAMBRIDGE_CBCONV_H
#define STEAMBRIDGE_CBCONV_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>

typedef struct { int id; const char *name; const char *layout; } sb_cb_desc;

static const sb_cb_desc SB_CALLBACKS[] = {
    { 25,   "LicensesUpdated_t",               "" },
    { 101,  "SteamServersConnected_t",         "" },
    { 102,  "SteamServerConnectFailure_t",     "ib" },
    { 103,  "SteamServersDisconnected_t",      "i" },
    { 304,  "PersonaStateChange_t",            "qi" },
    { 331,  "GameOverlayActivated_t",          "bbii" },
    { 336,  "FriendRichPresenceUpdate_t",      "qi" },
    { 701,  "IPCountry_t",                     "" },
    { 702,  "LowBatteryPower_t",               "b" },
    { 703,  "SteamAPICallCompleted_t",         "qii" },
    { 704,  "SteamShutdown_t",                 "" },
    { 714,  "GamepadTextInputDismissed_t",     "bii" },
    { 736,  "AppResumingFromSuspend_t",        "" },
    { 738,  "FloatingGamepadTextInputDismissed_t", "" },
    { 1005, "DlcInstalled_t",                  "i" },
    { 1014, "NewUrlLaunchParameters_t",        "" },
    { 1101, "UserStatsReceived_t",             "qiq" },
    { 1102, "UserStatsStored_t",               "qi" },
    { 1103, "UserAchievementStored_t",         "qbc128ii" },
    { 1108, "UserStatsUnloaded_t",             "q" },
    { 1109, "UserAchievementIconFetched_t",    "qc128bi" },
    { 1110, "GlobalAchievementPercentagesReady_t", "qi" },
    { 1112, "GlobalStatsReceived_t",           "qi" },
};
#define SB_CALLBACK_COUNT ((int)(sizeof SB_CALLBACKS / sizeof SB_CALLBACKS[0]))

static const sb_cb_desc *sb_cb_find(int id) {
    for (int i = 0; i < SB_CALLBACK_COUNT; i++) if (SB_CALLBACKS[i].id == id) return &SB_CALLBACKS[i];
    return NULL;
}

static size_t sb_cb_align(size_t off, size_t al) { return (off + al - 1) / al * al; }

/* Relayout between pack(4) (macOS, Win32) and natural alignment (Win64). Returns the byte count written to dst, or a
 * negative code: -1 unknown callback id, -2 destination too small, -3 source length does not match the descriptor. */
static inline int sb_cb_relayout(int id, const uint8_t *src, int srclen, uint8_t *dst, int cap, int src_natural, int dst_natural) {
    const sb_cb_desc *d = sb_cb_find(id);
    if (!d) return -1;
    size_t so = 0, dof = 0, maxsrc = 1, maxdst = 1;
    for (const char *p = d->layout; *p; ) {
        size_t size, count = 1;
        switch (*p++) {
        case 'q': size = 8; break;
        case 'i': size = 4; break;
        case 'h': size = 2; break;
        case 'b': size = 1; break;
        case 'c': size = 1; count = 0; while (*p >= '0' && *p <= '9') count = count * 10 + (size_t)(*p++ - '0'); break;
        default: return -1;
        }
        size_t asrc = src_natural ? size : (size < 4 ? size : 4), adst = dst_natural ? size : (size < 4 ? size : 4);
        size_t previous_dst = dof;
        so = sb_cb_align(so, asrc); dof = sb_cb_align(dof, adst);
        if (so + size * count > (size_t)srclen) return -3;
        if (dof + size * count > (size_t)cap) return -2;
        memset(dst + previous_dst, 0, dof - previous_dst);
        memcpy(dst + dof, src + so, size * count);
        so += size * count; dof += size * count;
        if (asrc > maxsrc) maxsrc = asrc;
        if (adst > maxdst) maxdst = adst;
    }
    size_t total_src = sb_cb_align(so, maxsrc), total_dst = sb_cb_align(dof, maxdst);
    if (total_src == 0) total_src = 1;     /* an empty struct is 1 byte in C++ */
    if (total_dst == 0) total_dst = 1;
    if ((size_t)srclen != total_src) return -3;
    if (total_dst > (size_t)cap) return -2;
    for (size_t k = dof; k < total_dst; k++) dst[k] = 0;   /* padding and empty struct read as zero */
    return (int)total_dst;
}

/* macOS pack(4) -> Win64 natural (helper side). */
static inline int sb_cb_convert(int id, const uint8_t *src, int srclen, uint8_t *dst, int cap) { return sb_cb_relayout(id, src, srclen, dst, cap, 0, 1); }
/* Win64 natural -> pack(4) (32-bit DLL side: Win32 Steamworks packs callbacks at 4, same as macOS). */
static inline int sb_cb_to_pack4(int id, const uint8_t *src, int srclen, uint8_t *dst, int cap) { return sb_cb_relayout(id, src, srclen, dst, cap, 1, 0); }
#endif

/* Byte size of a callback struct in both layouts, from the descriptor; 0 when unknown. */
static inline int sb_cb_size(int id, int natural) {
    const sb_cb_desc *d = sb_cb_find(id);
    if (!d) return 0;
    size_t off = 0, maxal = 1;
    for (const char *p = d->layout; *p; ) {
        size_t size, count = 1;
        switch (*p++) {
        case 'q': size = 8; break;
        case 'i': size = 4; break;
        case 'h': size = 2; break;
        case 'b': size = 1; break;
        case 'c': size = 1; count = 0; while (*p >= '0' && *p <= '9') count = count * 10 + (size_t)(*p++ - '0'); break;
        default: return 0;
        }
        size_t al = natural ? size : (size < 4 ? size : 4);
        off = sb_cb_align(off, al) + size * count;
        if (al > maxal) maxal = al;
    }
    size_t total = sb_cb_align(off, maxal);
    return (int)(total ? total : 1);
}
