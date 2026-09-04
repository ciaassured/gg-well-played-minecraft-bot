# Fabric client bridge

This project packages the Fabric bridge and an isolated HeadlessMC runtime. A
client maintains its Minecraft connection across trainer commands, exposes one
single-peer trainer socket, applies one action per tick, and releases every
controlled input on either transport failure. It does not record episodes or
load Replay Mod.

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

Configuration is external:

- `JUMP_CLIENT_RUNTIME` selects the persistent runtime/cache directory.
- `JUMP_PAPER_ADDRESS` selects Paper (default `127.0.0.1:25565` locally).
- `JUMP_TRAINER_BIND` and `JUMP_TRAINER_PORT` select the trainer listener
  (default `127.0.0.1:64123` locally; Kubernetes sets `0.0.0.0:64123`).
- `JUMP_CLIENT_USERNAME` selects the offline name. Otherwise the launcher
  derives `jumpbot-<ordinal>` from `POD_NAME`.
- `JUMP_CLIENT_READINESS_FILE` selects the readiness path.
- `JUMP_CLIENT_XMS` and `JUMP_CLIENT_XMX` select the client JVM heap.

Headless clients use a two-chunk render distance and a five-chunk simulation
distance, the minimum simulation distance accepted by Minecraft 26.2. Dummy
assets and every HMC Optimizations category are enabled by default. The pinned
HMC Optimizations 0.5.0 Fabric build targets Minecraft 26.2, Fabric Loader
0.19.3 or newer, and Java 25.

Readiness is published atomically only after the trainer listener is bound and
Paper acknowledges protocol v3. It is removed on Paper loss. Kubernetes uses
an exec file probe because connecting to port 64123 would consume its sole
trainer peer. Unexpected trainer loss aborts the active episode and releases
controls without disconnecting Minecraft. Paper loss is reported to a connected
trainer and retried with exponential backoff capped at 30 seconds.

Each Kubernetes StatefulSet ordinal mounts a 2 GiB PVC at `/runtime`, so the
first public Minecraft download is reused after restarts. Both launchers default
to `JUMP_CLIENT_XMS=192m` and `JUMP_CLIENT_XMX=320m`. Container memory remains
an independent budget because it includes the HeadlessMC JVM, native JVM/LWJGL
memory, and filesystem cache in addition to the Minecraft heap.

The `#image` app builds `result-client-image`. Use `#image -- load <tag>` to
load the same archive into Podman, or set `JUMP_LOCAL_IMAGE_TRANSPORT` to
`docker-daemon` for Docker. The root README documents coordinated publication.
