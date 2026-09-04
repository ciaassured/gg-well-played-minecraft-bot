package gg.wellplayed.yrush.client.core;

import gg.wellplayed.yrush.protocol.v1.ActionRequest;
import gg.wellplayed.yrush.protocol.v1.ArmEpisode;
import gg.wellplayed.yrush.protocol.v1.ErrorCode;
import gg.wellplayed.yrush.protocol.v1.PlayerOutcome;
import gg.wellplayed.yrush.protocol.v1.WireMessage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/** Dependency-light assertions used by Gradle and the Nix flake. */
public final class CoreTestMain {
  private static final int VERSION = RoundSequencer.PROTOCOL_VERSION;
  private static int assertions;

  private CoreTestMain() {}

  public static void main(String[] args) throws Exception {
    framingIsBounded();
    packetsAreStrictlyParsed();
    attachmentDuringAnActiveRoundIsSkipped();
    eliminationAllowsTheNextRoundToBeArmed();
    orderingAndPolicyVersionsAreStrict();
    partialActionsAreNotAcknowledgedAsComplete();
    terminalRaceDiscardsTheInFlightAction();
    voxelsAreCardinalAndEgocentric();
    everyActionDimensionIsMapped();
    controlsReleaseCompletely();
    configurationIdentityAndReadinessAreStable();
    reconnectBackoffIsBounded();
    System.out.println("YRush client core assertions: " + assertions);
  }

  private static void framingIsBounded() throws Exception {
    WireMessage original =
        WireMessage.newBuilder()
            .setProtocolVersion(VERSION)
            .setArmEpisode(arm("session", 1, 1, 0))
            .build();
    ByteArrayOutputStream bytes = new ByteArrayOutputStream();
    FramedProtobuf.write(bytes, original);
    equal(original, FramedProtobuf.read(new ByteArrayInputStream(bytes.toByteArray())));

    ByteArrayOutputStream tooLarge = new ByteArrayOutputStream();
    new DataOutputStream(tooLarge).writeInt(FramedProtobuf.MAX_MESSAGE_BYTES + 1);
    throwsType(
        IOException.class,
        () -> FramedProtobuf.read(new ByteArrayInputStream(tooLarge.toByteArray())));

    ByteArrayOutputStream truncated = new ByteArrayOutputStream();
    DataOutputStream output = new DataOutputStream(truncated);
    output.writeInt(12);
    output.write(new byte[] {1, 2, 3});
    throwsType(
        EOFException.class,
        () -> FramedProtobuf.read(new ByteArrayInputStream(truncated.toByteArray())));
  }

  private static void packetsAreStrictlyParsed() throws Exception {
    YRushPacket active = YRushPacket.parse(activeJson(true));
    equal(YRushPacket.Phase.ACTIVE, active.phase());
    equal(YRushPacket.Direction.DOWN, active.direction());
    equal(39, active.targetY());
    equal(3, active.activePlayers());

    YRushPacket eliminated = YRushPacket.parse(activeJson(false));
    check(!eliminated.playerActive());

    YRushPacket complete = YRushPacket.parse(completeJson("WON"));
    equal(YRushPacket.Result.WIN, complete.result());
    equal(YRushPacket.Outcome.WON, complete.playerOutcome());

    equal(YRushPacket.Phase.INACTIVE, YRushPacket.parse(inactiveJson()).phase());
    throwsCode(
        ErrorCode.ERROR_CODE_VERSION_MISMATCH,
        () ->
            YRushPacket.parse(
                activeJson(true).replace("\"schema_version\":1", "\"schema_version\":2")));
    throwsCode(
        ErrorCode.ERROR_CODE_INVALID_MESSAGE,
        () -> YRushPacket.parse(activeJson(true).replace("\"target_y\":39,", "")));
    throwsCode(
        ErrorCode.ERROR_CODE_INVALID_MESSAGE,
        () -> YRushPacket.parse(activeJson(true).replace("{", "[")));
    throwsCode(
        ErrorCode.ERROR_CODE_INVALID_MESSAGE,
        () -> YRushPacket.parse(activeJson(true).replace("\"target_y\":39", "\"target_y\":39.5")));
  }

