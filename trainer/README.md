# DQN trainer

This project owns seed selection, the loopback Protobuf client, Gymnasium reset
and step semantics, reward and normalization, SB3 DQN training, checkpoint
promotion, evaluation, and inference. It never starts Paper or Minecraft and it
does not record or render Replay Mod files.

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
```

Runs are written beneath `trainer/runs` by default. Each run contains its
configuration, metrics, untrained/latest/best checkpoints, validation promotion
history, and reserved directories for the later replay and video stages.
