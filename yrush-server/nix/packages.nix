{...}: {
  perSystem = {pkgs, ...}: let
    paperServer = pkgs.fetchurl {
      url = "https://fill-data.papermc.io/v1/objects/bd3a58cf96874e5ea6643f5f6fe9b4f5bf9e34b795fa078c2f0ee8b98b2f907e/paper-26.2-112.jar";
      hash = "sha256-vTpYz5aHTl6mZD9fb+m09b+eNLeV+geMLw7ouYsvkH4=";
    };
    mojangServer = pkgs.fetchurl {
      url = "https://piston-data.mojang.com/v1/objects/823e2250d24b3ddac457a60c92a6a941943fcd6a/server.jar";
      hash = "sha256-zazfsliY3l5LSw5d3MJyL3cGfkZgVwnC2IbAAOu2PsU=";
    };
    yrushPlugin = pkgs.fetchurl {
      url = "https://github.com/ciaassured/minecraft-yrush/releases/download/v1.3.1/YRush.jar";
      hash = "sha256-/FLZRz5fJ7BayywWK2C0eD3gRvgZd1kquIW0YgCUpfc=";
    };
    serverProperties = pkgs.writeText "yrush-server.properties" ''
      allow-flight=true
      allow-nether=true
      difficulty=normal
      enable-command-block=false
      enforce-secure-profile=false
      force-gamemode=true
      gamemode=survival
      generate-structures=true
      level-name=world
      level-seed=@WORLD_SEED@
      level-type=minecraft:normal
      max-players=@MAX_PLAYERS@
      motd=YRush training server
      online-mode=false
      pause-when-empty-seconds=-1
      player-idle-timeout=0
      server-port=25565
      simulation-distance=6
      spawn-animals=true
      spawn-monsters=true
      spawn-npcs=true
      spawn-protection=0
      sync-chunk-writes=true
      view-distance=10
      white-list=false
    '';
    yrushConfig = pkgs.writeText "yrush-config.yml" ''
      round:
        countdown-seconds: 5
        between-rounds-seconds: 5
        timeout-seconds: 240

      target-y:
        minimum-distance: 10
        maximum-distance: 50

      start-location:
        radius: 3000

      bot-packets:
        enabled: true

      debug:
        enabled: true
    '';
    serverPackage = pkgs.runCommand "yrush-server-1.3.1" {} ''
      mkdir -p "$out/share/yrush-server"
      cp ${paperServer} "$out/share/yrush-server/paper-26.2-112.jar"
      cp ${mojangServer} "$out/share/yrush-server/mojang-26.2.jar"
      cp ${yrushPlugin} "$out/share/yrush-server/YRush-1.3.1.jar"
      cp ${serverProperties} "$out/share/yrush-server/server.properties.template"
      cp ${yrushConfig} "$out/share/yrush-server/yrush-config.yml"
    '';
    containerEntrypoint = pkgs.writeShellApplication {
      name = "yrush-server-container";
      runtimeInputs = [
        pkgs.coreutils
        pkgs.findutils
        pkgs.gawk
        pkgs.gnugrep
        pkgs.gnused
        pkgs.jdk25_headless
      ];
      text = ''
        runtime_dir="''${YRUSH_SERVER_RUNTIME:-/data}"
        expected_clients="''${YRUSH_EXPECTED_CLIENT_COUNT:-1}"
        expected_client_names="''${YRUSH_EXPECTED_CLIENT_NAMES:-yrushbot-0}"
        max_players="''${YRUSH_MAX_PLAYERS:-$expected_clients}"
        server_xms="''${YRUSH_SERVER_XMS:-2g}"
        server_xmx="''${YRUSH_SERVER_XMX:-4g}"
        world_seed="''${YRUSH_WORLD_SEED:-20260904}"
        startup_timeout="''${YRUSH_STARTUP_TIMEOUT_SECONDS:-900}"
        pod_uid="''${POD_UID:-local}"
        restart_count="''${POD_RESTART_COUNT:-0}"

        if [[ ! "$expected_clients" =~ ^[1-9][0-9]*$ ]]; then
          echo "YRUSH_EXPECTED_CLIENT_COUNT must be a positive integer" >&2
          exit 2
        fi
        IFS=',' read -r -a required_clients <<<"$expected_client_names"
        if (( ''${#required_clients[@]} != expected_clients )); then
          echo "YRUSH_EXPECTED_CLIENT_NAMES must contain exactly YRUSH_EXPECTED_CLIENT_COUNT names" >&2
          exit 2
        fi
        declare -A unique_required_clients=()
        for required_client in "''${required_clients[@]}"; do
          if [[ ! "$required_client" =~ ^[A-Za-z0-9_-]{1,16}$ ]]; then
            echo "YRUSH_EXPECTED_CLIENT_NAMES contains an invalid client username" >&2
            exit 2
          fi
          if [[ -n "''${unique_required_clients[$required_client]:-}" ]]; then
            echo "YRUSH_EXPECTED_CLIENT_NAMES must not contain duplicates" >&2
            exit 2
          fi
          unique_required_clients[$required_client]=1
        done
        if [[ ! "$max_players" =~ ^[1-9][0-9]*$ ]] || (( max_players < expected_clients )); then
          echo "YRUSH_MAX_PLAYERS must be an integer at least YRUSH_EXPECTED_CLIENT_COUNT" >&2
          exit 2
        fi
        if [[ ! "$world_seed" =~ ^-?[0-9]+$ ]]; then
          echo "YRUSH_WORLD_SEED must be an integer" >&2
          exit 2
        fi
        if [[ ! "$startup_timeout" =~ ^[1-9][0-9]*$ ]]; then
          echo "YRUSH_STARTUP_TIMEOUT_SECONDS must be a positive integer" >&2
          exit 2
        fi
        if [[ ! "$restart_count" =~ ^[0-9]+$ ]]; then
          echo "POD_RESTART_COUNT must be a non-negative integer" >&2
          exit 2
        fi
        if [[ -z "$server_xms" || -z "$server_xmx" ]]; then
          echo "YRUSH_SERVER_XMS and YRUSH_SERVER_XMX must not be blank" >&2
          exit 2
        fi
        if [[ "''${YRUSH_ENTRYPOINT_VALIDATE:-0}" == 1 ]]; then
          printf 'expected=%s names=%s max-players=%s heap=%s..%s runtime=%s seed=%s startup-timeout=%s pod=%s restart=%s\n' \
            "$expected_clients" "$expected_client_names" "$max_players" "$server_xms" "$server_xmx" \
            "$runtime_dir" "$world_seed" "$startup_timeout" "$pod_uid" "$restart_count"
          exit 0
        fi

        mkdir -p "$runtime_dir/cache" "$runtime_dir/plugins/YRush"
        start_marker="$runtime_dir/.yrush-server-started"
        if [[ -s "$start_marker" ]]; then
          previous_pod_uid=""
          previous_restart_count=""
          IFS=$'\t' read -r previous_pod_uid previous_restart_count < "$start_marker" || true
          if [[ "$previous_pod_uid" == "$pod_uid" ]]; then
            if [[ "$previous_restart_count" =~ ^[0-9]+$ ]]; then
              restart_count=$((previous_restart_count + 1))
            else
              restart_count=$((restart_count + 1))
            fi
            printf 'recovering YRush server container in pod %s (restart=%s); preserving world\n' \
              "$pod_uid" "$restart_count"
          else
            printf 'starting YRush server with runtime from pod %s under pod %s; preserving world\n' \
              "$previous_pod_uid" "$pod_uid"
          fi
        fi
        printf '%s\t%s\n' "$pod_uid" "$restart_count" > "$start_marker.tmp"
        mv "$start_marker.tmp" "$start_marker"
        ln -sfn ${serverPackage}/share/yrush-server/paper-26.2-112.jar \
          "$runtime_dir/paper.jar"
        ln -sfn ${serverPackage}/share/yrush-server/mojang-26.2.jar \
          "$runtime_dir/cache/mojang_26.2.jar"
        ln -sfn ${serverPackage}/share/yrush-server/YRush-1.3.1.jar \
          "$runtime_dir/plugins/YRush.jar"
        install -m 0644 ${yrushConfig} "$runtime_dir/plugins/YRush/config.yml"
        printf 'eula=true\n' > "$runtime_dir/eula.txt"
        sed \
          -e "s/@MAX_PLAYERS@/$max_players/" \
          -e "s/@WORLD_SEED@/$world_seed/" \
          ${serverProperties} > "$runtime_dir/server.properties"

        ready_file="$runtime_dir/ready"
        fatal_file="$runtime_dir/fixed-pool-failure"
        console_fifo="$runtime_dir/.yrush-console"
        rm -f "$ready_file" "$fatal_file" "$console_fifo"
        if [[ "''${YRUSH_ENTRYPOINT_PREPARE_ONLY:-0}" == 1 ]]; then
          printf 'prepared pod=%s restart=%s runtime=%s\n' \
            "$pod_uid" "$restart_count" "$runtime_dir"
          exit 0
        fi
        mkfifo "$console_fifo"
        exec 3<>"$console_fifo"
        cd "$runtime_dir"
        java -Xms"$server_xms" -Xmx"$server_xmx" -jar paper.jar --nogui \
          <"$console_fifo" &
        server_pid=$!
        terminating=0

        # shellcheck disable=SC2329 # Invoked indirectly by the signal trap.
        shutdown() {
          terminating=1
          rm -f "$ready_file"
          printf 'yrush stop\nstop\n' >&3 || true
        }
        trap shutdown TERM INT

        fail_startup() {
          echo "YRush server readiness failed: $1" >&2
          printf '%s\n' "$1" > "$fatal_file"
          rm -f "$ready_file"
          printf 'yrush stop\nstop\n' >&3 || true
          wait "$server_pid" 2>/dev/null || true
          exit 1
        }

        log_file="$runtime_dir/logs/latest.log"
        deadline=$((SECONDS + startup_timeout))
        until [[ -f "$log_file" ]] && grep -q 'Done (' "$log_file"; do
          kill -0 "$server_pid" 2>/dev/null || fail_startup "Paper exited before readiness"
          (( SECONDS < deadline )) || fail_startup "Paper startup timed out"
          sleep 1
        done

        online_snapshot() {
          local first_line response count roster attempt
          first_line=$(wc -l < "$log_file")
          printf 'list\n' >&3
          for attempt in 1 2 3 4 5 6 7 8 9 10; do
            response=$(tail -n "+$((first_line + 1))" "$log_file" \
              | grep -E 'There are [0-9]+ of a max of [0-9]+ players online' \
              | tail -n 1 || true)
            if [[ -n "$response" ]]; then
              count=$(sed -E 's/.*There are ([0-9]+) of a max.*/\1/' <<<"$response")
              roster=$(sed -E 's/.*players online:[[:space:]]*//' <<<"$response")
              printf '%s\t%s\n' "$count" "$roster"
              return 0
            fi
            if (( attempt < 10 )); then
              sleep 0.2
            fi
          done
          return 1
        }

        missing_required_clients() {
          local roster="$1" required_client wrapped_roster missing
          wrapped_roster=",''${roster// /},"
          missing=""
          for required_client in "''${required_clients[@]}"; do
            if [[ "$wrapped_roster" != *",$required_client,"* ]]; then
              missing="''${missing:+$missing,}$required_client"
            fi
          done
          printf '%s\n' "$missing"
        }

        clients=0
        roster=""
        missing_clients="$expected_client_names"
        until [[ -z "$missing_clients" ]]; do
          kill -0 "$server_pid" 2>/dev/null || fail_startup "Paper exited while waiting for clients"
          (( SECONDS < deadline )) || fail_startup "expected clients did not arrive before timeout"
          snapshot=$(online_snapshot || printf '0\t')
          IFS=$'\t' read -r clients roster <<<"$snapshot"
          missing_clients=$(missing_required_clients "$roster")
          if (( clients >= max_players )) && [[ -n "$missing_clients" ]]; then
            fail_startup "server is full before all required clients arrived: missing=$missing_clients"
          fi
          sleep 1
        done

        start_line=$(wc -l < "$log_file")
        printf 'yrush start training\n' >&3
        training_started=0
        while (( SECONDS < deadline )); do
          new_log=$(tail -n "+$((start_line + 1))" "$log_file")
          if grep -q 'Starting YRush. mode=training' <<<"$new_log"; then
            launch_line=$(grep -E 'Launching round=[0-9]+ participants=[0-9]+ mode=training' \
              <<<"$new_log" | tail -n 1 || true)
            if [[ -n "$launch_line" ]]; then
              launch_participants=$(sed -E \
                's/.*Launching round=[0-9]+ participants=([0-9]+) mode=training.*/\1/' \
                <<<"$launch_line")
              if (( launch_participants < expected_clients )); then
                fail_startup "YRush launched without the complete required client pool"
              fi
              training_started=1
              break
            fi
          fi
          if grep -qE 'YRush is already running|No eligible players found' <<<"$new_log"; then
            fail_startup "YRush training mode was rejected"
          fi
          kill -0 "$server_pid" 2>/dev/null || fail_startup "Paper exited while starting YRush"
          sleep 1
        done
        (( training_started == 1 )) || fail_startup "YRush training mode did not start"

        snapshot=$(online_snapshot || fail_startup "could not verify required clients after starting YRush")
        IFS=$'\t' read -r clients roster <<<"$snapshot"
        missing_clients=$(missing_required_clients "$roster")
        if [[ -n "$missing_clients" ]]; then
          fail_startup "required clients departed while starting YRush: missing=$missing_clients"
        fi

        printf '{"pod_uid":"%s","restart_count":%s,"expected_clients":%s,"expected_client_names":"%s","world_seed":"%s"}\n' \
          "$pod_uid" "$restart_count" "$expected_clients" "$expected_client_names" \
          "$world_seed" > "$ready_file.tmp"
        mv "$ready_file.tmp" "$ready_file"
        echo "YRush required client pool ready: clients=$expected_client_names online=$clients pod=$pod_uid restart=$restart_count"

        monitor_preparation() {
          local line preparation_started now preparation_ms
          preparation_started=0
          tail -n 0 -F -s 0.05 "$log_file" | while IFS= read -r line; do
            now=$(date +%s%3N)
            if [[ "$line" == *"Launching round="* ]]; then
              preparation_started=$now
            elif [[ "$line" == *"Round active."* ]] && (( preparation_started > 0 )); then
              preparation_ms=$((now - preparation_started))
              printf 'YRUSH_METRIC {"round_preparation_ms":%s}\n' "$preparation_ms"
              preparation_started=0
            fi
          done
        }
        monitor_preparation &
        preparation_pid=$!

        monitor_pool() {
          local observed roster snapshot missing_clients departed_client
          local disk_bytes world_bytes initial_world_bytes world_growth_bytes
          local pool_log_line pool_log_end new_pool_log
          initial_world_bytes=0
          if [[ -d "$runtime_dir/world" ]]; then
            initial_world_bytes=$(du -sb "$runtime_dir/world" | awk '{print $1}')
          fi
          pool_log_line=$(wc -l < "$log_file")
          while kill -0 "$server_pid" 2>/dev/null; do
            if ! snapshot=$(online_snapshot); then
              echo "YRush roster query timed out; retaining readiness until the next check" >&2
              sleep 10
              continue
            fi
            IFS=$'\t' read -r observed roster <<<"$snapshot"
            missing_clients=$(missing_required_clients "$roster")
            pool_log_end=$(wc -l < "$log_file")
            new_pool_log=""
            if (( pool_log_end > pool_log_line )); then
              new_pool_log=$(sed -n "$((pool_log_line + 1)),''${pool_log_end}p" "$log_file")
              pool_log_line=$pool_log_end
            fi
            departed_client=""
            for required_client in "''${required_clients[@]}"; do
              if grep -Eq \
                "(^|[^A-Za-z0-9_-])$required_client (lost connection:|left the game)" \
                <<<"$new_pool_log"; then
                departed_client="$required_client"
                break
              fi
            done
            if [[ -n "$missing_clients" || -n "$departed_client" ]]; then
              printf 'required client pool changed: expected=%s missing=%s departed=%s online=%s\n' \
                "$expected_client_names" "$missing_clients" "$departed_client" "$observed" \
                > "$fatal_file"
              rm -f "$ready_file"
              echo "YRush required-client failure: expected=$expected_client_names missing=$missing_clients departed=$departed_client online=$observed" >&2
              printf 'yrush stop\nstop\n' >&3 || true
              return 1
            fi

            disk_bytes=$(du -sb "$runtime_dir" | awk '{print $1}')
            world_bytes=0
            if [[ -d "$runtime_dir/world" ]]; then
              world_bytes=$(du -sb "$runtime_dir/world" | awk '{print $1}')
            fi
            world_growth_bytes=$((world_bytes - initial_world_bytes))
            printf 'YRUSH_METRIC {"disk_bytes":%s,"world_bytes":%s,"world_growth_bytes":%s,"online_players":%s,"required_clients":%s,"pod_uid":"%s","restart_count":%s}\n' \
              "$disk_bytes" "$world_bytes" "$world_growth_bytes" "$observed" \
              "$expected_clients" "$pod_uid" "$restart_count"
            printf 'tps\n' >&3 || true
            sleep 10
          done
        }
        monitor_pool &
        monitor_pid=$!

        set +e
        wait "$server_pid"
        server_status=$?
        if (( terminating == 1 )); then
          wait "$server_pid" 2>/dev/null || true
          server_status=143
        fi
        kill "$monitor_pid" 2>/dev/null || true
        kill "$preparation_pid" 2>/dev/null || true
        wait "$monitor_pid" 2>/dev/null || true
        wait "$preparation_pid" 2>/dev/null || true
        rm -f "$ready_file" "$console_fifo"
        if [[ -f "$fatal_file" ]]; then
          exit 1
        fi
        exit "$server_status"
      '';
    };
    oci = pkgs.dockerTools.buildLayeredImage {
      name = "ghcr.io/ciaassured/gg-well-played-minecraft-bot-server";
      tag = "unstable";
      maxLayers = 120;
      contents = [containerEntrypoint pkgs.cacert];
      config = {
        Entrypoint = ["${containerEntrypoint}/bin/yrush-server-container"];
        WorkingDir = "/data";
        Env = [
          "PATH=${pkgs.lib.makeBinPath [
            pkgs.coreutils
            pkgs.findutils
            pkgs.gawk
            pkgs.gnugrep
            pkgs.gnused
            pkgs.jdk25_headless
          ]}"
          "YRUSH_SERVER_RUNTIME=/data"
          "YRUSH_EXPECTED_CLIENT_COUNT=1"
          "YRUSH_EXPECTED_CLIENT_NAMES=yrushbot-0"
          "YRUSH_MAX_PLAYERS=1"
          "YRUSH_SERVER_XMS=2g"
          "YRUSH_SERVER_XMX=4g"
          "YRUSH_WORLD_SEED=20260904"
          "YRUSH_STARTUP_TIMEOUT_SECONDS=900"
        ];
      };
    };
  in {
    packages = {
      default = serverPackage;
      paper = paperServer;
      plugin = yrushPlugin;
      oci = oci;
      container = oci;
    };

    _module.args.serverArtifacts = {
      inherit containerEntrypoint mojangServer oci paperServer serverPackage yrushPlugin;
    };
  };
}
