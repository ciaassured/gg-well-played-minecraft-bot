# Parallel DQN trainer

The trainer owns Gymnasium semantics, deterministic normalization/reward,
seed partitions, the fixed actor pool, batched inference, one spawned SB3 DQN
learner, validation promotion, final evaluation, and checkpoint/metric
artifacts. It never starts Paper or Minecraft and contains no recording code.

```console
nix develop ./trainer
nix build ./trainer
nix build ./trainer#oci
nix run ./trainer#image
nix flake check ./trainer
(cd trainer && nix fmt)
nix run ./trainer#smoke
nix run ./trainer#capacity -- --transitions 2000
nix run ./trainer#train -- --timesteps 30000
nix run ./trainer#pipeline -- --run-id <id> --timesteps 30000
```

Every command accepts either repeatable `--endpoint HOST:PORT`, or
`--endpoint-template ... --clients N`. `--host`/`--port` remain the local
single-client fallback and cannot be mixed with pool options. Duplicate or
invalid endpoints and nonpositive counts are rejected. Kubernetes sets the
pool startup timeout to 900 seconds for first-time client downloads.

One I/O actor thread owns each endpoint. The coordinator permits one in-flight
transition per actor and batches all currently available observations. The
spawned learner exclusively owns the SB3 model, replay buffer, optimizer,
counters, and checkpoint writes. Its transition queue holds at most two
batches; learner death, saturation, client loss, or policy lag over two action
cycles fails the complete run.

Schedules use aggregate transitions regardless of client count: learning
starts after 500 transitions, one gradient step occurs per four later
transitions, the target updates every 1,000 transitions, and epsilon/checkpoint
intervals use aggregate counts. Validation barriers stop actions, collect the
in-flight pool width, drain the learner, and evaluate a frozen policy without
changing replay state. Fixed validation/evaluation seeds are each assigned
once and results are sorted by seed. Training streams are independently derived
from `(run seed, client ordinal)`.

`pipeline` trains and promotes with the established lexicographic ordering,
then evaluates `best.zip` in the same process and Job. `capacity` exercises the
same actor/inference coordinator with a scripted policy and no learning.

The `#image` app builds `result-trainer-image`. Use `#image -- load <tag>` to
load the same archive into Podman, or set `JUMP_LOCAL_IMAGE_TRANSPORT` to
`docker-daemon` for Docker. The root README documents coordinated publication.
