{...}: {
  perSystem = {pkgs, ...}: let
    schemaBundle = pkgs.stdenvNoCC.mkDerivation {
      pname = "jump-benchmark-protocol";
      version = "1.0.0";
      src = ../.;
      nativeBuildInputs = [pkgs.buf pkgs.protobuf];

      buildPhase = ''
        runHook preBuild
        export HOME="$TMPDIR"
        export XDG_CACHE_HOME="$TMPDIR/cache"
        buf build --as-file-descriptor-set -o jump-benchmark-v1.binpb
        protoc \
          --proto_path=proto \
          --include_imports \
          --descriptor_set_out=jump-benchmark-v1.protoset \
          proto/jump/v1/jump.proto
        runHook postBuild
      '';

      installPhase = ''
        runHook preInstall
        mkdir -p "$out/share/jump-benchmark-protocol"
        cp -R proto "$out/share/jump-benchmark-protocol/"
        cp buf.yaml jump-benchmark-v1.binpb jump-benchmark-v1.protoset \
          "$out/share/jump-benchmark-protocol/"
        runHook postInstall
      '';
    };
  in {
    packages = {
      default = schemaBundle;
      schema = schemaBundle;
    };
  };
}
