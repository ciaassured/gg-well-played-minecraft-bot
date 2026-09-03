# Benchmark server

This project packages one Paper 26.2 process and the authoritative benchmark
plugin. It owns resets, episode time, observations, and terminal decisions; it
does not run a Python service or launch clients.

Each established player session receives the lowest free nonnegative lane
ordinal. Lanes are constructed lazily, translated by eight blocks on Z, and
repaired independently on reset. Ordinals are released when players leave.
Benchmark players are non-collidable and mutually hidden. There is no coded
lane limit.

```console
nix develop ./benchmark-server
nix build ./benchmark-server
nix build ./benchmark-server#oci
nix run ./benchmark-server#image
nix flake check ./benchmark-server
(cd benchmark-server && nix fmt)
nix run ./benchmark-server#server
```

Mutable state defaults to `benchmark-server/runtime`; set
`JUMP_BENCHMARK_SERVER_RUNTIME` to relocate it. Paper's `max-players` value is
the sum of `JUMP_CLIENT_COUNT` and the nonnegative
`JUMP_SERVER_PLAYER_HEADROOM`, which defaults to one spare slot.
`JUMP_SERVER_XMS` and `JUMP_SERVER_XMX` configure the JVM heap, with local
defaults of `512m` and `1g`. Server simulation and view distances are both two
chunks, which covers the fixed 24-block arena. The Paper and matching Mojang
server JARs are pinned in the Nix package, so server startup does not require
network egress.

The `#image` app builds `result-server-image`. Use `#image -- load <tag>` to
load the same archive into Podman, or set `JUMP_LOCAL_IMAGE_TRANSPORT` to
`docker-daemon` for Docker. The root README documents coordinated publication.
