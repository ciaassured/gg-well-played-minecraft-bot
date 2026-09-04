# Fabric YRush bridge

This project packages one persistent Fabric/HeadlessMC client. Every configured
client joins the same Paper service, listens to YRush JSON state packets, and
owns one trainer TCP endpoint. It neither controls Paper's lifecycle nor starts
new rounds.

```console
nix develop ./client-mod
nix build ./client-mod
nix build ./client-mod#hmc-optimizations
nix build ./client-mod#oci
nix run ./client-mod#image
nix flake check ./client-mod
(cd client-mod && nix fmt)
nix run ./client-mod#headless
```

The bridge advertises `yrush:bot_state`, validates schema 1 and ordered
`INACTIVE -> LOCKED_COUNTDOWN -> ACTIVE -> ROUND_COMPLETE -> INACTIVE`
lifecycle packets, and skips a round when attached after it became active.
Elimination ends only that client's episode; the process immediately releases
its controls and can wait for the next round while other clients continue.

The observation is a 5×5×5 egocentric voxel map plus normalized target,
movement, within-block position, orientation, health, air, grounded, and time
features. Each six-head action is held for four client ticks. Moving forward
automatically sprints, and the supplied pickaxe is selected at round start.
Every terminal, disconnect, shutdown, timeout, and protocol-failure path
releases all controlled keys.

Configuration is external:

- `YRUSH_CLIENT_RUNTIME` selects the client cache directory.
- `YRUSH_PAPER_ADDRESS` selects the one Paper service.
- `YRUSH_TRAINER_BIND` and `YRUSH_TRAINER_PORT` select the trainer listener.
- `YRUSH_CLIENT_USERNAME` overrides the default `yrushbot-<pod ordinal>` name.
- `YRUSH_CLIENT_READINESS_FILE` selects the readiness path.
- `YRUSH_CLIENT_XMS` and `YRUSH_CLIENT_XMX` select the Minecraft heap.

Readiness is published only after the trainer listener is bound and Minecraft
has joined Paper. It is removed on Paper loss. Kubernetes uses an exec file
probe because connecting to the port would occupy its single trainer peer.
Frame and world rendering remain disabled, but the small entity-renderer
registry is retained because Minecraft's item-pickup network handler uses it
even when no frames are produced.

The `#image` app keeps the generic client GHCR repository. Set
`YRUSH_LOCAL_IMAGE_TRANSPORT=docker-daemon` only when loading into Docker. The
root README is the canonical orchestration guide.
