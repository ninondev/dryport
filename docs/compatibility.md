# Compatibility ledger

Selected recorded results, linked to public summaries in the Summary record column. The summaries preserve historical results; original records are unpublished. Version and original-record context is listed in the summary records; null means unknown. Entries without a name say one title, not named. Chain lists the steps reported as passed; see the legend.

## Unity (Mono) retarget — experimental: installable, not promised

| Date | Game | Engine | Hardware | Summary record | Chain | Frames | Notes |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | A Space for the Unbound | Unity (Mono) | Apple M5, 16 GB | real-machine [entry-01](compatibility-evidence.json) | 1+2+3+4+5 |  | Full chain passed 2026-09-29: 14 checklist steps, force-quit reload byte-identical. |
| 2026-09-29 | one title, not named | Unity (Mono) | Apple M5, 16 GB | real-machine [entry-03](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |
| 2026-09-29 | one title, not named | Unity 5.6 (Mono, Rosetta) | Apple M5, 16 GB | real-machine [entry-04](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |
| 2026-09-14 | one title, not named | Unity (Mono) | Apple M5, 16 GB | real-machine [entry-02](compatibility-evidence.json) | 1 | 60 fps, p99 18.97 ms | Reported checks do not establish the full save/reload verification chain. |

## Wine + DXMT — experimental: installable, not promised

| Date | Game | Engine | Hardware | Summary record | Chain | Frames | Notes |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | one title, not named | Unreal | Apple M5, 16 GB | real-machine [entry-05](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |
| 2026-09-29 | one title, not named | not recorded in the project ledger | Apple M5, 16 GB | real-machine [entry-06](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |
| 2026-09-29 | one title, not named | not recorded in the project ledger | Apple M5, 16 GB | real-machine [entry-07](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |

## mkxp-z (RPG Maker XP/VX/VX Ace) — experimental: installable, not promised

| Date | Game | Engine | Hardware | Summary record | Chain | Frames | Notes |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | one title, not named | RPG Maker VX Ace (RGSS3) | Apple M5, 16 GB | real-machine [entry-08](compatibility-evidence.json) | 1+2+3+4+5 | 60 fps, p99 19.44 ms | Full verification chain reported; performance summary in the columns. |

## NW.js — experimental: installable, not promised

| Date | Game | Engine | Hardware | Summary record | Chain | Frames | Notes |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | Star Sky 3 | Construct 2 | Apple M5, 16 GB | real-machine [entry-09](compatibility-evidence.json) | 1 | 60.01 fps, p99 18.81 ms | Key menus take only mouse input (now clickable); saves sit inside levels or chapters and need real play. Measured 2026-09-29 19:59 PDT: 60.01 fps, frame p50/p95/p99 16.67/17.89/18.81 ms, max 22.94 ms, 0 frames over 50 ms, main process CPU median 1.2%, RSS peak 242.04 MB, machine GPU median 50%. |
| 2026-09-29 | one title, not named | not recorded in the project ledger | Apple M5, 16 GB | real-machine [entry-10](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |

## WebKit — experimental: installable, not promised

| Date | Game | Engine | Hardware | Summary record | Chain | Frames | Notes |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | Koi Farm | Browser entry (system WebKit) | Apple M5, 16 GB | real-machine [entry-11](compatibility-evidence.json) | 1 |  | The WebKit route's one verified title; the route is experimental. Date is the project ledger's record date. |

## Ren'Py — experimental: installable, not promised

| Date | Game | Engine | Hardware | Summary record | Chain | Frames | Notes |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | Stories from Sol: The Gun-Dog | Ren'Py | Apple M5, 16 GB | real-machine [entry-12](compatibility-evidence.json) | 1 |  | The fixed-version Ren'Py route's one verified title; the route is experimental. Date is the project ledger's record date. |

## Godot — experimental: installable, not promised

| Date | Game | Engine | Hardware | Summary record | Chain | Frames | Notes |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | one title, not named | Godot | Apple M5, 16 GB | real-machine [entry-13](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |

## Legend

- 1. draws a picture and can be operated
- 2. saves inside the game
- 3. a normal quit and relaunch loads that save
- 4. a force-quit and relaunch loads the latest save
- 5. original files and existing saves stay byte-identical
- Evidence real-machine: a historical real-game summary, awaiting comparison with original records.
- Evidence mechanism: proven with fixtures or synthetic data in tests; no real game run.
- The Frames column retains recorded measurements; entries without measurements leave it empty.
