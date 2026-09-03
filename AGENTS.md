# Agent guidance

- Preserve top-level project boundaries: each subproject must remain independently buildable, configurable, and testable. Do not make one subproject inspect, import, configure, or derive behavior from another; coordinate only through explicit contracts or externally supplied configuration.
- Use the root `README.md` as the canonical guide for orchestrating the project, including its Nix commands; do not duplicate those instructions here.
- Follow [Scoped Commits](https://scopedcommits.com/): format normal commit messages as `<scope>: <description>`, using the affected subproject or repository area as the scope (for example, `trainer: add evaluation metric`).

## Project boundaries

- `protocol/` is the canonical versioned Protobuf contract. It builds schemas
  and a descriptor set but runs no service. Consumers generate their own Java
  or Python bindings from this non-flake source input.
- `benchmark-server/` packages Paper and the benchmark plugin. It constructs
  the fixed arena, resets the player, advances authoritative episode time, and
  decides success, missed-jump failure, and timeout.
- `client-mod/` packages the Fabric bridge and isolated HeadlessMC clients. It
  relays reset/state messages, observes the local player, applies input, and
  safely releases all controls. Its persistent client additionally owns Replay
  Mod episode splitting and staging finalization.
- `trainer/` owns the Gymnasium environment, deterministic normalization and
  reward, seed partitions, baselines, SB3 DQN training, validation promotion,
  final evaluation, saved-model inference, and recording retention. It never
  starts Paper or Minecraft.
- `replay-renderer/` validates retained `.mcpr` archives and drives Replay Mod's
  camera-path renderer under Xvfb and Mesa software OpenGL, then verifies the
  resulting MP4 with `ffprobe`. It neither captures episodes nor trains models.

## Development

```console
nix build ./protocol
nix flake check ./protocol
nix build ./benchmark-server
nix flake check ./benchmark-server
nix build ./client-mod
nix flake check ./client-mod
nix build ./trainer
nix flake check ./trainer
nix build ./replay-renderer
nix flake check ./replay-renderer
```

Formatting remains project-local as well:

```console
(cd protocol && nix fmt)
(cd benchmark-server && nix fmt)
(cd client-mod && nix fmt)
(cd trainer && nix fmt)
(cd replay-renderer && nix fmt)
```