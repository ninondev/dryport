# What this tool touches on your machine and your account

## What stays on your machine

The repository contains project source, interface-name metadata, patches and documentation under the scope of [LICENSE](../LICENSE). It contains no games, Steam components, SDKs or third-party runtime binaries. Runtime downloads come from upstream services, without a project mirror.

The tool uses your existing Steam login and does not handle your password. It does not patch the Steam client or change platform mappings. Library integration uses non-Steam shortcuts for the selected account. Games with known anti-cheat are refused, and incomplete risk checks stop before downloads or packaging.

On the Wine route, a game whose main executable carries the Steamworks DRM wrapper (a `.bind` PE section) is refused before packaging with an explanation: it needs a running Windows Steam client inside the same prefix, and this tool does not bypass DRM. Inspection errors also stop packaging; the old override cannot enable it. Native routes do not run that executable; they print a notice and continue (details in [docs/LEGAL.md](LEGAL.md)).

Single-player compatibility fixes may change separate working copies on your machine; Steam originals stay unchanged. Fixes must retain an original copy, record input/output hashes and provide restoration that leaves saves untouched. They must not modify or bypass anti-cheat or DRM, or distribute modified games. These [working-copy requirements](架构.md#游戏工作副本与恢复) do not mean every existing route has passed restore testing.

## The Steam debugging switch

Client downloads currently require Steam's local debugging endpoint. On a machine where it is off, `./bin/dryport steam-debug --enable` (or the button in the app's status bar) creates Valve's own switch file, an empty `.cef-enable-remote-debugging` inside the Steam bundle, after you confirm; nothing else in the bundle is touched, and Steam must be restarted once. While that switch is on and Steam is running, any program on this Mac can control the Steam client through that endpoint; switch it on for installing and off afterwards (`dryport steam-debug --disable`, then restart Steam).

## The Steam bridge

The manual `lib/steambridge/install-into-game.sh` entry accepts only a generated Wine app's
`SN_GAMES/<name>.app/Contents/Resources/game` directory, with matching Wine metadata and appid. It refuses
links beneath the resolved games root and hardlinked DLLs before starting a helper or replacing files;
the games root itself may point to another directory. Arbitrary original folders are refused.

The bridge lets Windows games running through Wine use the Steam identity and achievements from the Steam client you already have logged in. It is off by default and is enabled per game with `dryport bridge enable <appid>`, which states what it does before asking for confirmation. Connections must present the random per-launch token within a two-second total deadline. The token verifies possession, not the game's process identity; same-user isolation is not established, and another program that obtains the token can use the helper. Multiplayer, tickets, and item/economy surfaces are never forwarded. Enabling it means achievements and stats will be written to the logged-in account; account safety is not established.

Local runtime assembly still has to respect the applicable game and runtime terms; see [the unresolved authorization questions](LEGAL.md#authorization-boundaries).

The helper retains at most four pending token checks and eight connections in total, and reads pending tokens concurrently. Authentication, incomplete request frames and stalled responses have two-second total deadlines. A client that has completed a response may idle for 30 seconds before disconnection. Mock API tests require a valid HELLO within three seconds behind three silent clients, twenty pending arrivals, an arrival stream every 0.2 seconds, or an authenticated silent client. This bounds the tested socket delay; it does not bound Steam API execution or establish availability against arbitrary local programs. Games that pause Steam calls for over 30 seconds may need to reconnect; that behavior still needs real-game verification.
