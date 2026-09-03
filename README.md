# Minecraft ML Bot

This repository trains one Stable-Baselines3 DQN from concurrent Minecraft
actors. One Paper process owns authoritative episode state and gives each
player a lazily built, isolated arena lane. Persistent Fabric/HeadlessMC clients
bridge one trainer connection each, and the Python coordinator batches policy
inference while a spawned learner overlaps replay-buffer updates with
collection.

New runs contain checkpoints and metrics only. Replay Mod is no longer loaded
and protocol v3 has no recording lifecycle. The unchanged `replay-renderer/`
and any existing `.mcpr` files remain available for historical recordings.

## Local startup

Run these in separate terminals from the repository root:

```console
nix run ./benchmark-server#server
nix run ./client-mod#headless
nix run ./trainer#smoke
```

The local fallback uses Paper at `127.0.0.1:25565` and one trainer endpoint at
`127.0.0.1:64123`. The client listener remains loopback-only unless
`JUMP_TRAINER_BIND` is explicitly changed.

Train one or more clients with repeatable endpoints:

```console
nix run ./trainer#train -- \
  --endpoint 127.0.0.1:64123 \
  --timesteps 30000 \
  --validation-interval 5000 \
  --validation-episodes 20
```

Kubernetes uses a StatefulSet endpoint template and a unique run ID:

```console
jump-trainer pipeline \
  --endpoint-template 'jump-client-{index}.jump-clients:64123' \
  --clients 2 \
  --pool-startup-timeout 900 \
  --run-root /artifacts/runs \
  --run-id <unique-id> \
  --timesteps 30000 \
  --validation-interval 5000 \
  --validation-episodes 20 \
  --evaluation-episodes 100
```

`--timesteps` is an aggregate transition budget across the complete pool. An
action boundary can overshoot by at most the number of in-flight clients; both
requested and actual counts are reported. Every configured client is required
for the entire command.

Other pool-aware commands are:

```console
nix run ./trainer#capacity -- --transitions 2000
nix run ./trainer#evaluate -- --checkpoint <checkpoint.zip> --episodes 100
nix run ./trainer#run -- --run <run-directory>
```

`capacity` uses the same actor/inference coordinator with no learner. `smoke`
runs the showcase episode concurrently on every endpoint. Validation and
evaluation assign each fixed seed exactly once and sort reports by seed.

## Run artifacts

Named cluster runs are written to `/artifacts/runs/<run-id>/` and refuse to
overwrite an existing ID. Local unnamed runs default to timestamped directories
beneath `trainer/runs/`:

```text
<run-id>/
|-- config.json
|-- versions.json
|-- promotion-history.json
|-- checkpoints/
|   |-- untrained.zip
|   |-- latest.zip
|   |-- best.zip
|   |-- candidates/
|   `-- promoted/
`-- metrics/
```

Reports include deployment revisions, endpoints and ordinals, requested and
actual transitions, update counts, learner backlog/policy lag, per-client
results, action-latency percentiles, tick cadence, throughput, and failures.
SIGTERM stops collection, requests `latest.zip`, atomically records the
interruption, and exits nonzero.

## Reproducible builds

Every subproject remains independently buildable and checkable:

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

Formatting is also project-local:

```console
(cd protocol && nix fmt)
(cd benchmark-server && nix fmt)
(cd client-mod && nix fmt)
(cd trainer && nix fmt)
(cd replay-renderer && nix fmt)
```

The server, client, and trainer flakes expose independent `#oci` archives.
After every successful flake-check workflow, the separate image workflow
publishes all three amd64 GHCR images with the same full Git commit tag. Each
GHCR package must be made public once in its package settings; that visibility
then applies to later versions. GitOps manifests pin immutable digests and
upgrade the intentionally incompatible protocol-v3 images together.
