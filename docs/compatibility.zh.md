# 兼容性台账

这是已有结果的选录，公开摘要可通过摘要记录列查阅。原始记录未公开。版本与原始记录背景见摘要记录，null 表示未知。无名条目写作「一款，未点名」。链列列出摘要报告通过的步骤编号，见下面图例。

## Unity（Mono）换原生播放器 — 实验：照样能装，不承诺

| 日期 | 游戏 | 引擎 | 硬件 | 摘要记录 | 链 | 帧率 | 备注 |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | A Space for the Unbound | Unity (Mono) | Apple M5, 16 GB | real-machine [entry-01](compatibility-evidence.json) | 1+2+3+4+5 |  | Full chain passed 2026-09-29: 14 checklist steps, force-quit reload byte-identical. |
| 2026-09-29 | 一款，未点名 | Unity (Mono) | Apple M5, 16 GB | real-machine [entry-03](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |
| 2026-09-29 | 一款，未点名 | Unity 5.6 (Mono, Rosetta) | Apple M5, 16 GB | real-machine [entry-04](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |
| 2026-09-14 | 一款，未点名 | Unity (Mono) | Apple M5, 16 GB | real-machine [entry-02](compatibility-evidence.json) | 1 | 60 fps, p99 18.97 ms | Reported checks do not establish the full save/reload verification chain. |

## Wine + DXMT — 实验：照样能装，不承诺

| 日期 | 游戏 | 引擎 | 硬件 | 摘要记录 | 链 | 帧率 | 备注 |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | 一款，未点名 | Unreal | Apple M5, 16 GB | real-machine [entry-05](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |
| 2026-09-29 | 一款，未点名 | not recorded in the project ledger | Apple M5, 16 GB | real-machine [entry-06](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |
| 2026-09-29 | 一款，未点名 | not recorded in the project ledger | Apple M5, 16 GB | real-machine [entry-07](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |

## mkxp-z（RPG Maker XP/VX/VX Ace） — 实验：照样能装，不承诺

| 日期 | 游戏 | 引擎 | 硬件 | 摘要记录 | 链 | 帧率 | 备注 |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | 一款，未点名 | RPG Maker VX Ace (RGSS3) | Apple M5, 16 GB | real-machine [entry-08](compatibility-evidence.json) | 1+2+3+4+5 | 60 fps, p99 19.44 ms | Full verification chain reported; performance summary in the columns. |

## NW.js — 实验：照样能装，不承诺

| 日期 | 游戏 | 引擎 | 硬件 | 摘要记录 | 链 | 帧率 | 备注 |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | Star Sky 3 | Construct 2 | Apple M5, 16 GB | real-machine [entry-09](compatibility-evidence.json) | 1 | 60.01 fps, p99 18.81 ms | Key menus take only mouse input (now clickable); saves sit inside levels or chapters and need real play. Measured 2026-09-29 19:59 PDT: 60.01 fps, frame p50/p95/p99 16.67/17.89/18.81 ms, max 22.94 ms, 0 frames over 50 ms, main process CPU median 1.2%, RSS peak 242.04 MB, machine GPU median 50%. |
| 2026-09-29 | 一款，未点名 | not recorded in the project ledger | Apple M5, 16 GB | real-machine [entry-10](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |

## WebKit — 实验：照样能装，不承诺

| 日期 | 游戏 | 引擎 | 硬件 | 摘要记录 | 链 | 帧率 | 备注 |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | Koi Farm | Browser entry (system WebKit) | Apple M5, 16 GB | real-machine [entry-11](compatibility-evidence.json) | 1 |  | The WebKit route's one verified title; the route is experimental. Date is the project ledger's record date. |

## Ren'Py — 实验：照样能装，不承诺

| 日期 | 游戏 | 引擎 | 硬件 | 摘要记录 | 链 | 帧率 | 备注 |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | Stories from Sol: The Gun-Dog | Ren'Py | Apple M5, 16 GB | real-machine [entry-12](compatibility-evidence.json) | 1 |  | The fixed-version Ren'Py route's one verified title; the route is experimental. Date is the project ledger's record date. |

## Godot — 实验：照样能装，不承诺

| 日期 | 游戏 | 引擎 | 硬件 | 摘要记录 | 链 | 帧率 | 备注 |
|---|---|---|---|---|---|---|---|
| 2026-09-29 | 一款，未点名 | Godot | Apple M5, 16 GB | real-machine [entry-13](compatibility-evidence.json) | 1 |  | Reported checks do not establish the full save/reload verification chain. |

## 图例

- 1. 出画面且可操作
- 2. 游戏内存档
- 3. 正常退出重开能读档
- 4. 强退重开能读最新档
- 5. 原件与既有存档逐字节不变
- 证据 real-machine：历史真游戏结果摘要，尚未与原始记录核对。
- 证据 mechanism：用夹具或合成数据在测试里证明（未跑真游戏）。
- 帧率列保留已记录的测量结果；未测量的条目该列为空。