  private static void attachmentDuringAnActiveRoundIsSkipped() throws Exception {
    RoundSequencer sequencer = new RoundSequencer();
    sequencer.startSession("session");
    equal(RoundSequencer.Event.NONE, sequencer.receive(YRushPacket.parse(activeJson(true)), 10));
    sequencer.arm(arm("session", 1, 1, 0));
    equal(RoundSequencer.Phase.SKIPPING, sequencer.phase());
    equal(
        RoundSequencer.Event.NONE, sequencer.receive(YRushPacket.parse(completeJson("LOST")), 20));
    sequencer.receive(YRushPacket.parse(inactiveJson()), 21);
    sequencer.receive(YRushPacket.parse(countdownJson()), 22);
    equal(
        RoundSequencer.Event.EPISODE_STARTED,
        sequencer.receive(YRushPacket.parse(activeJson(true)), 23));
    equal(1L, sequencer.activeArm().getRoundSequence());
  }

  private static void eliminationAllowsTheNextRoundToBeArmed() throws Exception {
    RoundSequencer sequencer = startedSequencer();
    equal(
        RoundSequencer.Event.ELIMINATED,
        sequencer.receive(YRushPacket.parse(activeJson(false)), 50));
    equal(PlayerOutcome.PLAYER_OUTCOME_ELIMINATED, sequencer.terminalOutcome());
    sequencer.arm(arm("session", 2, 2, 1));
    equal(RoundSequencer.Phase.SKIPPING, sequencer.phase());
    equal(
        RoundSequencer.Event.NONE,
        sequencer.receive(YRushPacket.parse(completeJson("ELIMINATED")), 60));
    equal(RoundSequencer.Event.CLEANED, sequencer.receive(YRushPacket.parse(inactiveJson()), 61));
    sequencer.receive(YRushPacket.parse(countdownJson()), 62);
    equal(
        RoundSequencer.Event.EPISODE_STARTED,
        sequencer.receive(YRushPacket.parse(activeJson(true)), 63));
    equal(2L, sequencer.activeArm().getRoundSequence());
    equal(1L, sequencer.activeArm().getPolicyVersion());
  }

  private static void orderingAndPolicyVersionsAreStrict() throws Exception {
    RoundSequencer sequencer = startedSequencer();
    ActionRequest first = action("session", 1, 0, 0, 1, List.of(2, 1, 1, 0, 2, 2));
    sequencer.queueAction(first);
    equal(first, sequencer.beginAction());
    sequencer.completeAction(30);
    equal(1L, sequencer.actionSequence());
    throwsCode(
        ErrorCode.ERROR_CODE_STALE_ROUND,
        () -> sequencer.queueAction(action("session", 1, 9, 1, 2, List.of(1, 1, 0, 0, 2, 2))));
    throwsCode(
        ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION,
        () -> sequencer.queueAction(action("session", 1, 0, 0, 3, List.of(1, 1, 0, 0, 2, 2))));
    throwsCode(
        ErrorCode.ERROR_CODE_INVALID_MESSAGE,
        () -> sequencer.queueAction(action("session", 1, 0, 1, 2, List.of(3, 1, 0, 0, 2, 2))));

    RoundSequencer badOrder = startedSequencer();
    throwsCode(
        ErrorCode.ERROR_CODE_ROUND_ORDERING,
        () -> badOrder.receive(YRushPacket.parse(countdownJson()), 99));
  }

  private static void partialActionsAreNotAcknowledgedAsComplete() throws Exception {
    RoundSequencer sequencer = startedSequencer();
    ActionRequest request = action("session", 1, 0, 0, 1, List.of(2, 1, 1, 0, 2, 2));
    sequencer.queueAction(request);
    equal(request, sequencer.beginAction());
    equal(
        RoundSequencer.Event.ELIMINATED,
        sequencer.receive(YRushPacket.parse(activeJson(false)), 14));
    equal(0L, sequencer.actionSequence());
    equal(0L, sequencer.observationSequence());
    throwsType(IllegalStateException.class, () -> sequencer.completeAction(15));
  }

