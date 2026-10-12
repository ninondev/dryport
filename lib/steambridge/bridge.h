// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
#ifndef STEAMBRIDGE_BRIDGE_H
#define STEAMBRIDGE_BRIDGE_H
#include <stddef.h>
#include <stdint.h>

#define API __declspec(dllexport)

void sb_log(const char *fmt, ...);          /* Win32-handle logger in bridge.c; safe from any file */
void sb_stub_hit(int idx);
extern const char *const stub_names[];
extern const int stub_count;

/* Every unimplemented export lands here. On x86_64 there is one calling convention, so a
 * no-argument function is ABI-compatible as a callee for any integer/pointer signature: the caller's
 * arguments sit in registers we simply do not read, and 0 in RAX reads back as false/0/NULL.
 * Exports that return a float, an aggregate wider than 8 bytes, or fill a caller buffer get a typed
 * body in inert_stubs.c instead (INERT_HANDWRITTEN in gen.py). */
#define STUB(name, idx) \
    __declspec(dllexport) long long name(void) { sb_stub_hit(idx); return 0; }

/* ---- interface handles ----
 * A handle is a real object whose first word is a vtable, so a C++ caller that treats it as ISteamXxx*
 * reaches a table of inert methods instead of dereferencing an integer token. Served handles stand for
 * the families the helper forwards; flat calls ignore the handle and route by fixed FID. Inert handles
 * stand for every other family: never forwarded, every method returns 0 / false / 0.0f. */
typedef struct sb_handle {
    void *const *vtable;
    unsigned which;          /* IF_* from proto.h, 0xF0 for ISteamClient, 0 for inert */
    unsigned inert;
    const char *family;
    volatile long logged;
} sb_handle;

extern void *const sb_inert_vtable[];       /* generated: SB_INERT_SLOTS inert methods */
void sb_inert_init(void);                   /* fill the handle tables; DllMain calls it first */
void sb_inert_hit(int slot);
void *sb_served_handle(unsigned which);
void sb_vtables_init(void);                 /* vtables.c: per-version C++ tables from lib/steambridge/vtables */
void *sb_vtable_handle(unsigned which, const char *version);   /* handle whose vtable matches the version string, else the inert one */
void *sb_inert_handle(const char *family);  /* "matchmaking", "SteamMatchMaking009": both work; unknown names share one handle */
void *sb_inert_versioned(const char *version, const char *family);   /* vtables.c: exact-version all-stub table for an inert family, else sb_inert_handle */
void *sb_inert_by_version(const char *version);
unsigned sb_family_token(const char *version, char *family, size_t cap);   /* helper.c's reduction; 0 = not served */

#endif
