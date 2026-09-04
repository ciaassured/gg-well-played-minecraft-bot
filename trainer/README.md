# Asynchronous PPO trainer

The trainer owns Gymnasium semantics, deterministic normalization and reward,
fixed-pool scheduling, Stable-Baselines3 PPO optimization, global policy
switches, deterministic evaluation, and persistent run artifacts. It never
starts Paper or Minecraft.

```console
nix develop ./trainer
nix build ./trainer
nix build ./trainer#oci
nix run ./trainer#image
nix flake check ./trainer
(cd trainer && nix fmt)
nix run ./trainer#smoke -- --rounds 2
nix run ./trainer#canary -- --run-id <id>
nix run ./trainer#tuning-canary -- --run-id <id>
nix run ./trainer#proof -- --run-id <id>
```

Every command accepts repeatable `--endpoint HOST:PORT` or an
`--endpoint-template ... --clients N` pair. Kubernetes supplies all fixed
StatefulSet endpoints. Losing any endpoint aborts the run and never creates a
learning transition. Additional normal players need no trainer endpoint: they
may participate in the same rounds, and their participant counts and wins are
reconciled from the controlled clients' YRush terminal packets. If every
controlled client is eliminated while an external player remains, the report
marks that global terminal as unobserved instead of misclassifying it as a
draw.

The scheduler batches every currently ready survivor. An eliminated client no
longer gates survivor actions; all clients are armed together at each global
round boundary. Each PPO rollout stores exactly 256 valid transitions per
client from one policy version, with observation, six-head action, reward,
termination state, action log probability, and value. GAE is computed per
client across episode boundaries. Optimization runs on a copied model while
the frozen policy continues acting; later transitions are discarded. A ready
candidate switches globally only at a boundary. If it is not ready, the old
policy remains for the entire next round.

`training.toml` contains the initial defaults. Only PPO initialization and
action sampling use its seed; Minecraft rounds are not seeded by the trainer.
The policy and value networks are separate two-layer, 128-unit MLPs without
recurrence or history. At five decisions per second, `gamma = 0.999` gives an
effective reward horizon of roughly 200 seconds.

Runs preserve `untrained.zip`, `latest.zip`, `best.zip`, all update candidates,
round records, PPO metrics, action distributions, client cadence/throughput,
deployment revisions, server identity, restart count, expected pool size, and
world seed. Every archive embeds protocol, spaces, normalization, and
deployment metadata. Unsupported archives, including old DQN files, are
rejected before SB3 loads them.

The recorded server restart count is the baseline captured before a run, so a
healthy server that has already recovered may start a new bounded stage. A
restart during collection disconnects the fixed client pool and raises an
infrastructure error; it is never converted into a transition or a passed
stage.

The `#image` app retains the generic trainer GHCR repository. `/artifacts` is
the persistent trainer volume and is independent of the server's ephemeral
world. The root README is the canonical orchestration guide.
