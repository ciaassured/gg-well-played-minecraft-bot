package gg.wellplayed.jump.server.core;

import gg.wellplayed.jump.server.core.EpisodeController.ActionStatus;
import gg.wellplayed.jump.server.core.EpisodeController.EndReason;
import gg.wellplayed.jump.server.core.EpisodeController.Kinematics;
import gg.wellplayed.jump.server.core.EpisodeController.Phase;
import gg.wellplayed.jump.server.core.EpisodeController.ResetCommand;
import gg.wellplayed.jump.server.core.EpisodeController.ResetStatus;

/** Dependency-free test runner used by both Gradle and the Nix flake check. */
public final class CoreTestMain {
  private static int assertions;

  private CoreTestMain() {}

  public static void main(String[] args) {
    arenaMeetsSpecification();
    seededGapsAreDeterministicAndUniform();
    resetsAreIdempotentAndSequenced();
    readinessNeedsTwoStableTicks();
    actionSequencesAreStrict();
    waitsForAnActionBeforeAdvancing();
    detectsSuccess();
    rejectsGroundBelowTheLandingFloor();
    detectsMissedJump();
    enforcesTimeLimit();
    abortsOnActionDeadline();
    System.out.printf("server core tests passed (%d assertions)%n", assertions);
  }

  private static void arenaMeetsSpecification() {
    ArenaGeometry arena = ArenaGeometry.STANDARD;
    check(arena.floorMaxX() - arena.floorMinX() + 1 >= 20, "lane length");
    check(arena.laneMaxZ() - arena.laneMinZ() + 1 == 3, "lane width");
    check(arena.landingLength() >= 4.0, "landing length");
    check(arena.floorY() == 300, "terrain-independent sky platform");
    close(301.0, arena.standingFeetY(), "standing height");
    close(9.7, arena.spawnCenterX(4.0), "minimum gap spawn");
    close(5.7, arena.spawnCenterX(8.0), "maximum gap spawn");
    check(arena.landingLength() == 9.0, "nine flat landing blocks");
    check(arena.endBarrierX() == 24, "containment follows the landing floor");
  }

  private static void seededGapsAreDeterministicAndUniform() {
    int[] bins = new int[4];
    for (long seed = 0; seed < 100_000; seed++) {
      double first = SeededGap.fromSeed(seed);
      double second = SeededGap.fromSeed(seed);
      check(first == second, "seed determinism");
      check(first >= 4.0 && first <= 8.0, "gap bounds");
      int bin = Math.min(3, (int) (first - 4.0));
      bins[bin]++;
    }
    for (int count : bins) {
      check(count > 24_000 && count < 26_000, "uniform gap bins");
    }
  }

  private static void resetsAreIdempotentAndSequenced() {
    EpisodeController controller = new EpisodeController();
    ResetCommand first = reset(1, 1, 0);
    check(controller.requestReset(first) == ResetStatus.ACCEPTED, "accept reset");
    check(controller.requestReset(first) == ResetStatus.IDEMPOTENT, "idempotent retry");
    check(
        controller.requestReset(new ResetCommand(1, "other", 1, 0, SeededGap.fromSeed(0)))
            == ResetStatus.REQUEST_MISMATCH,
        "mismatched retry");
    check(controller.requestReset(reset(2, 1, 1)) == ResetStatus.STALE_EPISODE, "stale episode");
    check(controller.requestReset(reset(3, 2, 1)) == ResetStatus.ACCEPTED, "new episode");
    check(controller.requestReset(reset(2, 3, 2)) == ResetStatus.STALE_REQUEST, "stale request");
  }

  private static void readinessNeedsTwoStableTicks() {
    EpisodeController controller = readyController();
    check(controller.phase() == Phase.READY, "ready after two stable ticks");

    controller = new EpisodeController();
    controller.requestReset(reset(1, 1, 0));
    check(!controller.observeResetStability(true, 0), "first stable tick");
    check(!controller.observeResetStability(false, 0), "unstable tick resets count");
    check(!controller.observeResetStability(true, 0), "new first stable tick");
    check(
        !controller.observeResetStability(true, 1.0e-4), "horizontal movement resets stable count");
    check(!controller.observeResetStability(true, 0), "stable count restarts after movement");
    check(controller.observeResetStability(true, 0), "second consecutive stable tick");
  }

  private static void actionSequencesAreStrict() {
    EpisodeController controller = readyController();
    check(controller.acceptAction(0, 1, 10) == ActionStatus.ACCEPTED, "first action");
    check(controller.acceptAction(0, 1, 10) == ActionStatus.STALE_OR_DUPLICATE, "duplicate action");
    check(controller.acceptAction(3, 2, 11) == ActionStatus.OUT_OF_ORDER, "observation gap");
    check(controller.acceptAction(1, 2, 11) == ActionStatus.ACCEPTED, "next action");
  }

