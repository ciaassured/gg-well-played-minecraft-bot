# Minecraft YRush PPO

This repository trains one feed-forward Stable-Baselines3 PPO policy while a
fixed pool of Fabric/HeadlessMC clients competes in the same YRush rounds on
exactly one Paper server. The server owns the ordinary generated world and
round lifecycle; the trainer never starts or resets Minecraft.

The components communicate only through explicit boundaries:

- `protocol/` defines the intentionally incompatible `yrush.v1` trainer/client
  Protobuf contract.
- `yrush-server/` packages Paper 26.2 and pinned YRush v1.3.1.
- `client-mod/` bridges YRush's JSON channel to one trainer endpoint per
  persistent client.
- `trainer/` owns Gymnasium semantics, PPO collection, evaluation, and durable
  artifacts.
- `replay-renderer/` remains available for retained historical recordings.

## Local startup

Start one process per terminal, in this order:

```console
YRUSH_EXPECTED_CLIENT_COUNT=1 \
  YRUSH_EXPECTED_CLIENT_NAMES=yrushbot-0 \
  YRUSH_MAX_PLAYERS=8 \
  nix run ./yrush-server#server
nix run ./client-mod#headless
nix run ./trainer#smoke -- --rounds 2
```

The defaults use Paper at `127.0.0.1:25565` and the trainer listener at
`127.0.0.1:64123`. The listener stays loopback-only unless
`YRUSH_TRAINER_BIND` is changed. For multiple local clients, give each process
a distinct `YRUSH_CLIENT_RUNTIME`, `YRUSH_TRAINER_PORT`, and
`YRUSH_CLIENT_USERNAME`, then pass every endpoint to the trainer.

The server waits for the comma-separated `YRUSH_EXPECTED_CLIENT_NAMES` before
it issues `yrush start training`. Any later departure by one of those required
Fabric clients is a fixed-pool failure and aborts an active trainer command.
Other players can join and leave normally and are enrolled by YRush at the
next complete round boundary, up to `YRUSH_MAX_PLAYERS`.

## Farm deployment

Kubernetes deployment and operating instructions live only in the sibling
`../kube` repository, under `flux/apps/farm/yrush-training*`. This repository
publishes the coordinated server, client, and trainer images; it does not own
or duplicate cluster manifests.

## Training and inference

The local bounded stages use the same commands as the farm:

```console
nix run ./trainer#canary -- --run-id <unique-id>
nix run ./trainer#tuning-canary -- --run-id <unique-id>
nix run ./trainer#proof -- --run-id <unique-id>
```

Every trainer command accepts repeated `--endpoint HOST:PORT` arguments or an
`--endpoint-template ... --clients N` pair. A rollout contains 256 valid
transitions per client from one frozen policy version. Optimization overlaps
continued play, whose transitions are discarded, and a completed policy can
switch only for the whole pool at a global round boundary.

Deterministic evaluation ranks candidates by global completion rate, then mean
completion time, then best remaining target distance in draws:

```console
nix run ./trainer#evaluate -- \
  --checkpoint <checkpoint.zip> --rounds 8
nix run ./trainer#run -- --run <run-directory> --rounds 1
```

Run artifacts are written under `/artifacts/runs/<run-id>/` in Kubernetes and
`trainer/runs/` locally:

```text
<run-id>/
|-- config.json
|-- versions.json
|-- checkpoints/
|   |-- untrained.zip
|   |-- latest.zip
|   |-- best.zip
|   |-- candidates/
|   `-- promoted/
`-- metrics/
    |-- rounds.jsonl
    |-- ppo-updates.jsonl
    |-- evaluation-policy-*.json
    `-- summary.json
```

Archives embed the protocol and space definitions, normalization metadata,
deployment revision, fixed client count, server identity, and world seed.

## Reproducible builds

Every subproject remains independently buildable and checkable:

```console
nix build ./protocol
nix flake check ./protocol
nix build ./yrush-server
nix flake check ./yrush-server
nix build ./client-mod
nix flake check ./client-mod
nix build ./trainer
nix flake check ./trainer
nix build ./replay-renderer
nix flake check ./replay-renderer
```

Formatting is project-local:

```console
(cd protocol && nix fmt)
(cd yrush-server && nix fmt)
(cd client-mod && nix fmt)
(cd trainer && nix fmt)
(cd replay-renderer && nix fmt)
```

The deployable components expose independent OCI archives and image helpers:

```console
nix run ./yrush-server#image
nix run ./client-mod#image
nix run ./trainer#image
```

These create `result-server-image`, `result-client-image`, and
`result-trainer-image`. Use `#image -- load <tag>` for Podman, or set
`YRUSH_LOCAL_IMAGE_TRANSPORT=docker-daemon` for Docker. CI builds all three
archives before publishing the same immutable commit tag. Upgrade the
coordinated `yrush.v1` server, client, and trainer images together.
