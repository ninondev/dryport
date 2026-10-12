// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* savebridge - exchange files from an explicit local save directory with one game's
 * Steam Cloud slot (ISteamRemoteStorage, API namespace), preserving pull conflicts locally.
 *
 *   savebridge <appid> status <dir> [pattern]
 *   pattern 可以写多个，用分号隔开，例如 "*.rvdata2;*.sav"。存档和游戏本体混在一个目录时必须给。
 *   savebridge <appid> push   <dir> [pattern]
 *   savebridge <appid> pull   <dir>
 *   savebridge <appid> sync   <dir> [pattern]   two-way, conflicts kept side by side
 *   savebridge <appid> wipe                      delete every file in this game's cloud slot
 *
 * Needs steam_appid.txt next to the binary (or SteamAppId in the environment) and a running Steam.
 * MIT. Part of the dryport project.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <dirent.h>
#include <fnmatch.h>
#include <sys/stat.h>
#include <time.h>
#include <CommonCrypto/CommonDigest.h>

typedef void* H;
extern int  SteamAPI_Init(void);
extern void SteamAPI_Shutdown(void);
extern void SteamAPI_RunCallbacks(void);
extern H    SteamAPI_SteamRemoteStorage_v016(void);
extern int32_t     SteamAPI_ISteamRemoteStorage_GetFileCount(H);
extern const char* SteamAPI_ISteamRemoteStorage_GetFileNameAndSize(H, int32_t, int32_t*);
extern int32_t     SteamAPI_ISteamRemoteStorage_GetFileSize(H, const char*);
extern int64_t     SteamAPI_ISteamRemoteStorage_GetFileTimestamp(H, const char*);
extern int         SteamAPI_ISteamRemoteStorage_FileExists(H, const char*);
extern int         SteamAPI_ISteamRemoteStorage_FileWrite(H, const char*, const void*, int32_t);
extern int32_t     SteamAPI_ISteamRemoteStorage_FileRead(H, const char*, void*, int32_t);
extern int         SteamAPI_ISteamRemoteStorage_FileDelete(H, const char*);
extern int         SteamAPI_ISteamRemoteStorage_IsCloudEnabledForAccount(H);
extern int         SteamAPI_ISteamRemoteStorage_IsCloudEnabledForApp(H);
extern int         SteamAPI_ISteamRemoteStorage_GetQuota(H, uint64_t*, uint64_t*);
extern int         SteamAPI_ISteamRemoteStorage_SetSyncPlatforms(H, const char*, uint32_t);

#define MAXF 256
#define MANIFEST ".savebridge.json"
#define LOCALSTATE ".savebridge-last.json"

static void sha1hex(const unsigned char* b, size_t n, char out[41]) {
    unsigned char d[CC_SHA1_DIGEST_LENGTH]; CC_SHA1(b, (CC_LONG)n, d);
    for (int i = 0; i < CC_SHA1_DIGEST_LENGTH; i++) sprintf(out + i * 2, "%02x", d[i]);
    out[40] = 0;
}
static unsigned char* readfile(const char* p, long* n) {
    FILE* f = fopen(p, "rb"); if (!f) return NULL;
    fseek(f, 0, SEEK_END); *n = ftell(f); fseek(f, 0, SEEK_SET);
    unsigned char* b = malloc(*n ? *n : 1);
    if (*n && fread(b, 1, *n, f) != (size_t)*n) { free(b); fclose(f); return NULL; }
    fclose(f); return b;
}
static int writefile(const char* p, const void* b, long n) {
    FILE* f = fopen(p, "wb"); if (!f) return 0;
    int ok = (n == 0) || (fwrite(b, 1, n, f) == (size_t)n); fclose(f); return ok;
}
static int keep_conflict(const char* target, const void* bytes, long n, char* side, size_t cap) {
    /* 每份冲突用新节点保留；快速重复 pull 不能覆盖上一份副本。 */
    if (snprintf(side, cap, "%s.cloud-XXXXXX", target) >= (int)cap) return 0;
    int fd = mkstemp(side); if (fd < 0) return 0;
    FILE* f = fdopen(fd, "wb"); if (!f) { close(fd); unlink(side); return 0; }
    int ok = n == 0 || fwrite(bytes, 1, n, f) == (size_t)n;
    if (fclose(f)) ok = 0;
    if (!ok) unlink(side);
    return ok;
}
static void joinp(char* out, size_t cap, const char* a, const char* b) { snprintf(out, cap, "%s/%s", a, b); }

