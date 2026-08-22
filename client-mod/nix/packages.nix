{inputs, ...}: {
  perSystem = {pkgs, ...}: let
    fabricApi = pkgs.fetchurl {
      url = "https://maven.fabricmc.net/net/fabricmc/fabric-api/fabric-api/0.155.2+26.2/fabric-api-0.155.2+26.2.jar";
      hash = "sha256-1lGMdwAky+ilViSPFvzbuRxqYvUCJ6bDuugZBRHiwbg=";
    };
    replayMod = pkgs.fetchurl {
      url = "https://www.replaymod.com/download/download_new.php?version=26.2-2.6.27";
      hash = "sha256-aKGLtyXqZcumIXpqk6/YVRuWnDbIeNRf6/5FTSRYaZk=";
      name = "replaymod-26.2-2.6.27.jar";
    };
    headlessMc = pkgs.fetchurl {
      url = "https://github.com/3arthqu4ke/HeadlessMc/releases/download/2.10.0/headlessmc-launcher-wrapper-2.10.0.jar";
      hash = "sha256-v4DYRRbu65pR+jWJTERmsUbVXQFGzW0q3En90jFlRTY=";
    };
    protobufJava = pkgs.fetchurl {
      url = "https://repo.maven.apache.org/maven2/com/google/protobuf/protobuf-java/4.35.1/protobuf-java-4.35.1.jar";
      hash = "sha256-pDRboqoAmRL/b5BGf+otEEYFJWtyxQhA118TJWY4pHI=";
    };
    clientMod = pkgs.stdenvNoCC.mkDerivation (finalAttrs: {
      pname = "jump-benchmark-client-mod";
      version = "1.0.0";
      src = ../.;

      nativeBuildInputs = [pkgs.gradle_9 pkgs.jdk25_headless pkgs.protobuf];
      gradleFlags = ["-PprotocolDir=${inputs.protocol}"];
      gradleBuildTask = "assemble";
      gradleUpdateTask = "assemble";

      mitmCache = pkgs.gradle_9.fetchDeps {
        pkg = finalAttrs.finalPackage;
        data = ../deps.json;
      };

      __darwinAllowLocalNetworking = true;

      installPhase = ''
        runHook preInstall
        mkdir -p "$out/share/jump-benchmark-client"
        cp build/libs/jump-benchmark-client-1.0.0.jar \
          "$out/share/jump-benchmark-client/jump-benchmark-client.jar"
        runHook postInstall
      '';
    });
  in {
    packages = {
      default = clientMod;
      mod = clientMod;
      fabric-api = fabricApi;
      replay-mod = replayMod;
      headlessmc = headlessMc;
    };

    _module.args.clientArtifacts = {
      inherit clientMod fabricApi replayMod headlessMc protobufJava;
    };
  };
}
