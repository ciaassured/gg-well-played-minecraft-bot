# Minecraft ML Bot

This repository is a reproducible benchmark in which a Stable-Baselines3 DQN
learns when to jump over a one-block wall in Minecraft. Paper owns the arena and
episode truth, a Fabric mod turns one action per tick into player input, and the
Python trainer owns Gymnasium semantics, rewards, seeds, models, and evaluation.
Replay Mod records every episode, and the renderer converts retained recordings
to playable video files.

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
nix run ./client-mod#headless
```

Terminal 3:

```console
nix run ./trainer#smoke
nix run ./trainer#train -- \
  --timesteps 30000 \
  --seed 20260823 \
  --validation-interval 5000 \
  --validation-episodes 20
```

`--timesteps` is required; `30000` is the recommended reference training
budget, not an implicit default. `--seed` controls the DQN and training seed
stream and defaults to `20260823`. `--validation-interval` controls how often a
candidate checkpoint is measured and defaults to 5000 training steps.
`--validation-episodes` controls the number of periodic validation episodes,
defaults to 20, and selects that many seeds from the start of the fixed
100-seed validation partition. Run
`nix run ./trainer#train -- --help` for all training options.

To load that run's promoted `checkpoints/best.zip` later and execute the showcase episode without learning:

```console
nix run ./trainer#run -- --run <run-directory>
```

## Checkpoint performance and rendering

Measure a frozen checkpoint deterministically without learning:

```console
nix run ./trainer#evaluate -- --checkpoint <checkpoint.zip> [--episodes 100] [--output <report.json>]
```

The default 100 episodes use consecutive seeds `200000..200099`; changing
`--episodes` extends or shortens that sequence from `200000`. The JSON report
records the resolved checkpoint, inclusive seed range, success count and
`0..1` success rate, observed terminal-reason counts, return and successful-
episode metrics, tick cadence, and every episode. The terminal summary renders
successful-episode completion ticks and jump requests as `n/a` when there are
no successes.

When the checkpoint is inside a training run, the default report is
`<run>/metrics/performance-<checkpoint>-<N>-episodes.json`. Otherwise it is
`$JUMP_TRAINER_OUTPUT_ROOT/performance-<checkpoint>-<N>-episodes.json`
(`trainer/evaluations/` by default through the Nix app). `--output` overrides
either location. Repeating the same checkpoint and episode count intentionally
replaces that deterministic report. Episode recordings remain historical,
timestamped artifacts under
`trainer/recordings/evaluate/<UTC timestamp>/` (or
`$JUMP_TRAINER_RECORDING_ROOT/evaluate/`).

This command only reports performance: a completed evaluation exits `0`
regardless of the checkpoint's results. Invalid arguments or infrastructure
failures exit `2`, and interruption exits `130`. During training, periodic
**validation** uses the configured prefix of the fixed validation partition to
rank each candidate against the best historical candidate. A strictly better
candidate becomes `checkpoints/best.zip`, so the final file is the best observed
checkpoint rather than necessarily the last one. The public `evaluate` command
uses its independent episode count and evaluation seeds; it neither validates
nor promotes a checkpoint.

For a future YRush migration, replace the current one-block episode metrics and
promotion ordering with authoritative YRush outcomes and performance criteria
appropriate to that benchmark. Keep the public evaluator checkpoint-only and
report-only. Do not add scripted YRush baselines or a binary acceptance gate
until an external definition of “good” supplies those criteria.

There is no retroactive capture command or checkpoint highlighting. Recordings
are historical artifacts of the command that ran the episodes. Once a command
has finalized, render either one retained archive or exactly the supplied
folder recursively:

```console
nix run ./replay-renderer#render -- <recording.mcpr>
nix run ./replay-renderer#render -- <recording-directory>
```

For a file input, output defaults to a sibling `videos/` directory. For a folder
input, output defaults to `<recording-directory>/videos/`; relative subpaths are
preserved. `--output-dir <directory>` overrides either default. The renderer
does not discover trainer runs or search outside the supplied directory.

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
|   `-- train-<UTC timestamp>/
|       |-- manifest.json
|       |-- 000-seed-<seed>.mcpr
|       `-- ...
`-- ...
```

Validation reports and training summaries are under `metrics/`; checkpoint
performance reports are also written there when the checkpoint belongs to a
run.
Training recordings and their command manifest are under the run's `replays/`
directory. Other commands use
`trainer/recordings/{smoke,evaluate,run}/<UTC timestamp>/`; relocate that root
with `JUMP_TRAINER_RECORDING_ROOT`. Clips are named
`<three-digit ordinal>-seed-<seed>.mcpr`. Each manifest records command context,
episode ID and seed, outcome, complete/partial status, policy or checkpoint
context, digest, size, and canonical path.

Recording retention failures are warnings and do not change an otherwise
successful trainer result. A failed or unacknowledged transfer removes any
incomplete trainer destination but deliberately leaves the source under
`client-mod/runtime/client/game/replay_recordings` for manual recovery. Files
whose retention was acknowledged are removed from client staging, leaving one
canonical trainer-owned copy. Interrupted commands keep their interrupted exit
status while attempting the same finalization; an active episode is recorded
as partial. Unexpected trainer disconnection also preserves all untransferred
staging files and the client reconnects automatically.