/* tiny flat-JSON helpers: our manifests are one level deep, {"name":{"sha":"..","size":N,"mtime":N},..} */
static int json_get(const char* js, const char* name, const char* key, char* out, size_t cap) {
    char pat[512]; snprintf(pat, sizeof pat, "\"%s\":{", name);
    const char* p = js ? strstr(js, pat) : NULL; if (!p) return 0;
    const char* end = strchr(p, '}'); if (!end) return 0;
    char kpat[128]; snprintf(kpat, sizeof kpat, "\"%s\":", key);
    const char* q = strstr(p, kpat); if (!q || q > end) return 0;
    q += strlen(kpat); if (*q == '"') { q++; const char* r = strchr(q, '"'); if (!r) return 0;
        size_t n = (size_t)(r - q); if (n >= cap) n = cap - 1; memcpy(out, q, n); out[n] = 0; return 1; }
    size_t n = strspn(q, "-0123456789"); if (n >= cap) n = cap - 1; memcpy(out, q, n); out[n] = 0; return 1;
}

struct ent { char name[192]; char sha[41]; long size; long mtime; };

/* Pull and push share the same flat filename and pattern scope. */
static int included_name(const char *name, const char *pattern) {
    if (!name || !*name || name[0] == '.' || strlen(name) >= 192
            || strpbrk(name, "/\\\"\r\n") || strstr(name, ".cloud-")) return 0;
    size_t len = strlen(name);
    if (len > 4 && strcasecmp(name + len - 4, ".log") == 0) return 0;
    if (!pattern || !*pattern) return 1;
    const char *p0 = pattern;
    while (*p0) {
        const char *semi = strchr(p0, ';');
        size_t n = semi ? (size_t)(semi - p0) : strlen(p0);
        char part[256];
        if (n < sizeof part) {
            memcpy(part, p0, n); part[n] = 0;
            if (n && fnmatch(part, name, 0) == 0) return 1;
        }
        p0 = semi ? semi + 1 : p0 + strlen(p0);
    }
    return 0;
}

static int scan_local(const char* dir, const char* pattern, struct ent* v, int cap) {
    DIR* d = opendir(dir); if (!d) return 0; int n = 0; struct dirent* e;
    while ((e = readdir(d)) && n < cap) {
        if (!included_name(e->d_name, pattern)) continue;
        char p[1024]; joinp(p, sizeof p, dir, e->d_name);
        struct stat st; if (stat(p, &st) || !S_ISREG(st.st_mode)) continue;
        long sz = 0; unsigned char* b = readfile(p, &sz); if (!b) continue;
        snprintf(v[n].name, sizeof v[n].name, "%s", e->d_name);
        sha1hex(b, sz, v[n].sha); v[n].size = sz; v[n].mtime = (long)st.st_mtime; free(b); n++;
    }
    closedir(d); return n;
}
static int find_ent(struct ent* v, int n, const char* name) {
    for (int i = 0; i < n; i++) if (strcmp(v[i].name, name) == 0) return i; return -1;
}
static char* cloud_read(H rs, const char* name, long* n) {
    if (!SteamAPI_ISteamRemoteStorage_FileExists(rs, name)) return NULL;
    int32_t sz = SteamAPI_ISteamRemoteStorage_GetFileSize(rs, name);
    char* b = malloc(sz ? sz : 1);
    int32_t got = SteamAPI_ISteamRemoteStorage_FileRead(rs, name, b, sz);
    if (got != sz) { free(b); return NULL; }
    *n = sz; return b;
}
static void build_manifest(char* out, size_t cap, struct ent* v, int n) {
    size_t o = snprintf(out, cap, "{");
    for (int i = 0; i < n; i++)
        o += snprintf(out + o, cap - o, "%s\"%s\":{\"sha\":\"%s\",\"size\":%ld,\"mtime\":%ld}",
                      i ? "," : "", v[i].name, v[i].sha, v[i].size, v[i].mtime);
    snprintf(out + o, cap - o, "}");
}

