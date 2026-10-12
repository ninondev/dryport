// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* Wire protocol between the Wine-side steam_api64.dll and the native macOS helper.
 * The first bytes of every connection are the per-launch token from STEAMBRIDGE_TOKEN, unframed (release
 * token authentication): the helper closes the connection before any call is handled when it does not match.
 * Request:  u16 fid | u32 len | payload
 * Response: u32 len | payload
 * Strings on the wire: u32 length, then bytes, no NUL.
 * One call at a time, under a mutex on the DLL side. Interface "self" pointers are not sent:
 * the helper owns the real pointers, the DLL hands the game an opaque non-null handle.
 */
#ifndef STEAMBRIDGE_PROTO_H
#define STEAMBRIDGE_PROTO_H

#define SB_PORT_DEFAULT 47821
#define SB_MAGIC 0x5342  /* "SB" */

enum sb_fid {
    F_HELLO = 0,
    F_INIT, F_SHUTDOWN, F_RUNCB, F_RESTART, F_ISRUNNING, F_IFACE,
    F_US_REQSTATS, F_US_STORE, F_US_GETACH, F_US_SETACH, F_US_CLEARACH,
    F_US_NUMACH, F_US_ACHNAME, F_US_ACHATTR, F_US_ACHTIME, F_US_PROGRESS,
    F_US_GETSTATI, F_US_GETSTATF, F_US_SETSTATI, F_US_SETSTATF,
    F_USER_STEAMID, F_USER_LOGGEDON,
    F_APPS_SUBSCRIBED, F_APPS_FAMILY, F_APPS_OWNER, F_APPS_LANG,
    F_FR_PERSONA, F_FR_RICH,
    F_UTILS_APPID, F_UTILS_OVERLAY,
    F_HSTEAMUSER, F_HSTEAMPIPE, F_IFACE_BY_VERSION,
    F_CB_DRAIN,      /* -> u8 count, then per callback: u32 id, u32 len, len bytes in Win64 layout (2026-09-13) */
    F_UTILS_INITFILTER,  /* u32 options -> u8 ok. entry-05's one unforwarded slot (2026-09-13) */
    F_UTILS_FILTERTEXT,  /* u32 context, u64 source steamid, str input, u32 out capacity -> u32 filtered count, str output */
    F_US_REQUSERSTATS,   /* u64 steamid -> u64 SteamAPICall_t; the completed call comes back as a UserStatsReceived_t callback (2026-09-13) */
    F_UT_ISCALLDONE,     /* u64 call -> u8 completed, u8 failed */
    F_UT_CALLRESULT,     /* u64 call, u32 expected callback id -> u8 ok, u8 failed, u32 len, len bytes in Win64 layout (descriptor required) */
    F_UT_CALLFAIL,       /* u64 call -> u32 ESteamAPICallFailure */
    F_MAX
};

/* F_IFACE argument */
enum sb_iface { IF_USERSTATS = 1, IF_USER, IF_APPS, IF_FRIENDS, IF_UTILS, IF_REMOTESTORAGE };

#endif