  private static void detectsSuccess() {
    EpisodeController controller = activeController();
    var result =
        controller.tick(11, new Kinematics(15.61, 15.01, 0, 0, 0.1, true), ArenaGeometry.STANDARD);
    check(result.finishedNow(), "success finishes");
    check(result.phase() == Phase.TERMINAL, "success terminal");
    check(result.reason() == EndReason.SUCCESS, "success reason");
  }

  private static void rejectsGroundBelowTheLandingFloor() {
    EpisodeController controller = activeController();
    var result =
        controller.tick(
            11, new Kinematics(15.61, 15.01, -364.0, 0, 0.1, true), ArenaGeometry.STANDARD);
    check(!result.finishedNow(), "terrain below the platform cannot count as a landing");
    check(result.phase() == Phase.ACTIVE, "below-platform ground keeps the episode active");
  }

  private static void waitsForAnActionBeforeAdvancing() {
    EpisodeController controller = activeController();
    Kinematics moving = new Kinematics(10.1, 9.5, 0, 0, 0.1, true);
    controller.tick(11, moving, ArenaGeometry.STANDARD);
    check(controller.elapsedTicks() == 1, "accepted action advances exactly one tick");
    controller.tick(12, moving, ArenaGeometry.STANDARD);
    check(controller.elapsedTicks() == 1, "elapsed time waits for the next action");
    check(controller.phase() == Phase.ACTIVE, "waiting for an action remains active");
  }

  private static void detectsMissedJump() {
    EpisodeController controller = activeController();
    Kinematics againstWall = new Kinematics(14.0, 13.4, 0, 0, 0, true);
    controller.tick(11, againstWall, ArenaGeometry.STANDARD);
    for (int tick = 2; tick <= EpisodeController.MISSED_JUMP_TICKS + 1; tick++) {
      check(
          controller.acceptAction(tick - 1, tick, 9 + tick) == ActionStatus.ACCEPTED,
          "sequential stuck action");
      controller.tick(10 + tick, againstWall, ArenaGeometry.STANDARD);
    }
    check(controller.phase() == Phase.TERMINAL, "miss is terminal");
    check(controller.snapshot(false).reason() == EndReason.MISSED_JUMP, "missed-jump reason");
  }

  private static void enforcesTimeLimit() {
    EpisodeController controller = activeController();
    for (int tick = 1; tick <= EpisodeController.TIME_LIMIT_TICKS; tick++) {
      if (tick > 1) {
        check(
            controller.acceptAction(tick - 1, tick, 9 + tick) == ActionStatus.ACCEPTED,
            "sequential timeout action");
      }
      controller.tick(
          10 + tick,
          new Kinematics(10.0 + tick * 0.002, 9.4, 0, 0, 0.002, true),
          ArenaGeometry.STANDARD);
    }
    check(controller.phase() == Phase.TERMINAL, "time limit terminal");
    check(controller.snapshot(false).reason() == EndReason.TIME_LIMIT, "time limit reason");
    check(controller.elapsedTicks() == 200, "time limit tick count");
  }

  private static void abortsOnActionDeadline() {
    EpisodeController controller = activeController();
    controller.tick(11, new Kinematics(10, 9.4, 0, 0, 0, true), ArenaGeometry.STANDARD);
    var result =
        controller.tick(21, new Kinematics(10, 9.4, 0, 0, 0, true), ArenaGeometry.STANDARD);
    check(result.phase() == Phase.ABORTED, "deadline abort phase");
    check(result.reason() == EndReason.INFRASTRUCTURE_ERROR, "deadline abort reason");
  }

  private static EpisodeController readyController() {
    EpisodeController controller = new EpisodeController();
    controller.requestReset(reset(1, 1, 0));
    controller.observeResetStability(true, 0);
    controller.observeResetStability(true, 0);
    return controller;
  }

  private static EpisodeController activeController() {
    EpisodeController controller = readyController();
    check(controller.acceptAction(0, 1, 10) == ActionStatus.ACCEPTED, "activate episode");
    return controller;
  }

  private static ResetCommand reset(long requestId, long episodeId, long seed) {
    return new ResetCommand(requestId, "test-session", episodeId, seed, SeededGap.fromSeed(seed));
  }

  private static void close(double expected, double actual, String message) {
    check(Math.abs(expected - actual) < 1.0e-9, message);
  }

  private static void check(boolean condition, String message) {
    assertions++;
    if (!condition) {
      throw new AssertionError(message);
    }
  }
}
