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
nix flake check ./benchmark-server
(cd benchmark-server && nix fmt)
nix run ./benchmark-server#server
```

Mutable state defaults to `benchmark-server/runtime`; set
`JUMP_BENCHMARK_SERVER_RUNTIME` to relocate it. `JUMP_CLIENT_COUNT` generates
Paper's `max-players` value at startup. `JUMP_SERVER_XMS` and
`JUMP_SERVER_XMX` configure the JVM heap, with local defaults of `512m` and
`1g`. Server simulation and view distances are both two chunks, which covers
the fixed 24-block arena.
