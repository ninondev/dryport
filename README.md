# dryport

dryport runs some Windows-only games on Apple silicon Macs. For Unity games built with Mono it skips Wine: it reads the game's own D3D11 shaders, rewrites them as Metal, and runs the game on a matching macOS Unity player. Other engines get their own routes, listed under [How it works](#how-it-works). This is an early preview.

On 2026-09-29, **A Space for the Unbound** passed all five checks on an Apple M5 Mac with 16 GB, including byte-identical reload after force quit. An anonymous RPG Maker VX Ace game also passed all five, at **60.0 fps, frame p99 19.44 ms**.

These numbers come from my own test records. The original records are unpublished, some version fields are missing, and these games were not rerun for this preview. The [per-route ledger](docs/compatibility.md) and [summary records](docs/compatibility-evidence.json) keep the dates, stages and gaps. The five checks are: a picture with working controls, saving in the game, a normal reload, a reload after force quit, and original files and saves left untouched. A game at stage 1 only has a picture and controls; that says nothing about saves or the whole game. Frame p99 is the 99th percentile of frame time, sampled by the [benchmark tool](tools/bench-game.py). The sampling settings are unpublished too.

| Title or anonymous entry | Route | Recorded stages | Recorded performance |
|---|---|---|---|
| A Space for the Unbound (entry-01) | Unity Mono / macOS player + Metal | 1 to 5 | Not recorded |
| Anonymous entry-02 | Unity Mono | 1 only | 60.0 fps; p99 18.97 ms |
| Anonymous entry-03 | Unity Mono | 1 only | Not recorded |
| Anonymous entry-04 | Unity 5.6 / Rosetta | 1 only; shader path fails | Not recorded |
| Anonymous entry-05 | Unreal / Wine | 1 only | Not recorded |
| Anonymous entry-06 | Wine | 1 only | Not recorded |
| Anonymous entry-07 | Wine | 1 only | Not recorded |
| Anonymous entry-08 | RPG Maker VX Ace / mkxp-z | 1 to 5 | 60.0 fps; p99 19.44 ms |
| Star Sky 3 (entry-09) | Construct 2 / NW.js | 1 only | 60.01 fps; p99 18.81 ms |
| Anonymous entry-10 | NW.js | 1 only | Not recorded |
| Koi Farm (entry-11) | System WebKit | 1 only | Not recorded |
| Stories from Sol: The Gun-Dog (entry-12) | Pinned Ren'Py | 1 only | Not recorded |
| Anonymous entry-13 | Godot | 1 only | Not recorded |

## Limits

Developer preview. Few titles have recorded results. Apple silicon Macs only; some older players still require Rosetta. All routes are experimental. Engine detection is not a promise that a game runs.

Unity Mono is the native retargeting route. Unity IL2CPP has no native retargeting route here. Unity 5.x shader translation is not implemented: the recorded 5.6 build generated no Metal programs and showed pink output. UAV-dependent passes can be skipped. Missing authorized macOS FMOD libraries can leave games silent. Windows-only native plugins can prevent a game from working. This is not general Windows compatibility.

A complete first installation on a clean Mac is unverified. mkxp-z and KiriKiri require prepared local builds. Legacy archives may lack licence texts; affected binary bundles stay unpublished until exact source, licenses and dependency notices are verified. Steam and Cloud interoperability, account safety, downloaded-app first launch and whole-game playthroughs remain unverified for this preview. Remote preview updates are disabled.

## How to try it

Clone the repository and start with the read-only check:

```bash
git clone https://github.com/ninondev/dryport && cd dryport
bash bin/install.sh --check          # read-only: what is ready, what is missing
./bin/dryport survey                 # which games have a route; downloads nothing
./bin/dryport install <appid>        # checks risk and builds a separate local .app
```

For the local preview, run the last three commands from this checkout. `survey` needs local library metadata; it does not fetch game content. Before `install`, read [usage](docs/usage.md) for runtime preparation, the logged-in Steam client and its debugging switch. Runtime manifests require a registered SHA256; `--untrusted-local` explicitly trusts a local runtime folder. The example is not evidence of clean-machine installation.

You must own your games. The tool modifies only local working copies. It uses the Steam client you are already logged in to. It never handles your password and never patches Steam. Games with known anti-cheat are refused. Original game files stay untouched. On Wine routes, DRM-wrapped executables are refused. Native routes do not execute the Windows launcher; permission to retarget a particular game's assets still needs review.

While Steam's local debugging switch is on and Steam runs, any program on the Mac can control the client through that endpoint. Turn it off after installation. The optional Steam client bridge for Wine routes is off by default and can write achievements and stats to the logged-in account. Multiplayer, tickets and items are never forwarded. Cloud sync is a separate persistent opt-in per game; real cross-device save safety is unverified. Read [safety](docs/safety.md) and [legal boundaries](docs/LEGAL.md) before enabling integration.

## How it works

The Unity route reads the shipped D3D11 shader bytecode and Unity reflection data, reconstructs resource declarations, translates through HLSLcc, and repacks Metal programs for a matching macOS player. Player versions from 2017.3 to 2020.1 use Rosetta; newer supported players can use arm64. The [shader notes](lib/unity-retarget/README.md) record shader-generation checks and failures. Generated shader counts alone do not prove correct rendering.

Other engine routes remain in the preview:

| Your game was made with | It runs here on | Tier |
|---|---|---|
| RPG Maker XP, VX, VX Ace | [mkxp-z](https://github.com/mkxp-z/mkxp-z), native arm64 | experimental |
| RPG Maker MV, MZ and other NW.js games | NW.js, native arm64 | experimental |
| Unity (Mono, 2017.3 and later) | matching macOS player and Metal; 2017.3 to 2020.1 through Rosetta | experimental |
| Ren'Py (pinned Gun-Dog), Godot, LÖVE 11.5, KiriKiri, Koi Farm | engine-specific runtimes; Koi Farm uses system WebKit | experimental |
| Other eligible Windows games | Wine + DXMT, or Wine Staging without D3D11 | experimental |

Unsupported Ren'Py versions are refused. Local patches change mkxp-z and dependency build files. Route details: [how it works](docs/how-it-works.md). Generated apps can be rebuilt with `rewrap`; `clean` previews cleanup. Operational commands: [usage](docs/usage.md). The [Chinese README](README.zh.md) is older and describes the broader engine routes.

## What comes next

Publish sanitized original measurement receipts and fill in the missing game, player, macOS and tool versions. Recheck rendering, audio and the five-stage save chain on more Unity games. Verify setup on a clean Mac. Then compare dryport with CrossOver and Highball on the same Mac, with the same game builds and settings, on frame rate, frame-time percentiles and save/reload results. No comparison numbers are available yet.

## Contribute and license

PRs are welcome. Follow [CONTRIBUTING.md](CONTRIBUTING.md), including the required [CLA](CLA.md) comment before merge. Do not attach game files, saves, account identifiers or private library exports.

Copyright (c) 2026 Ninon. Project source is [GPL-3.0-only](LICENSE). Third-party components keep their own licenses. No games, proprietary runtime binaries, Steam components or SDKs are bundled. dryport is not affiliated with Valve, Unity or any game publisher. See [LEGAL.md](LEGAL.md) and the [runtime inventory](docs/LEGAL.md).
