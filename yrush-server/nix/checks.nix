{...}: {
  perSystem = {
    pkgs,
    serverArtifacts,
    ...
  }: {
    checks = {
      package-build = serverArtifacts.serverPackage;

      paper-startup =
        pkgs.runCommand "yrush-paper-startup" {
          nativeBuildInputs = [
            pkgs.coreutils
            pkgs.gnugrep
            pkgs.gnused
            pkgs.jdk25_headless
            pkgs.util-linux
          ];
        } ''
          runtime="$TMPDIR/server"
          mkdir -p "$runtime/cache" "$runtime/plugins/YRush"
          ln -s ${serverArtifacts.paperServer} "$runtime/paper.jar"
          ln -s ${serverArtifacts.mojangServer} "$runtime/cache/mojang_26.2.jar"
          ln -s ${serverArtifacts.yrushPlugin} "$runtime/plugins/YRush.jar"
          cp ${serverArtifacts.serverPackage}/share/yrush-server/yrush-config.yml \
            "$runtime/plugins/YRush/config.yml"
          sed \
            -e 's/@MAX_PLAYERS@/1/' \
            -e 's/@WORLD_SEED@/20260904/' \
            -e 's/^server-port=.*/server-port=0/' \
            ${serverArtifacts.serverPackage}/share/yrush-server/server.properties.template \
            > "$runtime/server.properties"
          printf 'eula=true\n' > "$runtime/eula.txt"
          cd "$runtime"
          (
            for attempt in $(seq 1 180); do
              if grep -Fq 'YRush enabled.' logs/latest.log 2>/dev/null \
                && grep -Fq 'Done (' logs/latest.log 2>/dev/null; then
                printf 'stop\n'
                exit 0
              fi
              sleep 1
            done
            exit 1
          ) | timeout 240 script -qefc \
            'java -Xms512m -Xmx1g -jar paper.jar --nogui' \
            --flush /dev/null > console.log 2>&1
          grep -Fq 'YRush enabled.' logs/latest.log
          grep -Fq 'Done (' logs/latest.log
          grep -Fq 'Disabling YRush' logs/latest.log
          grep -Fq 'Stopping server' logs/latest.log
          touch "$out"
        '';

      pinned-yrush-release =
        pkgs.runCommand "pinned-yrush-v1.3.1" {
          nativeBuildInputs = [pkgs.unzip];
        } ''
          echo \
            "fc52d9473e5f27b05acb2c162b60b4783de046f81977592ab885b4620094a5f7  ${serverArtifacts.yrushPlugin}" \
            | sha256sum --check --status
          unzip -p ${serverArtifacts.yrushPlugin} plugin.yml > plugin.yml
          grep -q '^name: YRush$' plugin.yml
          grep -q "api-version: '26.2'" plugin.yml
          touch "$out"
        '';

      container-entrypoint-smoke = pkgs.runCommand "yrush-server-entrypoint-smoke" {} ''
        output=$(YRUSH_ENTRYPOINT_VALIDATE=1 \
          YRUSH_EXPECTED_CLIENT_COUNT=12 \
          YRUSH_MAX_PLAYERS=14 \
          YRUSH_WORLD_SEED=-42 \
          YRUSH_STARTUP_TIMEOUT_SECONDS=300 \
          POD_UID=pod-uid \
          POD_RESTART_COUNT=2 \
          ${serverArtifacts.containerEntrypoint}/bin/yrush-server-container)
        test "$output" = \
          "expected=12 max-players=14 heap=2g..4g runtime=/data seed=-42 startup-timeout=300 pod=pod-uid restart=2"

        if YRUSH_ENTRYPOINT_VALIDATE=1 \
          YRUSH_EXPECTED_CLIENT_COUNT=4 YRUSH_MAX_PLAYERS=3 \
          ${serverArtifacts.containerEntrypoint}/bin/yrush-server-container 2>/dev/null; then
          echo "max players below the client pool unexpectedly passed validation" >&2
          exit 1
        fi
        touch "$out"
      '';

      normal-world-configuration = pkgs.runCommand "yrush-normal-world-configuration" {} ''
        grep -q '^level-type=minecraft:normal$' \
          ${serverArtifacts.serverPackage}/share/yrush-server/server.properties.template
        grep -q '^generate-structures=true$' \
          ${serverArtifacts.serverPackage}/share/yrush-server/server.properties.template
        grep -q '^level-seed=@WORLD_SEED@$' \
          ${serverArtifacts.serverPackage}/share/yrush-server/server.properties.template
        if grep -qiE 'flat|generator-settings' \
          ${serverArtifacts.serverPackage}/share/yrush-server/server.properties.template; then
          echo "server package still selects a synthetic world" >&2
          exit 1
        fi
        touch "$out"
      '';

      lifecycle-configuration = pkgs.runCommand "yrush-lifecycle-configuration" {} ''
        grep -A1 '^bot-packets:$' \
          ${serverArtifacts.serverPackage}/share/yrush-server/yrush-config.yml \
          | grep -q 'enabled: true'
        grep -q "printf 'yrush start training" ${./packages.nix}
        grep -q 'expected clients did not arrive before timeout' ${./packages.nix}
        grep -q 'fixed client pool changed' ${./packages.nix}
        grep -q "printf 'yrush stop" ${./packages.nix}
        grep -q 'YRUSH_METRIC' ${./packages.nix}
        grep -q 'world_growth_bytes' ${./packages.nix}
        grep -q 'round_preparation_ms' ${./packages.nix}
        grep -q 'YRUSH_FAIL_ON_CONTAINER_RESTART' ${./packages.nix}
        grep -q 'refusing to reuse the active world' ${./packages.nix}
        touch "$out"
      '';

      ephemeral-runtime = pkgs.runCommand "yrush-ephemeral-runtime" {} ''
        grep -q 'YRUSH_SERVER_RUNTIME:-/data' ${./packages.nix}
        grep -q 'WorkingDir = "/data"' ${./packages.nix}
        grep -q 'PATH=.*makeBinPath' ${./packages.nix}
        grep -q 'pkgs.coreutils' ${./packages.nix}
        if grep -qiE 'persistentvolumeclaim|volumeclaimtemplates' ${./packages.nix}; then
          echo "server package unexpectedly provisions persistent world storage" >&2
          exit 1
        fi
        touch "$out"
      '';
    };
  };
}
