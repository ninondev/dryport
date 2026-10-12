// 包内主映像保留 macOS 应用身份；引擎初始化仍由固定原厂入口完成。
#import <Foundation/Foundation.h>
#import <Security/Security.h>
#include <dlfcn.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static NSString *const version = @"8.1.3.23091805";
static NSString *const appid = @"2118420";
static NSString *verifiedBootstrapApp;

static BOOL fail(NSString *message) {
    fprintf(stderr, "Ren’Py 未启动：%s\n", message.UTF8String);
    return NO;
}

static NSString *canonical(id value) {
    if (![value isKindOfClass:NSString.class] || ![value isAbsolutePath]) return nil;
    char *path = realpath([value fileSystemRepresentation], NULL);
    if (!path) return nil;
    NSString *result = [NSFileManager.defaultManager stringWithFileSystemRepresentation:path length:strlen(path)];
    free(path);
    return result;
}

static NSDictionary *readObject(NSString *path, NSUInteger limit) {
    NSNumber *size;
    NSURL *url = [NSURL fileURLWithPath:path];
    if (![url getResourceValue:&size forKey:NSURLFileSizeKey error:NULL] || size.unsignedLongLongValue > limit) return nil;
    NSData *data = [NSData dataWithContentsOfURL:url options:0 error:NULL];
    if (!data || data.length > limit) return nil;
    id object = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
    return [object isKindOfClass:NSDictionary.class] ? object : nil;
}

static BOOL verifyCode(NSString *path, NSString *expectedIdentity) {
    SecStaticCodeRef code = NULL;
    NSDictionary *attributes = @{(__bridge NSString *)kSecCodeAttributeArchitecture: @"arm64"};
    OSStatus status = SecStaticCodeCreateWithPathAndAttributes((__bridge CFURLRef)[NSURL fileURLWithPath:path],
        kSecCSDefaultFlags, (__bridge CFDictionaryRef)attributes, &code);
    if (status == errSecSuccess) status = SecStaticCodeCheckValidityWithErrors(code, kSecCSStrictValidate, NULL, NULL);
    if (status == errSecSuccess && expectedIdentity) {
        CFDictionaryRef information = NULL;
        status = SecCodeCopySigningInformation(code, kSecCSDefaultFlags, &information);
        if (status == errSecSuccess) {
            NSDictionary *info = CFBridgingRelease(information);
            NSData *digest = info[(__bridge NSString *)kSecCodeInfoUnique];
            NSMutableString *identity = [NSMutableString string];
            if ([digest isKindOfClass:NSData.class]) {
                const unsigned char *bytes = digest.bytes;
                for (NSUInteger index = 0; index < digest.length; index++) [identity appendFormat:@"%02x", bytes[index]];
            }
            if (![identity isEqualToString:expectedIdentity]) status = errSecCSSignatureFailed;
        }
    }
    if (code) CFRelease(code);
    return status == errSecSuccess || fail([NSString stringWithFormat:@"签名或原厂身份不符（%d）：%@", (int)status, path]);
}

// 自有 Python 启动层只复用本进程刚完成的校验，不能用环境变量跳过验签。
__attribute__((visibility("default")))
int sn_renpy_bootstrap_verified(const char *path) {
    if (!path || !verifiedBootstrapApp) return 0;
    return [verifiedBootstrapApp isEqualToString:canonical(@(path))] ? 1 : 0;
}

static BOOL directory(NSString *path) {
    BOOL isDirectory = NO;
    return path && [canonical(path) isEqualToString:path] &&
        [NSFileManager.defaultManager fileExistsAtPath:path isDirectory:&isDirectory] && isDirectory;
}

static NSString *environment(NSString *key) {
    const char *value = getenv(key.UTF8String);
    return value ? @(value) : nil;
}

static void replaceEnvironment(NSDictionary<NSString *, NSString *> *values) {
    for (NSString *key in NSProcessInfo.processInfo.environment) unsetenv(key.UTF8String);
    for (NSString *key in values) setenv(key.UTF8String, values[key].UTF8String, 1);
}