  private static void terminalRaceDiscardsTheInFlightAction() throws Exception {
    RoundSequencer sequencer = startedSequencer();
    equal(
        RoundSequencer.Event.ELIMINATED,
        sequencer.receive(YRushPacket.parse(activeJson(false)), 14));
    sequencer.receive(YRushPacket.parse(completeJson("ELIMINATED")), 15);
    sequencer.receive(YRushPacket.parse(inactiveJson()), 16);
    ActionRequest inFlight = action("session", 1, 0, 0, 1, List.of(2, 1, 1, 0, 2, 2));
    sequencer.queueAction(inFlight);
    equal(RoundSequencer.Phase.IDLE, sequencer.phase());
    check(sequencer.beginAction() == null);
    equal(0L, sequencer.actionSequence());
    throwsCode(
        ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION,
        () -> sequencer.queueAction(action("session", 1, 0, 0, 2, List.of(2, 1, 1, 0, 2, 2))));
    throwsCode(ErrorCode.ERROR_CODE_STALE_ROUND, () -> sequencer.arm(arm("session", 2, 1, 0)));
  }

  private static void voxelsAreCardinalAndEgocentric() {
    VoxelOrientation.Axes south = VoxelOrientation.fromYaw(44.9);
    equal(0, south.forwardX());
    equal(1, south.forwardZ());
    close(44.9, south.yawResidual());
    VoxelOrientation.Axes west = VoxelOrientation.fromYaw(45.1);
    equal(-1, west.forwardX());
    equal(-1, west.rightZ());
    close(-44.9, west.yawResidual());
    VoxelOrientation.Axes angled = VoxelOrientation.fromYaw(30.0);
    close(1.0, angled.forwardVelocity(-0.5, Math.sqrt(3.0) / 2.0));
    close(0.0, angled.strafeVelocity(-0.5, Math.sqrt(3.0) / 2.0));

    byte[] encoded =
        VoxelOrientation.encode(
            10,
            20,
            30,
            0.0,
            (x, y, z) ->
                new VoxelOrientation.BlockProperties(
                    x == 12 && y == 18 && z == 28, y == 20, z == 32, x == 8));
    equal(VoxelOrientation.ENCODED_BYTES, encoded.length);
    // First sample is local right=-2, up=-2, forward=-2 => world (12,18,28).
    equal((byte) 1, encoded[0]);
    equal((byte) 0, encoded[1]);
    // Centre block starts after 62 blocks.
    equal((byte) 1, encoded[62 * 4 + 1]);
  }

  private static void everyActionDimensionIsMapped() throws Exception {
    ActionVector low = ActionVector.fromChoices(List.of(0, 0, 0, 0, 0, 0));
    equal(-1, low.forwardAxis());
    equal(-1, low.strafeAxis());
    check(!low.jump());
    check(!low.attack());
    close(-30.0, low.yawDelta());
    close(-20.0, low.pitchDelta());

    ActionVector high = ActionVector.fromChoices(List.of(2, 2, 1, 1, 4, 4));
    equal(1, high.forwardAxis());
    equal(1, high.strafeAxis());
    check(high.jump());
    check(high.attack());
    close(30.0, high.yawDelta());
    close(20.0, high.pitchDelta());
    throwsType(IllegalArgumentException.class, () -> ActionVector.fromChoices(List.of(1, 1, 0)));
  }

  private static void controlsReleaseCompletely() {
    ControlledInputs inputs = new ControlledInputs();
    inputs.apply(ActionVector.fromChoices(List.of(2, 0, 1, 1, 2, 2)));
    check(inputs.forward());
    check(inputs.left());
    check(inputs.jump());
    check(inputs.attack());
    check(inputs.sprint());
    inputs.releaseAll();
    check(!inputs.forward());
    check(!inputs.backward());
    check(!inputs.left());
    check(!inputs.right());
    check(!inputs.jump());
    check(!inputs.attack());
    check(!inputs.sprint());
  }

  private static void configurationIdentityAndReadinessAreStable() throws Exception {
    ClientConfiguration configuration =
        new ClientConfiguration("0.0.0.0", 64123, "yrush-paper:25565", Path.of("/tmp/ready"));
    equal("yrush-paper:25565", configuration.paperAddress());
    equal("yrushbot-17", ClientIdentity.fromPodName("yrush-client-17"));
    throwsType(IllegalArgumentException.class, () -> ClientIdentity.fromPodName("yrush-client"));

    Path directory = Files.createTempDirectory("yrush-readiness-test");
    Path path = directory.resolve("ready");
    ReadinessFile readiness = new ReadinessFile(path);
    readiness.markReady("protocol=1\n");
    equal("protocol=1\n", Files.readString(path));
    check(!Files.exists(directory.resolve("ready.tmp")));
    readiness.remove();
    check(!Files.exists(path));
    Files.delete(directory);
  }

