# Source, runtime provenance and release boundaries

Inventory dated 2026-09-12. This document describes the project and its upstream sources. It does not certify that every use of every game is authorized.

Preview release scope: source only. Affected runtime bundles must not be published until their exact corresponding source, applicable licence texts and complete dependency notices are verified. This release hold does not establish legal clearance.

dryport is not affiliated with Valve, Unity or any game publisher. Users must own their games and follow the applicable terms. No games, game assets, proprietary players, Steam components or SDKs are redistributed. The tool modifies only local working copies; users must not distribute generated game apps.

Copyright (c) 2026 Ninon. Project source is licensed GPL-3.0-only. Third-party components retain their own terms.

## Repository contents

The repository contains the project's scripts, Python/C/C++/Swift/Ruby sources, generated interface-name metadata, local patch descriptions and documentation. [The GPL-3.0-only license](../LICENSE) covers the project's original code. Upstream code appearing as patch context retains its original notices and terms.

No runtime executables, games, Steam client components, Steamworks SDK or FMOD binaries are included. `tools/release-check.py` reports the file count of the actual source snapshot, rejects identified binaries and private data, and can produce an archive without `.git`. This is a mechanical content check, not a proof of source authorship or legal clearance.

`lib/steambridge/exports.json` contains export names extracted from a user-owned Windows library, not library code. Reproduce the list with `tools/dump-exports.sh <steam_api64.dll>`; `--json` writes the JSON form. API-name extraction does not grant rights to redistribute the input DLL.

## Runtime sources

The table preserves the recorded source and license inventory dated 2026-09-12. Current terms and the dependencies of actual binaries require verification. Retain the notices delivered with each fetched version.

