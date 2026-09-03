{...}: {
  perSystem = {
    pkgs,
    trainerArtifacts,
    ...
  }: let
    mkCommand = name: subcommand: let
      wrapper = pkgs.writeShellApplication {
        name = "jump-trainer-${name}";
        runtimeInputs = [trainerArtifacts.trainer];
        text = ''
          export JUMP_TRAINER_RUN_ROOT="''${JUMP_TRAINER_RUN_ROOT:-$PWD/trainer/runs}"
          export JUMP_TRAINER_OUTPUT_ROOT="''${JUMP_TRAINER_OUTPUT_ROOT:-$PWD/trainer/evaluations}"
          exec jump-trainer ${subcommand} "$@"
        '';
      };
    in {
      type = "app";
      program = "${wrapper}/bin/jump-trainer-${name}";
      meta.description = "Run the Minecraft jump trainer ${subcommand} command";
    };
    imageCommand = import ./image-app.nix {
      inherit pkgs;
      commandName = "jump-trainer-image";
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
      pipeline = mkCommand "pipeline" "pipeline";
      capacity = mkCommand "capacity" "capacity";
      image = {
        type = "app";
        program = "${imageCommand}/bin/jump-trainer-image";
        meta.description = "Build, load, or publish the trainer OCI image";
      };
    };
  };
}
