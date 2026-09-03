{inputs, ...}: {
  perSystem = {
    pkgs,
    serverArtifacts,
    ...
  }: {
    checks = {
      core-tests =
        pkgs.runCommand "jump-server-core-tests" {
          nativeBuildInputs = [pkgs.jdk25_headless];
          src = ../.;
        } ''
          cp -R "$src" source
          chmod -R u+w source
          cd source
          mkdir classes
          find src/main/java/gg/wellplayed/jump/server/core \
            src/test/java/gg/wellplayed/jump/server/core \
            -type f -name '*.java' | sort > sources.txt
          javac --release 25 -d classes @sources.txt
          java -ea -cp classes gg.wellplayed.jump.server.core.CoreTestMain
          touch "$out"
        '';

      protocol-generation =
        pkgs.runCommand "jump-server-protocol-generation" {
          nativeBuildInputs = [pkgs.jdk25_headless pkgs.protobuf];
        } ''
          mkdir -p generated/classes
          protoc \
            --proto_path=${inputs.protocol}/proto \
            --java_out=generated \
            ${inputs.protocol}/proto/jump/v1/jump.proto
          test -f generated/gg/wellplayed/jump/protocol/v1/WireMessage.java
          grep -q 'class ResetRequest' \
            generated/gg/wellplayed/jump/protocol/v1/ResetRequest.java
          touch "$out"
        '';

      plugin-build = serverArtifacts.plugin;

      container-entrypoint-smoke = pkgs.runCommand "jump-server-entrypoint-smoke" {} ''
        output=$(JUMP_ENTRYPOINT_VALIDATE=1 JUMP_CLIENT_COUNT=101 \
          ${serverArtifacts.containerEntrypoint}/bin/jump-server-container)
        test "$output" = "clients=101 heap=512m..1g runtime=/data"
        touch "$out"
      '';

      packaged-mojang-runtime = pkgs.runCommand "jump-server-packaged-mojang-runtime" {} ''
        echo \
          "cdacdfb25898de5e4b4b0e5ddcc2722f77067e46605709c2d886c000ebb63ec5  ${serverArtifacts.serverPackage}/share/jump-benchmark-server/mojang-26.2.jar" \
          | sha256sum --check --status
        grep -q 'cache/mojang_26.2.jar' ${./apps.nix}
        grep -q 'cache/mojang_26.2.jar' ${./packages.nix}
        touch "$out"
      '';

      protobuf-isolation =
        pkgs.runCommand "jump-server-protobuf-isolation" {
          nativeBuildInputs = [pkgs.jdk25_headless];
        } ''
          jar tf \
            ${serverArtifacts.plugin}/share/jump-benchmark-server/jump-benchmark-paper.jar \
            > contents.txt
          grep -q \
            '^gg/wellplayed/jump/server/internal/protobuf/MessageLite.class$' \
            contents.txt
          if grep -q '^com/google/protobuf/' contents.txt; then
            echo "unrelocated protobuf classes would collide with Paper" >&2
            exit 1
          fi
          touch "$out"
        '';

      idempotent-handshake = pkgs.runCommand "jump-server-idempotent-handshake" {} ''
        grep -q 'prior.sessionId.equals(hello.getSessionId())' \
          ${../src/main/java/gg/wellplayed/jump/server/JumpBenchmarkPlugin.java}
        grep -q 'sendConnectionReady(prior, hello.getClientTick())' \
          ${../src/main/java/gg/wellplayed/jump/server/JumpBenchmarkPlugin.java}
        touch "$out"
      '';

      isolated-world-configuration = pkgs.runCommand "jump-server-isolated-world-configuration" {} ''
        grep -q 'level-type=minecraft:flat' ${../nix/apps.nix}
        grep -q '"layers".*"minecraft:bedrock".*"height":1' ${../nix/apps.nix}
        grep -q 'generate-structures=false' ${../nix/apps.nix}
        grep -q 'spawn-animals=false' ${../nix/apps.nix}
        grep -q 'spawn-monsters=false' ${../nix/apps.nix}
        grep -q 'spawn-npcs=false' ${../nix/apps.nix}
        grep -q 'world.setSpawnLocation(initialSpawn)' \
          ${../src/main/java/gg/wellplayed/jump/server/ArenaManager.java}
        grep -q 'event.setSpawnLocation(spawn.clone())' \
          ${../src/main/java/gg/wellplayed/jump/server/JumpBenchmarkPlugin.java}
        grep -q 'geometry.endBarrierX()' \
          ${../src/main/java/gg/wellplayed/jump/server/ArenaManager.java}
        touch "$out"
      '';
    };
  };
}