| Component | Recorded upstream source and license | Project entry point / availability |
|---|---|---|
| Wine from CodeWeavers public source | [Public source archive](https://www.codeweavers.com/crossover/source); Wine core LGPL-2.1-or-later, bundled components retain their licenses | `tools/build-wine.sh`; pinned source archive, local x86_64 build. It does not install or present itself as the CrossOver product. |
| DXMT | [Upstream LICENSE](https://github.com/3Shain/dxmt/blob/main/LICENSE), LGPL-2.1-or-later; [releases](https://github.com/3Shain/dxmt/releases) | `tools/install-dxmt.sh` installs an already obtained v0.80 runtime into a local Wine prefix; this script is not a downloader. |
| FreeType | [Upstream licensing](https://freetype.org/license.html), FreeType License or GPLv2 | The Wine builder uses the FreeType source included in its pinned source archive and retains its notices. |
| mkxp-z | [Upstream COPYING](https://github.com/mkxp-z/mkxp-z/blob/dev/COPYING), GPL-2.0; dependencies have their own notices | Manual source build, `tools/patch-mkxp-deps.sh`; no complete clean-machine builder yet. |
| krkrsdl2 | [Upstream LICENSE](https://github.com/krkrsdl2/krkrsdl2/blob/main/LICENSE), MIT plus dependencies | Manual source build; `lib/krkr-wrap.sh` uses the local result. |
| NW.js | [Upstream LICENSE](https://github.com/nwjs/nw.js/blob/main/LICENSE), MIT for NW.js; Chromium/Node.js and bundled credits also apply; [official downloads](https://dl.nwjs.io/) | `bin/install.sh`, pinned version 0.115.0 for macOS arm64. Preserve the runtime's credits. |
| Godot export templates | [Upstream LICENSE](https://github.com/godotengine/godot/blob/master/LICENSE.txt), MIT plus bundled dependencies | `lib/godot-fetch-template.py`, upstream templates matched to the game's version. |
| HLSLcc | [Upstream license.txt](https://github.com/Unity-Technologies/HLSLcc/blob/master/license.txt), MIT-style grant and bundled bstring BSD-style terms | `bin/install.sh` builds the local translator. |
| Unity macOS player | [Official download CDN](https://download.unity3d.com/); [Unity software terms](https://unity.com/legal/editor-terms-of-service/software) | `bin/dryport` fetches the matching player/support package. Proprietary runtime, no redistribution by this project. Authorization for retargeting third-party games remains to be established. |
| Valve `libsteam_api.dylib` | [Steam Subscriber Agreement](https://store.steampowered.com/subscriber_agreement/) and applicable Valve terms | `lib/steambridge/helper.c` loads only the copy in the installed Steam client bundle. The bridge build neither links nor copies it. This restriction describes the bridge, not every historical native wrapper. |
| greenworks / Steamworks SDK | [greenworks MIT license](https://github.com/greenheartgames/greenworks/blob/master/LICENSE); SDK terms supplied by Valve | Optional native integration. The user obtains any required SDK under their own account; it is not bundled or fetched through login automation. |
| FMOD | [FMOD licensing](https://www.fmod.com/licensing) | User-obtained runtime where authorized. The repository contains a stub for missing functions; a stub is not equivalent to working audio. |
| Anti-cheat metadata | [AreWeAntiCheatYet MIT license](https://github.com/AreWeAntiCheatYet/AreWeAntiCheatYet/blob/master/LICENSE) | `tools/update-anticheat-db.sh` / `bin/install.sh`; public data fetched locally, not an exhaustive safety assessment. |
| Python packages | [UnityPy](https://pypi.org/project/UnityPy/), [lz4](https://pypi.org/project/lz4/), [dnfile](https://pypi.org/project/dnfile/), [icoextract](https://pypi.org/project/icoextract/) | `lib/unity-retarget/requirements.txt`; package metadata and bundled license files govern those installations. |

Ren'Py installation is restricted to the pinned Gun-Dog version and remains experimental. The historical summary reports one real-game result; its original record is unpublished. The runtime is fetched from the official distribution. FNA remains a detected candidate, not an implemented installation route; its runtime is not installed by this project today.

## Authorization boundaries

Distributing only source avoids redistributing proprietary runtime binaries. It does not remove every license, contractual or trademark obligation. The recorded Unity terms cover runtime use and modifications as well as distribution; their grants are primarily framed around creators' own projects. Those grants do not by themselves establish authorization to retarget someone else's shipped game. A matching player on an official CDN is a technical source, not proof of that authorization. [Unity software terms, sections 2 and 9](https://unity.com/legal/editor-terms-of-service/software).

Steam's agreement also distinguishes personal-use access, game-specific terms and restrictions on modification, with stated exceptions for other permissions or applicable law. Whether a particular game's retargeting or bridge use is authorized must be assessed under the applicable terms and jurisdiction. This project has not obtained a general Valve or Unity authorization for all such uses. [Steam Subscriber Agreement, sections 1.B and 2.G](https://store.steampowered.com/subscriber_agreement/).

The project does not patch Steam, change compatibility mappings, distribute runtime mirrors, handle passwords, or implement authentication tickets, networking, servers or item economies in the bridge. Anti-cheat detection blocks automatic packaging. The explicit multiplayer override does not override anti-cheat refusal or missing risk evidence. Upstream patches are maintained locally.

Native wrapper assembly can rewrite the generated copy's shaders and plugin layout. The source game is intended to remain separate; saying that every assembled game byte is unchanged would be inaccurate. The current download mechanism depends on Steam's local debugging endpoint. The tool checks it, explains missing setup, and after user confirmation can create Valve's own empty switch file (`.cef-enable-remote-debugging`) inside the Steam bundle; `dryport steam-debug --disable` removes that file again, and no other file in the bundle is touched. While the switch is on and Steam is running, any program on this Mac can control the Steam client through that endpoint; the intended use is on for installing and off afterwards.

The working name is dryport. Public release still needs clean-machine installation evidence, review of upstream notices and provenance, and resolution of the Unity/game authorization questions above. Do not distribute generated game apps or include personal Git history in a source release.

## DRM-wrapped executables (added 2026-09-13)

Some games ship a main executable wrapped by the Steamworks DRM wrapper (a `.bind` PE section). On the Wine route the tool refuses to package such a game: the wrapper needs a running Windows Steam client inside the same prefix to decrypt the executable, the bridge cannot stand in for that, and the project does not strip or bypass the wrapper. Native routes (Unity Mono retargeting, Ren'Py, NW.js and the others) never execute the Windows executable at all; they run the game's own assemblies and assets on a macOS runtime. The wrapper's check therefore does not run on those routes, not because anything defeats it but because the wrapped file is not used. Whether running a legitimately downloaded game's assets on another runtime without executing its DRM-wrapped launcher is acceptable under the game's and Steam's terms is a legal question the project has not resolved; the installer now says so when it detects the section, and the applicable authorization must be reviewed before public release.

## Prebuilt runtime bundles (added 2026-09-13)

The runtime registry describes locally built Wine with DXMT, mkxp-z, krkrsdl2, steambridge and the HLSLcc command-line tool. `lib/runtimes.json` lists them and `tools/runtime-bundle.py` packs and installs them. The source-only preview does not include these binary bundles; distributing them would create additional obligations:

- Wine and DXMT are LGPL-2.1-or-later: each published bundle must be accompanied by, or point to, the exact corresponding source (the pinned CodeWeavers archive plus the patches in this repository, and the DXMT release used) and keep every upstream notice inside the archive.
- mkxp-z is GPL-2.0: the complete corresponding source of the built copy (upstream commit plus the local patches under `tools/`) must be offered with the bundle, and the bundle must not be combined with anything that would restrict GPL rights.
- krkrsdl2 (MIT) and HLSLcc (MIT-style with bstring BSD-style terms) require the license texts and notices to travel with the binaries.
- Unity players, Steam client components, Valve libraries and any game bytes are never packed; `lib/runtimes.json` does not list them and `runtime-bundle.py` cannot be pointed at them without editing the registry.

The manifest records the version string of each runtime as seen on the packing machine and the SHA256 of each archive; installation refuses an archive whose SHA256 does not match. Release hosting and identity still require configuration. Legacy archives may lack licence texts; successful installation does not establish legal clearance.

## HLSLcc source boundary

No upstream HLSLcc or bstring implementation is vendored in this source preview. `lib/unity-retarget/hlslcc-cli.cpp` is the project's GPL-3.0-only command-line adapter, built against separately obtained HLSLcc. The recorded upstream terms are an MIT-style grant for HLSLcc and BSD-style terms for bstring. The runtime registry requires both upstream license files when packing a translator binary; the adapter's GPL license does not replace them. Upstream license text and exact corresponding dependency notices must be checked before any binary distribution.
