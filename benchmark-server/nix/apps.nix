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
      level-name=jump-benchmark
      max-players=1
      motd=One-block jump benchmark
      online-mode=false
      player-idle-timeout=0
      server-port=25565
      simulation-distance=5
      spawn-animals=false
      spawn-monsters=false
      spawn-npcs=false
      spawn-protection=0
      sync-chunk-writes=false
      view-distance=5
      white-list=false
    '';
    eula = pkgs.writeText "eula.txt" ''
      eula=true
    '';
    launcher = pkgs.writeShellApplication {
      name = "jump-benchmark-server";
      runtimeInputs = [pkgs.coreutils pkgs.jdk25_headless];
      text = ''
        runtime_dir="''${JUMP_BENCHMARK_SERVER_RUNTIME:-$PWD/benchmark-server/runtime}"
        mkdir -p "$runtime_dir/plugins"
        ln -sfn ${serverArtifacts.serverPackage}/share/jump-benchmark-server/paper-26.2-112.jar \
          "$runtime_dir/paper.jar"
        ln -sfn ${serverArtifacts.serverPackage}/share/jump-benchmark-server/jump-benchmark-paper.jar \
          "$runtime_dir/plugins/jump-benchmark-paper.jar"
        if [[ ! -e "$runtime_dir/eula.txt" ]]; then
          cp ${eula} "$runtime_dir/eula.txt"
        fi
        if [[ ! -e "$runtime_dir/server.properties" ]]; then
          cp ${serverProperties} "$runtime_dir/server.properties"
        fi
        chmod u+w "$runtime_dir/eula.txt" "$runtime_dir/server.properties"
        cd "$runtime_dir"
        exec java -Xms512m -Xmx1g -jar paper.jar --nogui "$@"
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