  private static void reconnectBackoffIsBounded() {
    ReconnectBackoff backoff = new ReconnectBackoff(20, 600);
    equal(20L, backoff.delayTicks(0));
    equal(320L, backoff.delayTicks(4));
    equal(600L, backoff.delayTicks(5));
    equal(600L, backoff.delayTicks(100));
  }

  private static RoundSequencer startedSequencer() throws Exception {
    RoundSequencer sequencer = new RoundSequencer();
    sequencer.startSession("session");
    sequencer.receive(YRushPacket.parse(inactiveJson()), 10);
    sequencer.arm(arm("session", 1, 1, 0));
    sequencer.receive(YRushPacket.parse(countdownJson()), 11);
    equal(
        RoundSequencer.Event.EPISODE_STARTED,
        sequencer.receive(YRushPacket.parse(activeJson(true)), 12));
    return sequencer;
  }

  private static ArmEpisode arm(
      String session, long request, long roundSequence, long policyVersion) {
    return ArmEpisode.newBuilder()
        .setProtocolVersion(VERSION)
        .setSessionId(session)
        .setRequestId(request)
        .setRoundSequence(roundSequence)
        .setPolicyVersion(policyVersion)
        .build();
  }

  private static ActionRequest action(
      String session,
      long roundSequence,
      long policyVersion,
      long observationSequence,
      long actionSequence,
      List<Integer> choices) {
    return ActionRequest.newBuilder()
        .setProtocolVersion(VERSION)
        .setSessionId(session)
        .setRoundSequence(roundSequence)
        .setPolicyVersion(policyVersion)
        .setObservationSequence(observationSequence)
        .setActionSequence(actionSequence)
        .addAllAction(choices)
        .build();
  }

  private static String activeJson(boolean playerActive) {
    return "{\"schema_version\":1,\"round_active\":true,\"player_active\":"
        + playerActive
        + ",\"phase\":\"ACTIVE\",\"direction\":\"DOWN\",\"target_y\":39,"
        + "\"active_players\":3,\"total_players\":5,\"seconds_remaining\":482}";
  }

  private static String countdownJson() {
    return activeJson(true).replace("\"phase\":\"ACTIVE\"", "\"phase\":\"LOCKED_COUNTDOWN\"");
  }

  private static String completeJson(String outcome) {
    return "{\"schema_version\":1,\"round_active\":false,\"player_active\":false,"
        + "\"phase\":\"ROUND_COMPLETE\",\"result\":\"WIN\",\"player_outcome\":\""
        + outcome
        + "\",\"winner_uuid\":\"12345678-1234-5678-9abc-123456789abc\"}";
  }

  private static String inactiveJson() {
    return "{\"schema_version\":1,\"round_active\":false,\"player_active\":false,"
        + "\"phase\":\"INACTIVE\"}";
  }

  private static void check(boolean value) {
    assertions++;
    if (!value) {
      throw new AssertionError("condition was false");
    }
  }

  private static void equal(Object expected, Object actual) {
    assertions++;
    if (!expected.equals(actual)) {
      throw new AssertionError("expected " + expected + ", got " + actual);
    }
  }

  private static void close(double expected, double actual) {
    assertions++;
    if (Math.abs(expected - actual) > 1.0e-6) {
      throw new AssertionError("expected " + expected + ", got " + actual);
    }
  }

  private static void throwsCode(ErrorCode code, ThrowingRunnable runnable) throws Exception {
    assertions++;
    try {
      runnable.run();
      throw new AssertionError("expected protocol violation");
    } catch (ProtocolViolation violation) {
      equal(code, violation.code());
    }
  }

  private static void throwsType(Class<? extends Throwable> type, ThrowingRunnable runnable)
      throws Exception {
    assertions++;
    try {
      runnable.run();
      throw new AssertionError("expected " + type.getSimpleName());
    } catch (Throwable throwable) {
      if (!type.isInstance(throwable)) {
        throw throwable;
      }
    }
  }

  @FunctionalInterface
  private interface ThrowingRunnable {
    void run() throws Exception;
  }
}
