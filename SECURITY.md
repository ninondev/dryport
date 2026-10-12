# Security policy

## What counts as a security problem here

Report it privately if you find any of the following:

- unexpected writes outside the generated apps and configured tool folders (`SN_RUNTIMES`, `SN_GAMES`, caches and state), including writes beyond the documented Steam client operations;
- unintended Steam client or account changes beyond the documented downloads, shortcut changes, confirmed debugging switch and opt-in bridge activity;
- disclosure of the per-launch token outside its intended launch path, or bridge calls accepted without valid token possession;
- anything that exposes account data: your Steam identity, library contents, stats or achievements.

Also welcome: the runtime-bundle installer fetching or unpacking something the manifest checks should have refused, and any way a hostile manifest, archive or downloaded runtime escapes the folders above.

The token verifies possession and does not establish process identity or same-user isolation. Another program that obtains it can use the helper. The bridge is off by default; enabling it permits the documented stats and achievement writes. See [the account and filesystem boundaries](docs/safety.md).

## How to report

Use GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability"). Do not open a public issue for any of the above. Include what you ran, what you expected and what happened; never include account credentials.

Local app builds use ad-hoc signing and are not notarized. Archive hashes and codesign structure checks do not authenticate the publisher; remote app updates are off in this preview.
