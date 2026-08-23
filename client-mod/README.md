# Fabric client bridge

This project owns the Minecraft 26.2 Fabric client, its HeadlessMC runtime, and
the tick-synchronous bridge between one local Python trainer and the Paper
benchmark server. It does not choose seeds, calculate rewards, train models, or
manage the Paper process.

The client listens only on `127.0.0.1:64123`. It forwards versioned protobuf
reset and episode messages on `jump:control`, applies one `NOOP` or `JUMP` on
the next client tick, holds forward during active episodes, and releases every
controlled input on terminal states and transport failures. It relays the
applied action to Paper after that movement tick has completed, so Paper checks
the resulting server-side position without adding another idle client tick.
Training mode does not load Replay Mod; recording mode loads pinned Replay Mod
2.6.27. The recording client waits for Replay Mod startup before joining,
disables rename prompts, replaces its initial idle recording with a clean
session before acknowledging the first capture, finalizes one valid `.mcpr`
per requested checkpoint, and reconnects only when the trainer says another
capture follows.

```console
nix develop ./client-mod
nix build ./client-mod
nix flake check ./client-mod
(cd client-mod && nix fmt)
nix run ./client-mod#headless -- --mode training
nix run ./client-mod#headless -- --mode recording
```

Start the Paper project first. HeadlessMC installs the pinned Fabric Loader
0.19.3 into `client-mod/runtime/<mode>`, joins `127.0.0.1:25565`, and keeps
training and recording game directories separate. Null-driver settings and
client mixins bypass both host audio and speech/audio native initialization.
The launcher allows 60 rendered/task frames per second and the bridge resets
Minecraft's inactivity timer on every client tick. This prevents vanilla's
10-FPS long-AFK throttle from reducing observation/action throughput; the game
and authoritative server simulation still run at the normal 20 ticks per
second.
Set `JUMP_CLIENT_RUNTIME` to place that mutable runtime elsewhere. Recording
source files are finalized beneath `<runtime>/game/replay_recordings`; the
trainer copies verified captures into the selected run directory.
