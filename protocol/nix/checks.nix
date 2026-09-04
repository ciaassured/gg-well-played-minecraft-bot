{...}: {
  perSystem = {pkgs, ...}: {
    checks.schema =
      pkgs.runCommand "yrush-protocol-check" {
        nativeBuildInputs = [pkgs.buf pkgs.protobuf pkgs.python3];
        src = ../.;
      } ''
        export HOME="$TMPDIR"
        export XDG_CACHE_HOME="$TMPDIR/cache"
        cp -R "$src" source
        chmod -R u+w source
        cd source
        buf lint
        buf format --diff --exit-code
        protoc \
          --proto_path=proto \
          --include_imports \
          --descriptor_set_out=descriptor.binpb \
          proto/yrush/v1/yrush.proto
        python3 tests/validate_schema.py
        touch "$out"
      '';
  };
}
