# Minecraft one-block jump DQN

This repository is a reproducible benchmark in which a Stable-Baselines3 DQN
learns when to jump over a one-block wall in Minecraft. Paper owns the arena and
episode truth, a Fabric mod turns one action per tick into player input, and the
Python trainer owns Gymnasium semantics, rewards, seeds, models, and evaluation.
Replay Mod records deterministic checkpoint demonstrations, which the renderer
converts to playable H.264 MP4 files.

The target stack is Minecraft 26.2, Paper 26.2 build 112, Java 25, Fabric Loader
0.19.3, Fabric API 0.155.2+26.2, HeadlessMC 2.10.0, Replay Mod 2.6.27, and Fabric
Loom 1.17.19. Every directory below is an independent flake-parts project with
its own `flake.lock` and separate package, app, check, development-shell, and
formatter modules. There is intentionally no root flake and no process
orchestrator.

## Project boundaries

- `protocol/` is the canonical versioned Protobuf contract. It builds schemas
  and a descriptor set but runs no service. Consumers generate their own Java
  or Python bindings from this non-flake source input.
- `benchmark-server/` packages Paper and the benchmark plugin. It constructs
  the fixed arena, resets the player, advances authoritative episode time, and
  decides success, missed-jump failure, and timeout.
- `client-mod/` packages the Fabric bridge and isolated HeadlessMC clients. It
  relays reset/state messages, observes the local player, applies sequenced
  `NOOP` or one-tick `JUMP` input, and safely releases all controls. Recording
  mode additionally owns Replay Mod capture finalization.
- `trainer/` owns the Gymnasium environment, deterministic normalization and
  reward, seed partitions, baselines, SB3 DQN training, validation promotion,
  final evaluation, saved-model inference, and capture coordination. It never
  starts Paper or Minecraft.
- `replay-renderer/` validates retained `.mcpr` archives and drives Replay Mod's
  camera-path renderer under Xvfb and Mesa software OpenGL, then verifies the
  resulting MP4 with `ffprobe`. It neither captures episodes nor trains models.

Fabric Loom is a Gradle build plugin used by `client-mod/` and
`replay-renderer/`. Minecraft jars use runtime names that are unsuitable for
ordinary Java development; Loom resolves the pinned Minecraft/Fabric graph,
applies mappings to provide a readable compile classpath, integrates Fabric and
mixins, and remaps the finished mod jars back to runtime names. Loom is not a
runtime component of the benchmark, protocol, or learning loop.

## Start, train, and run

Run these in three terminals from the repository root. Wait for Paper to report
that it is done starting before launching the client, and wait for the client to
join before starting the trainer.

Terminal 1:

```console
nix run ./benchmark-server#server
```

Terminal 2:

```console
nix run ./client-mod#headless -- --mode training
```

Terminal 3:

```console
nix run ./trainer#smoke
nix run ./trainer#train
```

`train` starts a new DQN from random initialization, draws training episode
seeds from `0..99999`, and evaluates deterministic candidates on seeds
`100000..100099`. By default it runs 30,000 Minecraft ticks and validates every
5,000 ticks. Its console uses consistent one-line records: learning is reported
every 250 timesteps, evaluation every ten episodes, and checkpoint/promotion
decisions as they occur. Low CPU utilization is expected because actions remain
synchronized to Minecraft's real 20 TPS clock. It prints the new run directory
immediately and again when complete. To load that run's
promoted `checkpoints/best.zip` later and execute the showcase episode without
learning:

```console
nix run ./trainer#run -- --run <run-directory>
```

A specific saved model and explicit seed range can be run with:

```console
nix run ./trainer#run -- --checkpoint <checkpoint.zip> --episodes <count> --seed <seed>
```

## Reset and step flow

On reset, Python chooses the seed and sends a request to Fabric over a
size-limited, length-prefixed Protobuf connection on `127.0.0.1:64123`. Fabric
immediately releases controlled input and forwards the same Protobuf payload to
Paper through the player-associated `jump:control` custom-payload channel.
Paper repairs the arena, restores and teleports the player, waits for two stable
grounded server ticks, and returns authoritative episode geometry and timing.
Fabric waits for the client to apply that teleport, then emits observation zero;
only then does Gymnasium `reset()` return.

