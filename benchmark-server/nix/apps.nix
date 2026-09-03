{...}: {
  perSystem = {
    pkgs,
    serverArtifacts,
    ...
  }: let
    serverProperties = pkgs.writeText "jump-server.properties" ''
      allow-flight=false
      allow-nether=false
      difficulty=peaceful
      enable-command-block=false
      enforce-secure-profile=false
      force-gamemode=true
      gamemode=adventure
      generate-structures=false
      generator-settings={"layers":[{"block":"minecraft:bedrock","height":1}],"biome":"minecraft:plains"}
      level-name=jump-benchmark
      level-type=minecraft:flat
      max-players=@MAX_PLAYERS@
      motd=One-block jump benchmark
      online-mode=false
      pause-when-empty-seconds=-1
      player-idle-timeout=0
      server-port=25565
      simulation-distance=2
      spawn-animals=false
      spawn-monsters=false
      spawn-npcs=false
      spawn-protection=0
      sync-chunk-writes=false
      view-distance=2
      white-list=false
    '';
    eula = pkgs.writeText "eula.txt" ''
      eula=true
    '';
    launcher = pkgs.writeShellApplication {
      name = "jump-benchmark-server";
      runtimeInputs = [pkgs.coreutils pkgs.gnused pkgs.jdk25_headless];
      text = ''
        runtime_dir="''${JUMP_BENCHMARK_SERVER_RUNTIME:-$PWD/benchmark-server/runtime}"
        client_count="''${JUMP_CLIENT_COUNT:-1}"
        server_xms="''${JUMP_SERVER_XMS:-512m}"
        server_xmx="''${JUMP_SERVER_XMX:-1g}"
        if [[ ! "$client_count" =~ ^[1-9][0-9]*$ ]]; then
          echo "JUMP_CLIENT_COUNT must be a positive integer" >&2
          exit 2
        fi
        mkdir -p "$runtime_dir/plugins"
        ln -sfn ${serverArtifacts.serverPackage}/share/jump-benchmark-server/paper-26.2-112.jar \
          "$runtime_dir/paper.jar"
        ln -sfn ${serverArtifacts.serverPackage}/share/jump-benchmark-server/jump-benchmark-paper.jar \
          "$runtime_dir/plugins/jump-benchmark-paper.jar"
        if [[ ! -e "$runtime_dir/eula.txt" ]]; then
          cp ${eula} "$runtime_dir/eula.txt"
        fi
        sed "s/@MAX_PLAYERS@/$client_count/" ${serverProperties} > "$runtime_dir/server.properties"
        chmod u+w "$runtime_dir/eula.txt" "$runtime_dir/server.properties"
        cd "$runtime_dir"
        exec java -Xms"$server_xms" -Xmx"$server_xmx" -jar paper.jar --nogui "$@"
      '';
    };
  in {
    apps.server = {
      type = "app";
      program = "${launcher}/bin/jump-benchmark-server";
      meta.description = "Start the isolated Paper 26.2 benchmark server";
    };
    apps.default = {
      type = "app";
      program = "${launcher}/bin/jump-benchmark-server";
      meta.description = "Start the isolated Paper 26.2 benchmark server";
    };
  };
}
