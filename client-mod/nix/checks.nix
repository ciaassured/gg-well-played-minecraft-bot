{inputs, ...}: {
  perSystem = {
    pkgs,
    clientArtifacts,
    ...
  }: {
    checks = {
      mod-build = clientArtifacts.clientMod;
      hmc-optimizations-build = clientArtifacts.hmcOptimizations;

      hmc-optimizations-metadata =
        pkgs.runCommand "hmc-optimizations-metadata" {
          nativeBuildInputs = [pkgs.jq pkgs.unzip];
        } ''
          mkdir jar
          unzip -q \
            ${clientArtifacts.hmcOptimizations}/share/hmc-optimizations/hmc-optimizations.jar \
            -d jar
          jq -e '
            .id == "hmc_optimizations"
            and .version == "0.5.0"
            and .environment == "client"
            and .depends.fabricloader == ">=0.19.3"
            and .depends.minecraft == "~26.2"
            and .depends.java == ">=25"
          ' jar/fabric.mod.json >/dev/null
          touch "$out"
        '';

      container-entrypoint-smoke = pkgs.runCommand "yrush-client-entrypoint-smoke" {} ''
        output=$(YRUSH_ENTRYPOINT_VALIDATE=1 POD_NAME=yrush-client-117 \
          ${clientArtifacts.containerEntrypoint}/bin/yrush-client-container)
        echo "$output" | grep -q 'username=yrushbot-117'
        echo "$output" | grep -q 'bind=0.0.0.0:64123'
        echo "$output" | grep -q 'paper=yrush-paper:25565'
        touch "$out"
      '';

      core-tests =
        pkgs.runCommand "yrush-client-core-tests" {
          nativeBuildInputs = [pkgs.jdk25_headless pkgs.protobuf];
          src = ../.;
        } ''
          cp -R "$src" source
          chmod -R u+w source
          cd source
          mkdir -p generated classes
          protoc \
            --proto_path=${inputs.protocol}/proto \
            --java_out=generated \
            ${inputs.protocol}/proto/yrush/v1/yrush.proto
          find generated \
            src/main/java/gg/wellplayed/yrush/client/core \
            src/test/java/gg/wellplayed/yrush/client/core \
            -type f -name '*.java' | sort > sources.txt
          javac --release 25 \
            -cp ${clientArtifacts.protobufJava}:${clientArtifacts.gsonJava} \
            -d classes @sources.txt
          java -ea \
            -cp classes:${clientArtifacts.protobufJava}:${clientArtifacts.gsonJava} \
            gg.wellplayed.yrush.client.core.CoreTestMain
          touch "$out"
        '';

      protocol-generation =
        pkgs.runCommand "yrush-client-protocol-generation" {
          nativeBuildInputs = [pkgs.jdk25_headless pkgs.protobuf];
        } ''
          mkdir -p generated
          protoc \
            --proto_path=${inputs.protocol}/proto \
            --java_out=generated \
            ${inputs.protocol}/proto/yrush/v1/yrush.proto
          test -f generated/gg/wellplayed/yrush/protocol/v1/Observation.java
          grep -q 'class ArmEpisode' \
            generated/gg/wellplayed/yrush/protocol/v1/ArmEpisode.java
          touch "$out"
        '';

      lifecycle-and-controls = pkgs.runCommand "yrush-client-lifecycle-and-controls" {} ''
        source=${../src/main/java/gg/wellplayed/yrush/client/YRushClient.java}
        grep -q 'Identifier.fromNamespaceAndPath("yrush", "bot_state")' \
          ${../src/main/java/gg/wellplayed/yrush/client/YRushStatePayload.java}
        grep -q 'ACTION_HOLD_TICKS = 4' \
          ${../src/main/java/gg/wellplayed/yrush/client/core/RoundSequencer.java}
        grep -q 'selectMiningTool(client)' "$source"
        grep -q 'keySprint.setDown(inputs.sprint())' "$source"
        grep -q 'keyAttack.setDown(inputs.attack())' "$source"
        grep -q 'ClientLifecycleEvents.CLIENT_STOPPING' "$source"
        grep -q 'releaseAll(client)' "$source"
        grep -q 'YRUSH_PAPER_ADDRESS' ${./packages.nix}
        grep -q 'yrush.client.server' ${./packages.nix}
        touch "$out"
      '';
    };
  };
}