int main(int argc, char** argv) {
    /* 测试总开关：测试壳设 SN_SAVEBRIDGE=0 让云同步整体停摆，测试跑出来的存档不能上云弄脏真存档。 */
    const char* gate = getenv("SN_SAVEBRIDGE");
    if (gate && strcmp(gate, "0") == 0) { printf("测试模式（SN_SAVEBRIDGE=0），跳过云同步\n"); return 0; }
    if (argc < 3) { fprintf(stderr,
        "usage: savebridge <appid> status|push|pull|sync <dir> [pattern]\n"
        "       savebridge <appid> wipe\n"); return 2; }
    const char* appid = argv[1]; const char* cmd = argv[2];
    const char* dir = argc > 3 ? argv[3] : NULL;
    const char* pattern = argc > 4 ? argv[4] : NULL;
    setenv("SteamAppId", appid, 1); setenv("SteamGameId", appid, 1);
    if (!SteamAPI_Init()) { fprintf(stderr, "Steam 没连上：确认 Steam 正在运行并已登录，且这个 appid 在你的库里\n"); return 1; }
    H rs = SteamAPI_SteamRemoteStorage_v016();
    if (!rs) { fprintf(stderr, "拿不到 RemoteStorage 接口\n"); SteamAPI_Shutdown(); return 1; }
    for (int i = 0; i < 8; i++) { SteamAPI_RunCallbacks(); usleep(80000); }

    uint64_t tot = 0, avail = 0; SteamAPI_ISteamRemoteStorage_GetQuota(rs, &tot, &avail);
    int acct = SteamAPI_ISteamRemoteStorage_IsCloudEnabledForAccount(rs);
    int app  = SteamAPI_ISteamRemoteStorage_IsCloudEnabledForApp(rs);

    if (strcmp(cmd, "status") == 0) {
        printf("appid %s  云开关: 账号=%s 本作=%s  配额 %llu 字节, 剩余 %llu\n", appid,
               acct ? "开" : "关", app ? "开" : "关", (unsigned long long)tot, (unsigned long long)avail);
        int32_t n = SteamAPI_ISteamRemoteStorage_GetFileCount(rs);
        printf("云端 %d 个文件:\n", n);
        for (int32_t i = 0; i < n; i++) { int32_t sz = 0;
            const char* nm = SteamAPI_ISteamRemoteStorage_GetFileNameAndSize(rs, i, &sz);
            time_t ts = (time_t)SteamAPI_ISteamRemoteStorage_GetFileTimestamp(rs, nm);
            printf("  %-40s %8d 字节  %s", nm ? nm : "?", sz, ctime(&ts));
        }
        if (dir) { struct ent v[MAXF]; int ln = scan_local(dir, pattern, v, MAXF);
            printf("本地 %s: %d 个文件\n", dir, ln);
            for (int i = 0; i < ln; i++) printf("  %-40s %8ld 字节  %s", v[i].name, v[i].size, v[i].sha); if (ln) printf("\n"); }
        SteamAPI_Shutdown(); return 0;
    }
    if (strcmp(cmd, "wipe") == 0) {
        int32_t n = SteamAPI_ISteamRemoteStorage_GetFileCount(rs); int killed = 0;
        char names[MAXF][192]; int cnt = 0;
        for (int32_t i = 0; i < n && cnt < MAXF; i++) { int32_t sz = 0;
            const char* nm = SteamAPI_ISteamRemoteStorage_GetFileNameAndSize(rs, i, &sz);
            if (nm) snprintf(names[cnt++], 192, "%s", nm); }
        for (int i = 0; i < cnt; i++) if (SteamAPI_ISteamRemoteStorage_FileDelete(rs, names[i])) { printf("删除 %s\n", names[i]); killed++; }
        printf("删掉 %d 个云文件\n", killed); SteamAPI_Shutdown(); return 0;
    }
    if (!dir) { fprintf(stderr, "缺目录参数\n"); SteamAPI_Shutdown(); return 2; }
    if (!app || !acct) { fprintf(stderr, "Steam 云对这个账号或这个游戏是关的，先在 Steam 里打开\n"); SteamAPI_Shutdown(); return 1; }
    mkdir(dir, 0755);

    struct ent loc[MAXF]; int ln = scan_local(dir, pattern, loc, MAXF);
    long mlen = 0; char* mraw = cloud_read(rs, MANIFEST, &mlen);
    char* cman = NULL; if (mraw) { cman = malloc(mlen + 1); memcpy(cman, mraw, mlen); cman[mlen] = 0; free(mraw); }
    char lstate[1024]; joinp(lstate, sizeof lstate, dir, LOCALSTATE);
    long llen = 0; unsigned char* lraw = readfile(lstate, &llen);
    char* lman = NULL; if (lraw) { lman = malloc(llen + 1); memcpy(lman, lraw, llen); lman[llen] = 0; free(lraw); }

    int pushed = 0, pulled = 0, conflicts = 0, skipped = 0;
    struct ent unresolved[MAXF]; int unresolved_n = 0;
    int do_push = (strcmp(cmd, "push") == 0 || strcmp(cmd, "sync") == 0);
    int do_pull = (strcmp(cmd, "pull") == 0 || strcmp(cmd, "sync") == 0);

    /* cloud -> local */
    if (do_pull && cman) {
        int32_t n = SteamAPI_ISteamRemoteStorage_GetFileCount(rs);
        for (int32_t i = 0; i < n; i++) { int32_t sz = 0;
            const char* nm = SteamAPI_ISteamRemoteStorage_GetFileNameAndSize(rs, i, &sz);
            if (!included_name(nm, pattern)) { skipped++; continue; }
            char csha[64] = ""; json_get(cman, nm, "sha", csha, sizeof csha);
            int li = find_ent(loc, ln, nm);
            if (li >= 0 && strcmp(loc[li].sha, csha) == 0) { skipped++; continue; }
            char lastsha[64] = ""; int had_last = lman ? json_get(lman, nm, "sha", lastsha, sizeof lastsha) : 0;
            long cn = 0; char* cb = cloud_read(rs, nm, &cn); if (!cb) continue;
            char target[1024]; joinp(target, sizeof target, dir, nm);
            if (li >= 0 && (!had_last || strcmp(loc[li].sha, lastsha) != 0)) {
                /* 首次内容不同、或本地已改，均保留两份，不把冲突写成已完成同步。 */
                char side[1088];
                if (!keep_conflict(target, cb, cn, side, sizeof side)) {
                    fprintf(stderr, "冲突副本写入失败 %s：本地与云端原件未改动\n", nm);
                    free(cb); SteamAPI_Shutdown(); return 1;
                }
                if (find_ent(unresolved, unresolved_n, nm) < 0 && unresolved_n < MAXF)
                    unresolved[unresolved_n++] = loc[li];
                conflicts++;
                printf("冲突 %s：本地保留，云端那份存成 %s\n", nm, side + strlen(dir) + 1);
            } else if (li < 0 || strcmp(loc[li].sha, csha) != 0) {
                writefile(target, cb, cn); pulled++; printf("拉下 %s (%ld 字节)\n", nm, cn);
            }
            free(cb);
        }
        ln = scan_local(dir, pattern, loc, MAXF);
    }
    /* local -> cloud */
    if (do_push) {
        for (int i = 0; i < ln; i++) {
            char csha[64] = ""; int in_cloud = cman ? json_get(cman, loc[i].name, "sha", csha, sizeof csha) : 0;
            if (in_cloud && strcmp(csha, loc[i].sha) == 0) { skipped++; continue; }
            char p[1024]; joinp(p, sizeof p, dir, loc[i].name);
            long n = 0; unsigned char* b = readfile(p, &n); if (!b) continue;
            if (SteamAPI_ISteamRemoteStorage_FileWrite(rs, loc[i].name, b, (int32_t)n)) {
                pushed++; printf("上传 %s (%ld 字节)\n", loc[i].name, n);
            } else fprintf(stderr, "上传失败 %s：配额或文件数超限\n", loc[i].name);
            free(b);
        }
        char man[65536]; build_manifest(man, sizeof man, loc, ln);
        SteamAPI_ISteamRemoteStorage_FileWrite(rs, MANIFEST, man, (int32_t)strlen(man));
        writefile(lstate, man, (long)strlen(man));
    } else if (do_pull) {
        ln = scan_local(dir, pattern, loc, MAXF);
        int confirmed = 0;
        for (int i = 0; i < ln; i++)
            if (find_ent(unresolved, unresolved_n, loc[i].name) < 0) loc[confirmed++] = loc[i];
        char man[65536]; build_manifest(man, sizeof man, loc, confirmed); writefile(lstate, man, (long)strlen(man));
    }
    for (int i = 0; i < 20; i++) { SteamAPI_RunCallbacks(); usleep(50000); }
    printf("完成：上传 %d, 拉下 %d, 冲突 %d, 无变化 %d\n", pushed, pulled, conflicts, skipped);
    SteamAPI_Shutdown(); return conflicts ? 3 : 0;
}
