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
        ${inputs.protocol}/proto/jump/v1/jump.proto
      export PYTHONPATH="$PWD/src:$PWD/generated"
    '';
  in {
    checks = {
      package-build = trainerArtifacts.trainer;

      container-entrypoint-smoke = pkgs.runCommand "jump-trainer-entrypoint-smoke" {} ''
        ${trainerArtifacts.trainer}/bin/jump-trainer --help > help.txt
        grep -q pipeline help.txt
        grep -q capacity help.txt
        touch "$out"
      '';

      tests =
        pkgs.runCommand "minecraft-jump-trainer-tests" {
          nativeBuildInputs = [pkgs.protobuf trainerArtifacts.pythonEnv];
        } ''
          ${prepareSource}
          pytest -q tests
          touch "$out"
        '';

      gymnasium-validation =
        pkgs.runCommand "minecraft-jump-gymnasium-validation" {
          nativeBuildInputs = [pkgs.protobuf trainerArtifacts.pythonEnv];
        } ''
          ${prepareSource}
          pytest -q tests/test_env.py::test_gymnasium_checker_and_terminal_observation
          touch "$out"
        '';

      deterministic-mock-training =
        pkgs.runCommand "minecraft-jump-mock-training" {
          nativeBuildInputs = [pkgs.protobuf trainerArtifacts.pythonEnv];
        } ''
          ${prepareSource}
          pytest -q tests/test_mock_training.py
          touch "$out"
        '';

      typing =
        pkgs.runCommand "minecraft-jump-trainer-typing" {
          nativeBuildInputs = [pkgs.protobuf trainerArtifacts.pythonEnv];
        } ''
          ${prepareSource}
          mypy src/jump_trainer
          touch "$out"
        '';

      lint =
        pkgs.runCommand "minecraft-jump-trainer-lint" {
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
        pkgs.runCommand "minecraft-jump-python-protocol" {
          nativeBuildInputs = [pkgs.protobuf trainerArtifacts.pythonEnv];
        } ''
          mkdir -p generated
          protoc \
            --proto_path=${inputs.protocol}/proto \
            --python_out=generated \
            ${inputs.protocol}/proto/jump/v1/jump.proto
          test -f generated/jump/v1/jump_pb2.py
          PYTHONPATH=generated python -c \
            'from jump.v1 import jump_pb2 as p; assert p.WireMessage().protocol_version == 0'
          touch "$out"
        '';
    };
  };
}
