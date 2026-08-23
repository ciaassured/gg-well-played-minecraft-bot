# Benchmark server

This sibling project owns the Paper-side truth: fixed arena construction,
seeded reset state, authoritative elapsed ticks, and success/missed-jump/time
limit results. It has no Python socket and does not launch a Minecraft client.
All world and player changes are scheduled on Paper's main thread.

Pinned runtime: Minecraft/Paper `26.2` build `112`, Java `25`, Protobuf Java
`4.35.1`. Runtime state defaults to `benchmark-server/runtime/`; override it
with `JUMP_BENCHMARK_SERVER_RUNTIME`.

Commands:

```console
nix develop ./benchmark-server
nix build ./benchmark-server
nix flake check ./benchmark-server
nix fmt ./benchmark-server
nix run ./benchmark-server#server
```

The server uses offline mode only for the isolated local benchmark. It accepts
one player on `localhost:25565`, rebuilds the arena on every accepted reset, and
communicates solely through the `jump:control` Minecraft custom-payload channel.
The fixed lane is a smooth-stone sky platform at Y=300 in a structure-free
superflat world. Paper builds it and moves the world spawn onto it before the
first player joins. A three-block-high barrier after nine flat landing blocks
contains time-limited policies without changing the one-block jump itself.
Animals, NPCs, monsters, weather, and daylight changes are disabled.
