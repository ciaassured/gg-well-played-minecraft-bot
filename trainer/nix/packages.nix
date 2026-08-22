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
  in {
    packages.default = trainer;
    packages.trainer = trainer;

    _module.args.trainerArtifacts = {
      inherit python pythonPackages pythonEnv runtimePackages trainer;
    };
  };
}
