{...}: {
  perSystem = {pkgs, ...}: let
    validate = pkgs.writeShellApplication {
      name = "yrush-protocol-validate";
      runtimeInputs = [pkgs.buf pkgs.protobuf pkgs.python3];
      text = ''
        root=${../.}
        cd "$root"
        buf lint
        buf format --diff --exit-code
        protoc --proto_path=proto --descriptor_set_out=/dev/null proto/yrush/v1/yrush.proto
        python3 tests/validate_schema.py
      '';
    };
  in {
    apps.validate = {
      type = "app";
      program = "${validate}/bin/yrush-protocol-validate";
      meta.description = "Lint, format-check, compile, and validate the YRush protocol";
    };
  };
}
