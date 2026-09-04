{inputs, ...}: {
  perSystem = {
    pkgs,
    trainerArtifacts,
    ...
  }: let
    prepareSource = ''
      cp -R ${../.} source
      chmod -R u+w source
      cd source
      mkdir -p generated
      protoc \
        --proto_path=${inputs.protocol}/proto \
        --python_out=generated \
        ${inputs.protocol}/proto/yrush/v1/yrush.proto
      export PYTHONPATH="$PWD/src:$PWD/generated"
    '';
  in {
    checks = {
      package-build = trainerArtifacts.trainer;

      container-entrypoint-smoke = pkgs.runCommand "yrush-trainer-entrypoint-smoke" {} ''
        ${trainerArtifacts.trainer}/bin/yrush-trainer --help > help.txt
        grep -q canary help.txt
        grep -q tuning-canary help.txt
        grep -q proof help.txt
        touch "$out"
      '';

      tests =
        pkgs.runCommand "minecraft-yrush-trainer-tests" {
          nativeBuildInputs = [pkgs.protobuf trainerArtifacts.pythonEnv];
        } ''
          ${prepareSource}
          pytest -q tests
          touch "$out"
        '';

      gymnasium-validation =
        pkgs.runCommand "minecraft-yrush-gymnasium-validation" {
          nativeBuildInputs = [pkgs.protobuf trainerArtifacts.pythonEnv];
        } ''
          ${prepareSource}
          pytest -q tests/test_env.py::test_exact_spaces_and_gymnasium_contract
          touch "$out"
        '';

      ppo-update =
        pkgs.runCommand "minecraft-yrush-ppo-update" {
          nativeBuildInputs = [pkgs.protobuf trainerArtifacts.pythonEnv];
        } ''
          ${prepareSource}
          pytest -q tests/test_policy.py
          touch "$out"
        '';

      typing =
        pkgs.runCommand "minecraft-yrush-trainer-typing" {
          nativeBuildInputs = [pkgs.protobuf trainerArtifacts.pythonEnv];
        } ''
          ${prepareSource}
          mypy src/yrush_trainer
          touch "$out"
        '';

      lint =
        pkgs.runCommand "minecraft-yrush-trainer-lint" {
          nativeBuildInputs = [pkgs.ruff];
        } ''
          cp -R ${../.} source
          chmod -R u+w source
          cd source
          ruff check src tests
          ruff format --check src tests
          touch "$out"
        '';

      protocol-generation =
        pkgs.runCommand "minecraft-yrush-python-protocol" {
          nativeBuildInputs = [pkgs.protobuf trainerArtifacts.pythonEnv];
        } ''
          mkdir -p generated
          protoc \
            --proto_path=${inputs.protocol}/proto \
            --python_out=generated \
            ${inputs.protocol}/proto/yrush/v1/yrush.proto
          test -f generated/yrush/v1/yrush_pb2.py
          PYTHONPATH=generated python -c \
            'from yrush.v1 import yrush_pb2 as p; assert p.WireMessage().protocol_version == 0'
          touch "$out"
        '';
    };
  };
}
