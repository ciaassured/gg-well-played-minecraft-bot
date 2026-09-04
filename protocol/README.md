# YRush trainer protocol

`proto/yrush/v1/yrush.proto` is the canonical trainer-to-Fabric contract. It
uses the intentionally incompatible `yrush.v1` namespace and protocol version
1. Paper-to-Fabric state is a separate JSON schema supplied by YRush on
`yrush:bot_state`.

`WireMessage` is the only top-level Protobuf message. TCP uses a four-byte
unsigned big-endian length prefix and rejects messages larger than 1 MiB. The
contract covers identity/readiness, arming a future round, episode readiness,
six-head actions and acknowledgements, observations, results, errors, and
shutdown. It contains no world-reset operation or server topology identifier.

```console
nix build ./protocol
nix flake check ./protocol
(cd protocol && nix fmt)
```

The build produces schemas and descriptor sets but runs no service. Consumers
generate their own bindings from this non-flake source input.
