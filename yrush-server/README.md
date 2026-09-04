# YRush server

This project packages one Paper 26.2 process and the pinned YRush v1.3.2
release. It uses a normal generated world and has no custom server plugin or
Protobuf dependency.

```console
nix develop ./yrush-server
nix build ./yrush-server
nix build ./yrush-server#oci
nix run ./yrush-server#image
nix flake check ./yrush-server
(cd yrush-server && nix fmt)
nix run ./yrush-server#server
```

Mutable state defaults to `yrush-server/runtime` locally and `/data` in the
image. Kubernetes mounts a disk-backed `emptyDir` there; Paper caches, the
generated world, region files, plugin state, and logs therefore share the
node's local ephemeral filesystem. No server volume claim is used.

Configuration is external:

- `YRUSH_EXPECTED_CLIENT_COUNT` is the immutable Fabric client-pool size.
- `YRUSH_EXPECTED_CLIENT_NAMES` is the comma-separated list of those required
  Minecraft usernames and must contain exactly that many unique names.
- `YRUSH_MAX_PLAYERS` must be at least that size.
- `YRUSH_SERVER_XMS` and `YRUSH_SERVER_XMX` set the Java heap.
- `YRUSH_WORLD_SEED` sets normal-world generation.
- `YRUSH_STARTUP_TIMEOUT_SECONDS` bounds Paper startup and client arrival.
- `YRUSH_SERVER_RUNTIME` relocates mutable state.

After Paper is ready, the entrypoint waits for every named Fabric client,
issues `yrush start training` once, verifies YRush accepted at least that many
participants, and then publishes `/data/ready`. A required client departure
removes readiness, stops the server, and causes all trainer connections to
fail. Other players may freely join and leave like a normal server. YRush adds
eligible extra players to the next complete round; a player arriving during an
active round waits in the lobby until that boundary. Termination sends
`yrush stop` before Paper's `stop` command.

If the container restarts inside the same pod, the entrypoint clears stale
readiness and failure files, preserves the world on the disk-backed
`emptyDir`, restarts Paper, waits for the complete required client pool, and
starts YRush training again. Its per-pod restart generation is recorded in
the readiness metadata and metrics. Client disconnects still abort any trainer
command that was active during the interruption, so recovery cannot turn an
infrastructure failure into a learning transition.

The server emits `YRUSH_METRIC` records for ephemeral disk use, world size, and
round-preparation latency and requests Paper TPS output every ten seconds. Size
the Kubernetes ephemeral-storage limit from those measurements.

The `#image` app retains the generic server GHCR repository. Use `#image --
load <tag>` for Podman, or set `YRUSH_LOCAL_IMAGE_TRANSPORT=docker-daemon` for
Docker. The root README is the canonical orchestration guide.
