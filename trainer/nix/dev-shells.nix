{inputs, ...}: {
  perSystem = {
    pkgs,
    trainerArtifacts,
    ...
  }: {
    devShells.default = pkgs.mkShellNoCC {
      packages = [
        pkgs.alejandra
        pkgs.git
        pkgs.protobuf
        pkgs.ruff
        pkgs.taplo
        trainerArtifacts.pythonEnv
      ];
      PROTOCOL_DIR = inputs.protocol;
      shellHook = ''
        if [[ -f "$PWD/trainer/pyproject.toml" ]]; then
          trainer_root="$PWD/trainer"
        else
          trainer_root="$PWD"
        fi
        generated="$trainer_root/.generated"
        mkdir -p "$generated"
        protoc \
          --proto_path="$PROTOCOL_DIR/proto" \
          --python_out="$generated" \
          "$PROTOCOL_DIR/proto/yrush/v1/yrush.proto"
        export PYTHONPATH="$trainer_root/src:$generated''${PYTHONPATH:+:$PYTHONPATH}"
        echo "YRush trainer shell (Python 3.13, Gymnasium 1.3.0, SB3 PPO 2.9.0)"
      '';
    };
  };
}