For each step, Python sends exactly one sequenced `NOOP` or `JUMP` for the
current observation. On the following client tick Fabric holds forward, applies
jump for that tick only when requested, and then releases jump. Normal Minecraft
movement reaches Paper, which advances elapsed time and evaluates the episode.
Fabric combines Paper's state with the client's position and velocity and
returns the next observation. Python computes progress, per-tick cost, jump
penalty, and terminal reward before returning the Gymnasium transition.
Terminal observations remain valid replay-buffer transitions; transport,
timeout, and process failures instead abort as infrastructure errors and release
all inputs.

## Evaluation, capture, and rendering

The commands deliberately have separate effects:

- `train` updates a new DQN and periodically promotes deterministic validation
  checkpoints.
- `evaluate` performs no learning or replay-buffer mutation. It evaluates a
  scripted baseline or checkpoint over all 100 validation or reserved final
  test seeds and writes a JSON report.
- `run` loads a checkpoint and controls requested episodes without learning.
- `capture` re-runs the untrained and every promoted checkpoint on showcase seed
  `100000`, coordinating one finalized Replay Mod recording per checkpoint. It
  does not reproduce historical training episodes and does not render video.
- `render` validates finalized recordings and converts each one to a playable
  MP4 without starting any live benchmark service.

Baseline and checkpoint evaluation examples are:

```console
nix run ./trainer#evaluate -- --policy noop --suite validation
nix run ./trainer#evaluate -- --policy always-jump --suite test
nix run ./trainer#evaluate -- --checkpoint <checkpoint.zip> --suite test
```

The checkpoint test command also evaluates both baselines on final seeds
`200000..200099` and reports every acceptance condition: at least 95 successes,
higher mean return than both baselines, and at most two requested jumps on
successful episodes.

To capture a completed run, stop the training-mode client first. Leave Paper
running, start a fresh recording-mode client in terminal 2, and then invoke
capture in terminal 3:

```console
nix run ./client-mod#headless -- --mode recording
nix run ./trainer#capture -- <run-directory>
```

After capture has completed and the recording client has finalized its files,
render them independently:

```console
nix run ./replay-renderer#render -- <run-directory>
```

## Artifacts

The default training root is `trainer/runs/`. A run directory contains:

```text
run-<UTC timestamp>/
|-- config.json
|-- versions.json
|-- promotion-history.json
|-- checkpoints/
|   |-- untrained.zip
|   |-- latest.zip
|   |-- best.zip
|   |-- candidates/
|   `-- promoted/
|-- metrics/
|-- replays/
|   `-- capture-<UTC timestamp>/
`-- videos/
```

Validation reports and training summaries are under `metrics/`; final
checkpoint evaluation writes there when the checkpoint belongs to a run.
Capture manifests, per-recording metadata, and `.mcpr` files are under
`replays/`. The renderer mirrors their relative layout below `videos/` and
writes `render-manifest.json` alongside the MP4 outputs.

Mutable Paper, client, and renderer caches default to each project's `runtime/`
directory. They can be relocated with `JUMP_BENCHMARK_SERVER_RUNTIME`,
`JUMP_CLIENT_RUNTIME`, and `JUMP_RENDERER_RUNTIME`. Training output can be
relocated with `JUMP_TRAINER_RUN_ROOT`.

## Build and final verification

Each project documents its own development commands in its local README. From a
clean clone, verify every independent flake:

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

The complete acceptance workflow is:

1. Start Paper, the training client, and run `smoke` in the terminal order above.
2. Evaluate `noop` and `always-jump` on the final suite and retain their reports.
3. Run `train` from scratch and note the emitted run directory.
4. Load that run with `run`, proving `best.zip` works without further learning.
5. Evaluate `best.zip` once on the final suite and require the reported
   acceptance result to pass.
6. Stop the training client, start the recording client, and run `capture` for
   the same run.
7. Stop live services, run the renderer on the run directory, and inspect its
   manifest.
8. Confirm each retained `.mcpr` is valid and every successful render has an
   H.264 video stream with positive duration (the renderer performs this
   `ffprobe` check automatically).

Formatting remains project-local as well:

```console
(cd protocol && nix fmt)
(cd benchmark-server && nix fmt)
(cd client-mod && nix fmt)
(cd trainer && nix fmt)
(cd replay-renderer && nix fmt)
```