int main(int argc, char **argv) {
    @autoreleasepool {
        BOOL engine = argc == 2 && strcmp(argv[1], "--engine") == 0;
        if (argc != 1 && !engine) return fail(@"只接受本应用启动或内部 --engine 模式。") ? 0 : 64;
        NSBundle *bundle = NSBundle.mainBundle;
        NSString *app = canonical(bundle.bundlePath);
        if (!app || ![bundle.bundleIdentifier isEqualToString:@"io.dryport.renpy.2118420"] ||
            ![canonical(bundle.executablePath) isEqualToString:[app stringByAppendingPathComponent:@"Contents/MacOS/launcher"]]) {
            fail(@"主程序不在自己所属的应用包内。");
            return 65;
        }
        if (!verifyCode(app, nil)) return 66;
        NSString *resources = [app stringByAppendingPathComponent:@"Contents/Resources"];
        NSDictionary *config = readObject([resources stringByAppendingPathComponent:@"renpy-launch.json"], 4 * 1024 * 1024);
        if (![config[@"protocol"] isEqual:@2] || ![config[@"appid"] isEqual:appid] ||
            ![config[@"version"] isEqual:version] || ![config[@"entry"] isEqual:@"gundog.py"]) {
            fail(@"应用配置不是当前已支持的 Gun-Dog 版本。");
            return 67;
        }
        NSString *runtime = canonical(config[@"runtime"]);
        if (!runtime) { fail(@"共享运行时位置不存在。"); return 68; }
        NSString *binaries = [runtime stringByAppendingPathComponent:@"lib/py3-mac-universal"];
        // 来自已固定原厂归档的 arm64 CDHash；签名有效不能代替确切版本身份。
        NSDictionary *identities = @{
            @"pythonw": @"e0c1b530c07037832dd9f2d92cff7b0c751395a5",
            @"librenpython.dylib": @"6e621b9d98322d07d62dae7fab4ccb9a8edd709a",
            @"libsteam_api.dylib": @"67e7b9e34d91212673ce6c2c2a8255689bb7495f"
        };
        for (NSString *name in identities) {
            if (!verifyCode([binaries stringByAppendingPathComponent:name], identities[name])) return 69;
        }
        NSString *original = [resources stringByAppendingPathComponent:@"autorun"];
        NSMutableDictionary *clean = [@{
            @"PATH": @"/usr/bin:/bin:/usr/sbin:/sbin", @"LANG": environment(@"LANG") ?: @"en_US.UTF-8",
            @"HOME": environment(@"HOME") ?: NSHomeDirectory(),
            @"PYTHONDONTWRITEBYTECODE": @"1", @"PYTHONNOUSERSITE": @"1"
        } mutableCopy];
        NSArray<NSString *> *arguments;
        NSString *python = [binaries stringByAppendingPathComponent:@"pythonw"];
        if (engine) {
            NSString *root = canonical(config[@"state_root"]);
            NSString *state = root ? [root stringByAppendingPathComponent:appid] : nil;
            NSString *saves = [state stringByAppendingPathComponent:@"saves"];
            NSString *logs = environment(@"SN_RENPY_LOGDIR");
            NSDictionary *owner = state ? readObject([state stringByAppendingPathComponent:@".dryport-renpy.json"], 8192) : nil;
            NSString *cwd = canonical(NSFileManager.defaultManager.currentDirectoryPath);
            if (!directory(state) || !directory(saves) || !directory(logs) ||
                ![cwd isEqualToString:state] ||
                ![logs.stringByDeletingLastPathComponent isEqualToString:[state stringByAppendingPathComponent:@"logs"]] ||
                ![owner isEqual:@{@"protocol": @1, @"kind": @"dryport-renpy", @"appid": appid}] ||
                ![environment(@"SN_RENPY_AUTORUN") isEqualToString:original] ||
                ![environment(@"SN_RENPY_SAVES") isEqualToString:saves]) {
                fail(@"游戏启动状态尚未由本应用准备，未执行游戏。");
                return 70;
            }
            [clean addEntriesFromDictionary:@{
                @"HOME": [state stringByAppendingPathComponent:@"home"],
                @"TMPDIR": [[state stringByAppendingPathComponent:@"tmp"] stringByAppendingString:@"/"],
                @"RENPY_PATH_TO_SAVES": [state stringByAppendingPathComponent:@"renpy"],
                @"RENPY_MULTIPERSISTENT": [state stringByAppendingPathComponent:@"multipersistent"],
                @"RENPY_SCREENSHOT_PATTERN": [state stringByAppendingPathComponent:@"screenshots/screenshot%04d.png"],
                @"RENPY_SEARCHPATH": [resources stringByAppendingPathComponent:@"extensions"],
                @"RENPY_LOG_TO_STDOUT": @"1", @"SN_RENPY_AUTORUN": original,
                @"SN_RENPY_SAVES": saves, @"SN_RENPY_LOGDIR": logs,
                @"SN_RENPY_FIRST_RUN": [NSFileManager.defaultManager fileExistsAtPath:[saves stringByAppendingPathComponent:@"persistent"]] ? @"0" : @"1"
            }];
            arguments = @[python, [original stringByAppendingPathComponent:@"gundog.py"], @"--savedir", saves, original, @"sn_native"];
        } else {
            verifiedBootstrapApp = app;
            arguments = @[python, [resources stringByAppendingPathComponent:@"renpy-launch.py"], app];
        }
        // 这里约束引擎初始化环境；不声称能阻止进入 main 之前的动态链接器注入。
        replaceEnvironment(clean);
        void *library = dlopen([binaries stringByAppendingPathComponent:@"librenpython.dylib"].fileSystemRepresentation, RTLD_NOW | RTLD_GLOBAL);
        if (!library) { fail([NSString stringWithFormat:@"原厂引擎库未能加载：%s", dlerror()]); return 71; }
        dlerror();
        int (*entry)(int, char **) = (int (*)(int, char **))dlsym(library, "renpython_main");
        const char *error = dlerror();
        if (error || !entry) { fail(@"原厂库缺少此版本的初始化入口。"); return 72; }
        char **internal = calloc(arguments.count + 1, sizeof(char *));
        if (!internal) { fail(@"启动参数内存分配失败。"); return 73; }
        for (NSUInteger index = 0; index < arguments.count; index++) internal[index] = strdup(arguments[index].UTF8String);
        return entry((int)arguments.count, internal);
    }
}
