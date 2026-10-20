# Contributing

PRs welcome. Before merge, post the required
[CLA](CLA.md) comment: "I have read the CLA and I agree to it."

## Reporting a game

Open an issue with the game report form and fill in:

- the game's title and build (the version shown in its files or store page);
- the engine and its version, if you know it, and the player architecture (x86 or arm64 build);
- your Mac model and macOS version;
- what you saw (window, error text, frame rate) and reproducible steps from launching the app.

Never attach: game assets, account identifiers, library exports, or saves. A text description is enough.

## What counts as a working route

A route is labelled "promised" only with evidence: two real games per route passing the full five-step chain — the game draws and can be operated; it saves inside the game; a normal quit and relaunch loads that save; a force-quit and relaunch loads the latest save; original files and existing saves stay byte-identical. Until then a route stays "experimental": installable, with no promise that it plays through. Claims state which evidence they carry: real-machine (a real game showed a picture and could be operated, with a recorded result) or mechanism (proven with fixtures or synthetic data).

## Running the tests

```bash
python3 -m pytest -q -p no:cacheprovider tests --confcutdir=tests
```

On a checkout with the usual developer setup, `python3 -m pytest -q tests` works the same. The suite runs offline: no games, no Steam, no network.

The companion app has its own tests: `swift test` inside `app/`. Build it with `swift build` inside `app/`; this does not launch a GUI. Building a game wrapper requires separately obtained runtimes, game ownership and the checks in [usage](docs/usage.md).
