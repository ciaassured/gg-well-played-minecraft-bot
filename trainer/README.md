# DQN trainer

This project owns seed selection, the loopback Protobuf client, Gymnasium reset
and step semantics, reward and normalization, SB3 DQN training, checkpoint
promotion, evaluation, and inference. It never starts Paper or Minecraft and it
does not start Replay Mod or render video. Its capture command coordinates a
separately started recording client, verifies each finalized Replay Mod file,
and retains it with the training run.

Start `benchmark-server` and the training-mode `client-mod` first, then use:

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
nix run ./trainer#capture -- <run-directory>
```

`smoke` must complete showcase seed `100000` with one deterministic near-wall
JUMP request; a timeout, missed jump, or extra request makes the command fail.

Runs are written beneath `trainer/runs` by default. Each run contains its
configuration, metrics, untrained/latest/best checkpoints, validation promotion
history, captured `.mcpr` files and metadata, and a reserved video directory.
Training uses consistent one-line `[train]` and `[evaluate]` records instead of
SB3's box tables. It prints its run directory immediately, reports learning
every 250 timesteps with recent episode metrics, and reports evaluation every
ten episodes with successes, elapsed time, and an ETA. Both records include
`client_ticks/action` and `server_ticks/action`; values near `1.00` confirm that
the policy is receiving one decision opportunity per game tick. Checkpoint and
promotion decisions use the same format. Low CPU utilization is normal because
each action is synchronized to a real 20 TPS Minecraft client; the small DQN
update is not the throughput bottleneck.
Stop the training client and start `client-mod` in recording mode before using
`capture`; every retained checkpoint is rerun on showcase seed `100000` without
learning, then Replay Mod is finalized before the next checkpoint reconnects.

A checkpoint evaluation on the `test` suite also evaluates both scripted
baselines and persists the four final acceptance predicates. If any predicate
fails, the command prints the report and then exits with status `3`, allowing
automation to distinguish a rejected checkpoint from a passing evaluation.
