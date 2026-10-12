# Preview release notes

Development preview of dryport. Not made or endorsed by Valve.
The public name is still being chosen; `dryport` stands in for it.

## What is promised, what is experimental

The tiers live in `lib/route-tiers.json`; the installer, the app and the compatibility page all read the same file. As of 2026-10-01:

| Tier | Routes |
|---|---|
| Promised for the preview | none — no route had two full-chain games by the 2026-09-30 deadline |
| Experimental | Unity (Mono; before 2017.3 through Rosetta), Wine + DXMT, mkxp-z (RPG Maker XP/VX/VX Ace), NW.js, WebKit, pinned Ren'Py, Godot, LÖVE, KiriKiri |

A promised route that has not passed the full verification chain on two real games moves to experimental; the deadline rule was applied on 2026-10-01 with the evidence then in the ledger, which left every route experimental. "Promised" still means a development preview, not a finished product.

## What is verified

These are selected historical ledger summaries linked from the [compatibility page](compatibility.md);
original records are unpublished.

The recorded machine class was Apple M5, 16 GB. This is historical context, not a new hardware test.

- A Space for the Unbound (Unity Mono) passed the full five-step chain — picture and control, in-game save, normal-quit reload, force-quit reload, byte-identical originals — on 2026-09-29.
- One RPG Maker VX Ace title passed the same chain on 2026-09-29; measured the same day: 60.0 fps, frame p99 19.44 ms, 0 frames over 50 ms.
- One Construct 2 title (NW.js) ran a 10-minute fixed scene on 2026-09-29: 60.01 fps, frame p99 18.81 ms; its progress saves were not reached.
- One Unreal title, two further Wine titles and the WebKit, pinned-Ren'Py, Rosetta-Unity and Godot routes have each run at least one real game (picture and control) without yet passing the full chain; see the ledger for each game's exact state.

## What is not verified

- 8 GB machines: no device was tested.
- Power draw: not measured; it needs administrator access.
- A fresh macOS standard account with no developer present: not verified.
- An outside user completing the installation: not verified.
- A clean Mac completing the whole setup without assistance: not verified.
- Real Steam behavior in the bridge beyond the games listed in the ledger.

## What this tool touches

- Downloads go through the Steam client you are logged in to; your password is never handled. The client connection needs Steam's local debugging switch: while it is on and Steam runs, any program on this Mac can control the client through it. Turn it on for installing and off afterwards (`dryport steam-debug --disable`, then restart Steam).
- The Steam bridge for Wine routes is off by default and is enabled per game (`dryport bridge enable <appid>`). Enabled, it uses the logged-in account's identity, stats and achievements, and writes achievements and stats to that account; multiplayer, tickets and item surfaces are never forwarded; account safety is not established.
- The Wine route refuses to package a game whose main executable carries the Steamworks DRM wrapper; native routes do not run that executable, print a notice and continue.

Details: [safety.md](safety.md).

## First launch on macOS

The build uses ad-hoc signing and is not notarized. Downloaded-app first launch remains unverified.
The macOS approval path for downloaded builds also remains unverified.

## Updating and going back

- Built apps never update themselves. `dryport rewrap <appid>` regenerates an installed app with the current launcher; the previous app is kept under `SN_GAMES/.uninstalled` and can be restored manually.
- Remote updates are off in this preview; update from an explicitly trusted local folder using `SN_UPDATE_SOURCE` or `--from`. Versions use one to four dotted numeric components. A replacement must be newer than the installed version; an unknown version is refused. The manifest and bundle versions must match. Archive SHA256, confined paths, archive members, the app revision and its codesign structure are checked; these checks do not establish publisher identity. The old app is retained for `bin/self-update.sh --app <path> --rollback`.
- Updates write to the app and the tool's update cache. Games, saves, installed runtime binaries and Steam are not modified.

For installation and cleanup: [usage.md](usage.md).
