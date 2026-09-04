{...}: {
  perSystem = {pkgs, ...}: let
    schemaBundle = pkgs.stdenvNoCC.mkDerivation {
      pname = "yrush-protocol";
      version = "1.0.0";
      src = ../.;
      nativeBuildInputs = [pkgs.buf pkgs.protobuf];

      buildPhase = ''
        runHook preBuild
        export HOME="$TMPDIR"
        export XDG_CACHE_HOME="$TMPDIR/cache"
        buf build --as-file-descriptor-set -o yrush-v1.binpb
        protoc \
          --proto_path=proto \
          --include_imports \
          --descriptor_set_out=yrush-v1.protoset \
          proto/yrush/v1/yrush.proto
        runHook postBuild
      '';

      installPhase = ''
        runHook preInstall
        mkdir -p "$out/share/yrush-protocol"
        cp -R proto "$out/share/yrush-protocol/"
        cp buf.yaml yrush-v1.binpb yrush-v1.protoset \
          "$out/share/yrush-protocol/"
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
