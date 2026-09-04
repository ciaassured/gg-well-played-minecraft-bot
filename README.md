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
YRUSH_EXPECTED_CLIENT_COUNT=1 nix run ./yrush-server#server
nix run ./client-mod#headless
nix run ./trainer#smoke -- --rounds 2
```

The defaults use Paper at `127.0.0.1:25565` and the trainer listener at
`127.0.0.1:64123`. The listener stays loopback-only unless
`YRUSH_TRAINER_BIND` is changed. For multiple local clients, give each process
a distinct `YRUSH_CLIENT_RUNTIME`, `YRUSH_TRAINER_PORT`, and
`YRUSH_CLIENT_USERNAME`, then pass every endpoint to the trainer.

The server waits for exactly `YRUSH_EXPECTED_CLIENT_COUNT` players before it
issues `yrush start training`. Any later departure is a fixed-pool failure and
aborts an active trainer command.

## Kubernetes farm

[`deploy/kubernetes/yrush-farm.yaml`](deploy/kubernetes/yrush-farm.yaml)
contains the restricted `yrush-training` namespace, one server Deployment and
stable Service, a parallel client StatefulSet, explicit Cilium policy, a
separate artifacts claim, and suspended bounded trainer Jobs.
Before applying it:

1. Replace every `REPLACE_WITH_COMMIT` image tag with the same published full
   commit revision.
   Replace each `REPLACE_WITH_RUN_ID` with a unique suffix as well.
2. Label an appropriate node `yrush.gg/local-ssd=true`. That label is the
   operator's assertion that Kubernetes ephemeral storage is backed by local
   SSD.
3. Keep the server's expected count, maximum players, client replicas, endpoint
   count, and trainer expected count consistent if changing the default pool
   of four.
4. Set the chosen world seed and resource sizes.

Apply the farm and wait for the clients and server:

```console
kubectl apply -f deploy/kubernetes/yrush-farm.yaml
kubectl -n yrush-training rollout status deployment/yrush-paper --timeout=15m
kubectl -n yrush-training rollout status statefulset/yrush-client --timeout=15m
```

The Paper Service uses `publishNotReadyAddresses: true` so clients can join
while the entrypoint is still waiting for the complete pool. Server readiness
is not published until Paper is ready, all expected clients are present, and
YRush training mode has launched with that participant count.

Paper is available to the local `192.168.98.0/24` LAN at
`10.128.16.2:25565`, matching the previous farm access path. The `aurah__`
offline account is seeded as a level-four operator on each fresh server pod.
Clients may download Minecraft over ports 80 and 443 during first startup;
their 2 GiB `nfs-nasdaq` runtime claims retain that cache across pod updates.

The server mounts a disk-backed `emptyDir` at `/data`, requests `20Gi` of
ephemeral storage, and has a `50Gi` limit. World data, region files, Paper
caches, plugin state, and logs all live there; there is deliberately no server
volume claim. `/artifacts` is a separate persistent claim used only by trainer
Jobs. Keep the server pod alive across the bounded stages so its generated
chunks remain warm.

Before each trainer Job, capture the live server identity and verify it has not
changed:

```console
kubectl -n yrush-training get pod -l app.kubernetes.io/name=yrush-paper \
  -o jsonpath='{.items[0].metadata.uid}{" "}{.items[0].status.containerStatuses[0].restartCount}{"\n"}'
```

Put those two values into the `yrush-run-metadata` ConfigMap before each stage;
the suspended Jobs read it only when their pods are created. Unsuspend and wait
for each stage in order:

```console
kubectl -n yrush-training patch configmap yrush-run-metadata --type=merge \
  -p '{"data":{"YRUSH_SERVER_POD_UID":"<observed-uid>","YRUSH_SERVER_RESTART_COUNT":"<observed-count>"}}'
kubectl -n yrush-training patch job yrush-canary --type=merge -p '{"spec":{"suspend":false}}'
kubectl -n yrush-training wait --for=condition=complete job/yrush-canary --timeout=30m
kubectl -n yrush-training patch job yrush-tuning-canary --type=merge -p '{"spec":{"suspend":false}}'
kubectl -n yrush-training wait --for=condition=complete job/yrush-tuning-canary --timeout=90m
kubectl -n yrush-training patch job yrush-proof --type=merge -p '{"spec":{"suspend":false}}'
kubectl -n yrush-training wait --for=condition=complete job/yrush-proof --timeout=4h
```

The stages run one update/two evaluation rounds, four updates/four rounds, and
twelve updates/eight rounds respectively. A stage fails on pool loss, server
restart metadata, invalid action cadence, a missing update, entropy collapse,
or excessive KL. Re-read the server pod UID and restart count between stages.
Also inspect the server's `YRUSH_METRIC` log records for `/data` usage, world
growth, and round-preparation latency, plus Paper's TPS output. Revise the
ephemeral-storage request and limit from those measurements before a long run.

After all three bounded stages pass, create the final long command yourself
with a new run ID and the desired update/evaluation budget. For example, from a
trainer container with the same endpoints and `/artifacts` claim:

```console
yrush-trainer train \
  --endpoint-template 'yrush-client-{index}.yrush-clients:64123' \
  --clients 4 \
  --pool-startup-timeout 900 \
  --run-root /artifacts/runs \
  --run-id <unique-long-run-id> \
  --updates <chosen-update-count> \
  --evaluation-rounds <chosen-round-count>
```

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
