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
nix run ./trainer#evaluate -- --checkpoint <checkpoint.zip> [--episodes 100] [--output <report.json>]
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
every 250 timesteps with recent episode metrics, and reports periodic
**validation** every ten episodes with successes, elapsed time, and an ETA.
Both records include
`client_ticks/action` and `server_ticks/action`; values near `1.00` confirm that
the policy is receiving one decision opportunity per game tick. Checkpoint and
promotion decisions use the same format. Validation ranks candidates using the
existing success, completion-time, and jump-request ordering and may promote a
candidate to `checkpoints/best.zip`. It always reuses the fixed
`100000..100099` suite without mutating the seed stream used by seedless
training resets. The public `evaluate` command only measures a frozen checkpoint
and never participates in promotion. Low CPU utilization is normal because each
action is synchronized to a real 20 TPS Minecraft client; the small DQN update
is not the throughput bottleneck.
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

`evaluate` defaults to 100 consecutive episodes on seeds `200000..200099` and
accepts any positive `--episodes` count. Its concise terminal summary identifies
the checkpoint and report, successes and success rate, terminal-reason counts,
mean return, successful-episode completion ticks and jump requests, and client
and server tick cadence. Metrics that require a successful episode are shown as
`n/a` when there are no successes.

The detailed JSON contains the resolved checkpoint path, inclusive seed range,
the same aggregates (`success_rate` is a `0..1` fraction), and all per-episode
metrics. A run-owned checkpoint writes by default to
`<run>/metrics/performance-<checkpoint>-<N>-episodes.json`; an external
checkpoint writes to
`$JUMP_TRAINER_OUTPUT_ROOT/performance-<checkpoint>-<N>-episodes.json`
(`trainer/evaluations/` under the Nix app). `--output` overrides the path, and
repeating the same deterministic evaluation replaces the report. Recordings
remain timestamped beneath
`$JUMP_TRAINER_RECORDING_ROOT/evaluate/<UTC timestamp>/`. Completing the command
always exits `0` regardless of performance; invalid arguments or infrastructure
exit `2`, and Ctrl-C exits `130`.

When migrating to YRush, replace these episode metrics and the current
validation promotion ordering with authoritative YRush outcomes and suitable
performance criteria. Retain the checkpoint-only, report-only public evaluator;
do not introduce scripted YRush baselines or a binary gate without an external
definition of “good.”
