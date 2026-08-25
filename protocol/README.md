# Protocol

This project owns the versioned Protobuf contract shared by the Paper plugin,
Fabric client, and Python trainer. Generated Java and Python code is deliberately
not committed: each consumer generates bindings from this directory during its
own build.

`jump/v1/jump.proto` defines connection setup, idempotent resets, authoritative
episode state/results, sequenced observations and actions, errors, shutdown, and
Replay Mod command-finalization and artifact-retention coordination. TCP
messages use a four-byte unsigned
big-endian length followed by one `WireMessage`; peers reject frames larger than
1 MiB. Minecraft custom payloads contain the Protobuf bytes directly because
Minecraft supplies packet framing.

Protocol v2 has one client lifecycle: connection messages contain no mode and
the old capture messages are reserved. At trainer command end,
`CommandFinalize` starts post-processing, `EpisodeArtifact` offers recordings
sequentially, `RetentionAcknowledgement` confirms whether each staging source
may be deleted, and `BatchComplete` reports warnings and reconnection. Each
artifact identifies its episode, seed, ordinal, completeness, terminal reason,
path, size, and SHA-256 digest.

Commands:

```console
nix develop ./protocol
nix build ./protocol
nix flake check ./protocol
nix fmt ./protocol
nix run ./protocol#validate
```

Consumers declare `path:../protocol` with `flake = false`; this project is never
a runtime service.
