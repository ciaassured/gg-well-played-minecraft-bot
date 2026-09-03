# Fabric client bridge

This project packages the Fabric bridge and an isolated HeadlessMC runtime. A
client maintains its Minecraft connection across trainer commands, exposes one
single-peer trainer socket, applies one action per tick, and releases every
controlled input on either transport failure. It does not record episodes or
load Replay Mod.

```console
nix develop ./client-mod
nix build ./client-mod
nix build ./client-mod#oci
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

Readiness is published atomically only after the trainer listener is bound and
Paper acknowledges protocol v3. It is removed on Paper loss. Kubernetes uses
an exec file probe because connecting to port 64123 would consume its sole
trainer peer. Unexpected trainer loss aborts the active episode and releases
controls without disconnecting Minecraft. Paper loss is reported to a connected
trainer and retried with exponential backoff capped at 30 seconds.

Each Kubernetes StatefulSet ordinal mounts a 2 GiB PVC at `/runtime`, so the
first public Minecraft download is reused after restarts. Container memory
limits must remain at least 512 MiB above `JUMP_CLIENT_XMX` for native JVM/LWJGL
memory and filesystem cache.
