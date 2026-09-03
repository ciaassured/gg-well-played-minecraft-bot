# Benchmark wire protocol

`proto/jump/v1/jump.proto` is the canonical, versioned contract. The package
remains `jump.v1`, while the incompatible wire protocol is version 3. Paper,
Fabric, and Python reject other protocol-version values.

`WireMessage` is the only top-level message. TCP uses a four-byte unsigned
big-endian length prefix; Minecraft uses custom-payload framing. Both reject
messages larger than 1 MiB.

Protocol v3 contains connection, reset, state, action, observation, result,
error, and shutdown messages only. Removed capture fields 21–23 and recording
fields 24–27 are reserved by number and name so they cannot be reused.

```console
nix build ./protocol
nix flake check ./protocol
(cd protocol && nix fmt)
```

The build produces the schema and descriptor sets but runs no service.
Consumers generate their own language bindings from this non-flake source
input.
