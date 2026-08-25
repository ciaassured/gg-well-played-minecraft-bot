# DQN trainer

This project owns seed selection, the loopback Protobuf client, Gymnasium reset
and step semantics, reward and normalization, SB3 DQN training, checkpoint
promotion, evaluation, and inference. It never starts Paper or Minecraft and it
does not start Replay Mod or render video. Every command coordinates finalization
with the persistent client, validates each Replay Mod archive and its reported
size and digest, and atomically retains it before acknowledgement.

Start `benchmark-server` and the unified `client-mod` first, then use:

```console
nix develop ./trainer
nix build ./trainer
nix flake check ./trainer
(cd trainer && nix fmt)
nix run ./trainer#smoke
nix run ./trainer#evaluate -- --policy noop --suite validation
nix run ./trainer#evaluate -- --checkpoint <checkpoint.zip> --suite test
nix run ./trainer#train
nix run ./trainer#run -- --run <run-directory>
```

`smoke` must complete showcase seed `100000` with one deterministic near-wall
JUMP request; a timeout, missed jump, or extra request makes the command fail.

Runs are written beneath `trainer/runs` by default. Each run contains its
configuration, metrics, untrained/latest/best checkpoints, validation promotion
history, and historical episode recordings beneath
`replays/train-<UTC timestamp>/`. The `smoke`, `evaluate`, and `run` commands
retain recordings beneath
`trainer/recordings/<command>/<UTC timestamp>/`; set
`JUMP_TRAINER_RECORDING_ROOT` to relocate that root. Each command directory has
one manifest and sequential `<ordinal>-seed-<seed>.mcpr` files.
Training uses consistent one-line `[train]` and `[evaluate]` records instead of
SB3's box tables. It prints its run directory immediately, reports learning
every 250 timesteps with recent episode metrics, and reports evaluation every
ten episodes with successes, elapsed time, and an ETA. Both records include
`client_ticks/action` and `server_ticks/action`; values near `1.00` confirm that
the policy is receiving one decision opportunity per game tick. Checkpoint and
promotion decisions use the same format. Low CPU utilization is normal because
each action is synchronized to a real 20 TPS Minecraft client; the small DQN
update is not the throughput bottleneck.
Pressing Ctrl-C during learning saves the current in-memory model as
`checkpoints/latest.zip`, writes `metrics/training-interrupted.json`, and exits
without a traceback. A later `train` command always starts a new run; use
`run` or `evaluate` to load a checkpoint from an interrupted run.

The trainer's canonical copy is published through a temporary destination only
after ZIP, size, and SHA-256 validation. Failed transfers remove temporary
destinations and leave client staging intact for recovery. Recording failures
are warnings rather than ML failures. Ctrl-C preserves the trainer's exit
status, marks an in-progress episode partial, and still requests command
finalization. `JUMP_TRAINER_RECORDING_TIMEOUT` or `--recording-timeout` changes
the default five-minute wait.

A checkpoint evaluation on the `test` suite also evaluates both scripted
baselines and persists the four final acceptance predicates. If any predicate
fails, the command prints the report and then exits with status `3`, allowing
automation to distinguish a rejected checkpoint from a passing evaluation.
