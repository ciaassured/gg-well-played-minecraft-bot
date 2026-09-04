{...}: {
  perSystem = {
    pkgs,
    serverArtifacts,
    ...
  }: let
    launcher = pkgs.writeShellApplication {
      name = "yrush-server";
      runtimeInputs = [serverArtifacts.containerEntrypoint];
      text = ''
        export YRUSH_SERVER_RUNTIME="''${YRUSH_SERVER_RUNTIME:-$PWD/yrush-server/runtime}"
        exec yrush-server-container "$@"
      '';
    };
    imageCommand = import ./image-app.nix {
      inherit pkgs;
      commandName = "yrush-server-image";
      component = "server";
      imageArchive = serverArtifacts.oci;
      imageName = "ghcr.io/ciaassured/gg-well-played-minecraft-bot-server";
    };
  in {
    apps.server = {
      type = "app";
      program = "${launcher}/bin/yrush-server";
      meta.description = "Start the single Paper 26.2 YRush server";
    };
    apps.default = {
      type = "app";
      program = "${launcher}/bin/yrush-server";
      meta.description = "Start the single Paper 26.2 YRush server";
    };
    apps.image = {
      type = "app";
      program = "${imageCommand}/bin/yrush-server-image";
      meta.description = "Build, load, or publish the YRush server OCI image";
    };
  };
}
