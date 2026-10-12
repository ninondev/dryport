# Contributing

PRs welcome. Issues are read by the maintainer. Before merge, post the required
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
nice -n 10 python3 -m pytest -q -p no:cacheprovider tests --confcutdir=tests
```

On a checkout with the usual developer setup, `python3 -m pytest -q tests` works the same. The suite runs offline: no games, no Steam, no network.

```bash
python3 tools/release-check.py
python3 tools/release-check.py --history
```

The current working tree and Git history are checked separately. A clean source snapshot does not make older commits safe to publish. The companion app has its own tests: `nice -n 10 swift test` inside `app/`. Build it with `nice -n 10 swift build` inside `app/`; this does not launch a GUI. Building a game wrapper requires separately obtained runtimes, game ownership and the checks in [usage](docs/usage.md).

The private identifier inventory is never bundled. A maintainer can set
`DRYPORT_PRIVATE_INVENTORY` to an existing private inventory file for export tests.
An explicitly configured but missing or invalid file fails the check. Without that
private input the public tests report an explicit skip; that skip is not privacy clearance.
