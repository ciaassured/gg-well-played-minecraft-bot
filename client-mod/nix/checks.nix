{inputs, ...}: {
  perSystem = {
    pkgs,
    clientArtifacts,
    ...
  }: {
    checks = {
      mod-build = clientArtifacts.clientMod;

      container-entrypoint-smoke = pkgs.runCommand "jump-client-entrypoint-smoke" {} ''
        output=$(JUMP_ENTRYPOINT_VALIDATE=1 POD_NAME=jump-client-117 \
          ${clientArtifacts.containerEntrypoint}/bin/jump-client-container)
        echo "$output" | grep -q 'username=jumpbot-117'
        echo "$output" | grep -q 'bind=0.0.0.0:64123'
        touch "$out"
      '';

      container-readiness-probe =
        pkgs.runCommand "jump-client-readiness-probe" {
          nativeBuildInputs = [pkgs.jq pkgs.gnutar];
        } ''
          mkdir image
          tar -xf ${clientArtifacts.oci} -C image
          config=$(jq -r '.[0].Config' image/manifest.json)
          runtime_path=$(jq -r \
            '.config.Env[] | select(startswith("PATH=")) | sub("^PATH="; "")' \
            "image/$config")
          touch ready
          ${pkgs.coreutils}/bin/env PATH="$runtime_path" test -f ready
          touch "$out"
        '';

      core-tests =
        pkgs.runCommand "jump-client-core-tests" {
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
            ${inputs.protocol}/proto/jump/v1/jump.proto
          find generated \
            src/main/java/gg/wellplayed/jump/client/core \
            src/test/java/gg/wellplayed/jump/client/core \
            -type f -name '*.java' | sort > sources.txt
          javac --release 25 -cp ${clientArtifacts.protobufJava} -d classes @sources.txt
          java -ea -cp classes:${clientArtifacts.protobufJava} \
            gg.wellplayed.jump.client.core.CoreTestMain
          touch "$out"
        '';

      protocol-generation =
        pkgs.runCommand "jump-client-protocol-generation" {
          nativeBuildInputs = [pkgs.jdk25_headless pkgs.protobuf];
        } ''
          mkdir -p generated
          protoc \
            --proto_path=${inputs.protocol}/proto \
            --java_out=generated \
            ${inputs.protocol}/proto/jump/v1/jump.proto
          test -f generated/gg/wellplayed/jump/protocol/v1/Observation.java
          grep -q 'class ActionApplied' \
            generated/gg/wellplayed/jump/protocol/v1/ActionApplied.java
          touch "$out"
        '';

      startup-configuration = pkgs.runCommand "jump-client-startup-configuration" {} ''
        if grep -q 'CLIENT_MODE_' ${../src/main/java/gg/wellplayed/jump/client/JumpBenchmarkClient.java}; then
          echo "unified client still refers to a mode" >&2
          exit 1
        fi
        grep -q 'keySprint.setDown(false)' ${../src/main/java/gg/wellplayed/jump/client/JumpBenchmarkClient.java}
        grep -q 'clientTick - lastHelloAttemptTick >= 20' ${../src/main/java/gg/wellplayed/jump/client/JumpBenchmarkClient.java}
        grep -q 'hmc.offline=true' ${./apps.nix}
        grep -q 'echo "hmc.fileloglevel=INFO"' ${./apps.nix}
        grep -q 'java -Dhmc.fileloglevel=INFO' ${./apps.nix}
        grep -q 'jump.client.offline=true' ${./apps.nix}
        grep -q 'narrator:0' ${./apps.nix}
        grep -q 'maxFps:60' ${./apps.nix}
        grep -q 'inactivityFpsLimit:minimized' ${./apps.nix}
        grep -q 'simulationDistance:5' ${./apps.nix}
        grep -q 'simulationDistance:5' ${./packages.nix}
        grep -q 'getFramerateLimitTracker().onInputReceived()' ${../src/main/java/gg/wellplayed/jump/client/JumpBenchmarkClient.java}
        grep -q 'emitObservationIfActionTickComplete(client)' ${../src/main/java/gg/wellplayed/jump/client/JumpBenchmarkClient.java}
        grep -q 'jump.client.bind' ${./apps.nix}
        grep -q 'jump.client.readinessFile' ${./apps.nix}
        grep -q 'hmc.offline.username' ${./apps.nix}
        grep -q 'JUMP_CLIENT_XMS' ${./apps.nix}
        grep -q 'JUMP_CLIENT_XMX' ${./apps.nix}
        grep -q 'readiness.markReady' ${../src/main/java/gg/wellplayed/jump/client/JumpBenchmarkClient.java}
        grep -q 'scheduleReconnect("Paper disconnected")' ${../src/main/java/gg/wellplayed/jump/client/JumpBenchmarkClient.java}
        grep -q 'connectMinecraft(client);' ${../src/main/java/gg/wellplayed/jump/client/JumpBenchmarkClient.java}
        grep -q 'runtime/client' ${./apps.nix}
        if grep -qi 'replaymod\|episodeartifact\|commandfinalize' ${./apps.nix} \
          ${../src/main/java/gg/wellplayed/jump/client/JumpBenchmarkClient.java}; then
          echo "removed recording integration is still present" >&2
          exit 1
        fi
        if grep -q -- '--mode' ${./apps.nix}; then
          echo "unified launcher still accepts --mode" >&2
          exit 1
        fi
        if grep -q 'chmod -R u+w.*runtime_dir' ${./apps.nix} ${./packages.nix}; then
          echo "client startup attempts to change ownership-managed runtime permissions" >&2
          exit 1
        fi
        grep -q 'Narrator.EMPTY' ${../src/main/java/gg/wellplayed/jump/client/mixin/GameNarratorMixin.java}
        grep -q 'UserApiService.OFFLINE' ${../src/main/java/gg/wellplayed/jump/client/mixin/OfflineProfileKeyPairMixin.java}
        grep -q 'SoundEngine;reload()V' ${../src/main/java/gg/wellplayed/jump/client/mixin/SoundManagerMixin.java}
        touch "$out"
      '';
    };
  };
}
