{inputs, ...}: {
  perSystem = {pkgs, ...}: let
    python = pkgs.python313;
    pythonPackages = python.pkgs;
    runtimePackages = ps:
      with ps; [
        gymnasium
        numpy
        protobuf
        stable-baselines3
        torch
      ];
    pythonEnv = python.withPackages (ps:
      runtimePackages ps
      ++ (with ps; [
        mypy
        pytest
      ]));
    trainer = pythonPackages.buildPythonApplication {
      pname = "minecraft-jump-trainer";
      version = "1.0.0";
      pyproject = true;
      src = ../.;

      nativeBuildInputs = [pkgs.protobuf];
      build-system = [pythonPackages.setuptools];
      dependencies = runtimePackages pythonPackages;

      preBuild = ''
        mkdir -p src
        protoc \
          --proto_path=${inputs.protocol}/proto \
          --python_out=src \
          ${inputs.protocol}/proto/jump/v1/jump.proto
      '';

      doCheck = false;
      pythonImportsCheck = [
        "jump_trainer"
        "jump.v1.jump_pb2"
      ];

      meta.mainProgram = "jump-trainer";
    };
    oci = pkgs.dockerTools.buildLayeredImage {
      name = "ghcr.io/ciaassured/gg-well-played-minecraft-bot-trainer";
      tag = "unstable";
      maxLayers = 120;
      contents = [trainer pkgs.cacert];
      config = {
        Entrypoint = ["${trainer}/bin/jump-trainer"];
        WorkingDir = "/artifacts";
        Env = [
          "JUMP_TRAINER_RUN_ROOT=/artifacts/runs"
          "JUMP_TRAINER_OUTPUT_ROOT=/artifacts/evaluations"
          "JUMP_POOL_STARTUP_TIMEOUT=900"
        ];
      };
    };
  in {
    packages.default = trainer;
    packages.trainer = trainer;
    packages.oci = oci;
    packages.container = oci;

    _module.args.trainerArtifacts = {
      inherit oci python pythonPackages pythonEnv runtimePackages trainer;
    };
  };
}
