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
The one persistent client always loads pinned Replay Mod 2.6.27. It waits for
Replay Mod startup before joining, starts logically cut, opens one clip for each
accepted reset, and closes and splits that clip after its terminal tick. At
trainer command end it disconnects from Paper once so Replay Mod can
post-process every split, offers the resulting files sequentially, and
reconnects after the batch even when retention fails. An active episode is
marked partial when the command is interrupted or the trainer disappears.

```console
nix develop ./client-mod
nix build ./client-mod
nix flake check ./client-mod
(cd client-mod && nix fmt)
nix run ./client-mod#headless
```

Start the Paper project first. HeadlessMC installs the pinned Fabric Loader
0.19.3 into `client-mod/runtime/client` and joins `127.0.0.1:25565`. Null-driver
settings and client mixins bypass both host audio and speech/audio native
initialization.

The launcher deliberately uses an offline Minecraft session against the local
offline-mode Paper server. The client routes profile-key lookup through authlib's
offline service to avoid an unnecessary authenticated request. Vanilla Realms
requests can still log non-fatal authorization errors; they do not prevent the
benchmark connection.

The launcher allows 60 rendered/task frames per second and the bridge resets
Minecraft's inactivity timer on every client tick. This prevents vanilla's
10-FPS long-AFK throttle from reducing observation/action throughput; the game
and authoritative server simulation still run at the normal 20 ticks per
second.
Set `JUMP_CLIENT_RUNTIME` to place that mutable runtime elsewhere. Recording
source files are finalized beneath `<runtime>/game/replay_recordings`; this is
staging, not the canonical archive. The client deletes a source only after the
trainer acknowledges its validated, atomically published copy. Failed,
unacknowledged, and unexpectedly disconnected batches remain there for manual
recovery. `JUMP_CLIENT_FINALIZATION_TIMEOUT_MILLIS` changes the default
five-minute finalization ceiling.
