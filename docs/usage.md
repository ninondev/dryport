# Install, update, clean up

A clean source snapshot does not make older commits safe to publish.

## Try the source preview

Start in your local checkout. macOS on Apple Silicon, Python 3 and Node.js are required. A Swift build also requires Apple's command-line developer tools.

```bash
bash bin/install.sh --check     # Read-only: report what is ready and what is missing
bash bin/install.sh --plan      # Show installation steps without downloads or changes
bash bin/install.sh             # Prepare the basic risk database
```

Normal `dryport install <appid>` prepares missing risk data and selects the matching runtime after engine detection. You do not need to choose these runtime flags. To prepare a runtime separately:

```bash
bash bin/install.sh --runtime-only --with-unity # Unity translation tools
bash bin/install.sh --runtime-only --with-nwjs  # NW.js for MV/MZ
bash bin/install.sh --runtime-only --with-love  # Pinned LÖVE 11.5
bash bin/install.sh --runtime-only --with-renpy # Pinned Ren'Py 8.1.3
```

`--runtime-only` prepares only the selected runtimes. It does not check Steam login, the debugging endpoint or risk data; a ready runtime does not mean game downloads are ready.

This is a source-only preview. Affected runtime bundles remain withheld until exact source, licence texts and dependency notices are reviewed; see [LEGAL](LEGAL.md#preview-release-scope-source-only). A clean Mac completing the whole setup without assistance has **not** been verified.

Wine can be prepared with [the local build script](../tools/build-wine.sh):

```bash
bash tools/build-wine.sh --check       # Report prerequisites; no Wine launch or download
bash tools/build-wine.sh --help        # List build tools and supported source-archive options
```

Building needs the tools and Rosetta listed by that script, and a matching source archive. The script builds Wine and FreeType; it does not include DXMT. [DXMT preparation](../tools/install-dxmt.sh) is separate.

There is no automated source-build installer for mkxp-z or KiriKiri. Build the upstream projects manually and prepare the products and checks registered in [lib/runtimes.json](../lib/runtimes.json). mkxp-z also uses [dependency patches](../tools/patch-mkxp-deps.sh) and [the path patch](../tools/mkxp-nfc-path-fix.patch). These are preparation references, not proof of a complete first installation.

If you already have a trusted local runtime bundle, `tools/runtime-bundle.py install --from <local folder>` checks the registered manifest SHA256, archive hashes and confined output paths. An unregistered manifest requires explicit `--untrusted-local` trust for that local folder; it does not establish publisher identity or distribution clearance. No downloadable project runtime bundle is promised here.

Remote companion-app updates are off in this preview; update from a trusted local folder. Cloud sync is off until you persistently enable it for one game with `dryport cloud enable <appid>`; `cloud disable <appid>` turns it off again. Pull conflicts stop launch before push, and push conflicts are reported after the game exits.

## Install a game

For a game you can access, including family sharing, replace `<appid>` with the number in its Steam store URL:

```bash
./bin/dryport check <appid>
./bin/dryport install <appid>
./bin/dryport survey        # List accessible games, including family sharing; no game downloads
```

Installation checks risk, downloads with the logged-in client, identifies the engine, builds a separate `.app` and adds a shortcut. What the client connection and the optional bridge touch is described in [safety.md](safety.md). When the endpoint stays unavailable, the CLI explains the manual download path. Some engine routes still require manual work. Existing downloads need a complete delivery receipt for automatic reuse; a legacy single-depot marker is insufficient.

## The companion app

```bash
bash app/build.sh
```

The build script prints the app's location. Use the library discovery button to create a route catalog. A standalone build travels inside the bundle:

```bash
bash app/build.sh --standalone   # self-contained app: the toolchain travels inside the bundle
```

## Files and cleanup

| Variable | Default | Contents |
|---|---|---|
| `SN_HOME` | This checkout | Project source and documentation |
| `SN_RUNTIMES` | `~/Games/runtimes` | Upstream runtimes and build caches |
| `SN_GAMES` | `~/Games` | Game copies, generated apps and private library data |

```bash
./bin/dryport doctor
./bin/dryport clean
./bin/dryport archive <appid>
./bin/dryport restore <appid>
./bin/dryport uninstall <appid> [--saves]
./bin/dryport rewrap <appid>
```

`clean` previews space usage. Keep Steam open and logged in for `archive`, `restore` and `uninstall`: every shortcut change goes through the client. `archive` is reversible: the client removes the shortcut, the result is read back, and the matching generated app is moved intact under `SN_GAMES/.uninstalled`; downloads and saves stay put and no space is freed. If removal cannot be confirmed, the app and shortcut backup remain available. `restore` moves the newest archive back and re-adds it through the official path, without downloading or rebuilding. `uninstall` is irreversible: it archives first, then deletes only what this tool created, the download directory carrying our marker and this game's archived apps. It lists every path with its size before asking; saves are a separate line and are kept unless you pass `--saves`; the freed amount is reported as the difference in free disk space, because `du` overcounts APFS clones. `--keep-download` is still accepted and means `archive`. `rewrap` regenerates an installed app with the current launcher: same name and path, the Steam shortcut is untouched, nothing is downloaded, and the previous app is moved under `.uninstalled` so the step can be reversed. Generated apps do not update themselves when this tool is upgraded; this is the upgrade path for them.
