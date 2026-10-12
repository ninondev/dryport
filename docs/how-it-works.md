# How it works

dryport recognizes engines from Steam metadata and file lists. RPG Maker XP/VX/VX Ace uses mkxp-z; MV/MZ and other eligible NW.js games use NW.js. Unity Mono uses a matching macOS player and Metal, with 2017.3 to 2020.1 players running through Rosetta and older players also requiring it. Ren'Py is restricted to the pinned Gun-Dog version, LÖVE requires 11.5, and Koi Farm uses system WebKit. Godot and KiriKiri use their corresponding runtimes. Unsupported Ren'Py versions are refused. Eligible fallback routes use Wine + DXMT or Wine Staging without D3D11. Local patches modify mkxp-z and dependency build files; sources and unresolved licence obligations are recorded in [LEGAL.md](LEGAL.md).

Engine detection is not a promise that a game will run; the [route ledger](compatibility.md) separates real-game tests from sample-only and unimplemented routes. Engine identification does not establish playability.

## Route tiers

| Tier | Routes |
|---|---|
| Promised | none as of 2026-10-01 (the deadline rule moved the last four promised routes to experimental) |
| Experimental | Unity (Mono; before 2017.3 through Rosetta), Wine + DXMT, mkxp-z (RPG Maker XP/VX/VX Ace), NW.js, WebKit, pinned Ren'Py, Godot, LÖVE, KiriKiri |

Experimental routes still install, and the app and the installer label them; there is no promise that they play through. A promised route that has not passed the full check on two real games by 2026-09-30 moves to experimental too. That rule was applied on 2026-10-01 with the evidence then in the project ledger: no route had two full-chain games, so every route is experimental now. The tiers live in one file, `lib/route-tiers.json`, and the [compatibility ledger](compatibility.md) shows the current state per route.

## Downloads and the library

Downloads go through the logged-in Steam client (never a password; see [safety.md](safety.md)). Accessible family-shared games use the same catalog and actions as owned games. macOS releases open in the official Steam client. Missing metadata keeps a game visible while its route is checked. Downloads ask Steam for current authorization, select base-game depots and verify their files before publishing a complete copy. A real two-depot download passed file verification; Windows redistributables still need preparation, and gameplay requires separate testing. Catalog updates preserve previous versions and check the selected account. The default scan reuses valid classification caches and reads local file manifests when needed; it does not download game content or fetch missing manifests.

## Route specifics

Real-game results below are historical summaries; original records are unpublished.

Koi Farm has a version-specific WebKit route on Apple Silicon with macOS 14 or later. The installer verifies every browser resource and builds a native app that keeps the complete original game. The generated app uses system frameworks and runs without Python or this checkout. Installation, moving fish, normal exit and restoring that move in a new process have been tested; the full tutorial, audible sound, extended play and low-end hardware performance remain unverified. Other Electron games do not inherit this compatibility.

Stories from Sol: The Gun-Dog has a fixed-version Ren'Py route. Installation checks the complete game against the tested version and builds an app with its own native entry, sharing the original upstream arm64 runtime. Building requires Apple's command-line developer tools. One early story save has been restored in a new process; full-story coverage and other Ren'Py games remain unverified. Saves live under `SN_GAMES/.renpy-state/2118420` and survive app uninstall. The upstream ZIP files remain in a persistent runtime cache for later verification and reinstallation.
