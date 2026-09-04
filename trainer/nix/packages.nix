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
      pname = "minecraft-yrush-trainer";
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
          ${inputs.protocol}/proto/yrush/v1/yrush.proto
      '';

      doCheck = false;
      pythonImportsCheck = [
        "yrush_trainer"
        "yrush.v1.yrush_pb2"
      ];

      meta.mainProgram = "yrush-trainer";
    };
    oci = pkgs.dockerTools.buildLayeredImage {
      name = "ghcr.io/ciaassured/gg-well-played-minecraft-bot-trainer";
      tag = "unstable";
      maxLayers = 120;
      contents = [trainer pkgs.cacert];
      config = {
        Entrypoint = ["${trainer}/bin/yrush-trainer"];
        WorkingDir = "/artifacts";
        Env = [
          "USER=yrush-trainer"
          "YRUSH_TRAINER_RUN_ROOT=/artifacts/runs"
          "YRUSH_TRAINER_OUTPUT_ROOT=/artifacts/evaluations"
          "YRUSH_POOL_STARTUP_TIMEOUT=900"
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
