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
nix run ./trainer#train
```

The client is persistent and has no training/recording mode. It always loads
Replay Mod, waits for Replay Mod to finish starting, joins Paper, and then waits
for trainer commands on `127.0.0.1:64123`. Leave the same client running for
`smoke`, `train`, `evaluate`, and `run` commands.

Each trainer command records every Gym episode. At command end the client
disconnects from Paper once, asks Replay Mod to post-process the episode split
markers, and offers each resulting `.mcpr` to the trainer in episode order. The
trainer validates and atomically retains each file before acknowledging it; the
client then reconnects to Paper and waits for another command. Finalization is
allowed five minutes by default. Override it with
`JUMP_TRAINER_RECORDING_TIMEOUT` (seconds) and
`JUMP_CLIENT_FINALIZATION_TIMEOUT_MILLIS` (milliseconds).

To load that run's promoted `checkpoints/best.zip` later and execute the showcase episode without learning:

```console
nix run ./trainer#run -- --run <run-directory>
```

## Evaluation and rendering

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

Validation reports and training summaries are under `metrics/`; final
checkpoint evaluation writes there when the checkpoint belongs to a run.
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

Mutable Paper, client, and renderer caches default to each project's `runtime/`
directory. They can be relocated with `JUMP_BENCHMARK_SERVER_RUNTIME`,
`JUMP_CLIENT_RUNTIME`, and `JUMP_RENDERER_RUNTIME`. Training output can be
relocated with `JUMP_TRAINER_RUN_ROOT`.

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
