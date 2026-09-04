{...}: {
  perSystem = {
    pkgs,
    trainerArtifacts,
    ...
  }: let
    mkCommand = name: subcommand: let
      wrapper = pkgs.writeShellApplication {
        name = "yrush-trainer-${name}";
        runtimeInputs = [trainerArtifacts.trainer];
        text = ''
          export YRUSH_TRAINER_RUN_ROOT="''${YRUSH_TRAINER_RUN_ROOT:-$PWD/trainer/runs}"
          export YRUSH_TRAINER_OUTPUT_ROOT="''${YRUSH_TRAINER_OUTPUT_ROOT:-$PWD/trainer/evaluations}"
          exec yrush-trainer ${subcommand} "$@"
        '';
      };
    in {
      type = "app";
      program = "${wrapper}/bin/yrush-trainer-${name}";
      meta.description = "Run the YRush trainer ${subcommand} command";
    };
    imageCommand = import ./image-app.nix {
      inherit pkgs;
      commandName = "yrush-trainer-image";
      component = "trainer";
      imageArchive = trainerArtifacts.oci;
      imageName = "ghcr.io/ciaassured/gg-well-played-minecraft-bot-trainer";
    };
  in {
    apps = {
      default = mkCommand "train" "train";
      train = mkCommand "train" "train";
      evaluate = mkCommand "evaluate" "evaluate";
      run = mkCommand "run" "run";
      smoke = mkCommand "smoke" "smoke";
      canary = mkCommand "canary" "canary";
      tuning-canary = mkCommand "tuning-canary" "tuning-canary";
      proof = mkCommand "proof" "proof";
      image = {
        type = "app";
        program = "${imageCommand}/bin/yrush-trainer-image";
        meta.description = "Build, load, or publish the trainer OCI image";
      };
    };
  };
}
