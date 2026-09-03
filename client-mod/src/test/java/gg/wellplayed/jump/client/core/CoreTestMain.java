package gg.wellplayed.jump.client.core;

import gg.wellplayed.jump.protocol.v1.Action;
import gg.wellplayed.jump.protocol.v1.ActionRequest;
import gg.wellplayed.jump.protocol.v1.ConnectionHello;
import gg.wellplayed.jump.protocol.v1.EpisodePhase;
import gg.wellplayed.jump.protocol.v1.EpisodeReady;
import gg.wellplayed.jump.protocol.v1.EpisodeResult;
import gg.wellplayed.jump.protocol.v1.EpisodeState;
import gg.wellplayed.jump.protocol.v1.ErrorCode;
import gg.wellplayed.jump.protocol.v1.ResetRequest;
import gg.wellplayed.jump.protocol.v1.TerminalReason;
import gg.wellplayed.jump.protocol.v1.WireMessage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/** Dependency-free test runner used by Gradle and the Nix flake checks. */
public final class CoreTestMain {
  private static final int PROTOCOL_VERSION = 3;
  private static int assertions;

  private CoreTestMain() {}

  public static void main(String[] args) throws Exception {
    framingRoundTripsAndRejectsBadLengths();
    resetAndActionSequencingIsStrict();
    abortAcknowledgementsAreIdempotent();
    actionDeadlineAbortsInputs();
    observationsUseCollisionBoxFront();
    configurationAndIdentityAreStable();
    readinessLifecycleIsAtomic();
    reconnectBackoffIsBounded();
    System.out.println("client core assertions: " + assertions);
  }

  private static void framingRoundTripsAndRejectsBadLengths() throws Exception {
    WireMessage original =
        WireMessage.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setConnectionHello(
                ConnectionHello.newBuilder()
                    .setProtocolVersion(PROTOCOL_VERSION)
                    .setSessionId("session"))
            .build();
    ByteArrayOutputStream bytes = new ByteArrayOutputStream();
    FramedProtobuf.write(bytes, original);
    equal(original, FramedProtobuf.read(new ByteArrayInputStream(bytes.toByteArray())));

    ByteArrayOutputStream tooLarge = new ByteArrayOutputStream();
    new DataOutputStream(tooLarge).writeInt(FramedProtobuf.MAX_MESSAGE_BYTES + 1);
    throwsIo(() -> FramedProtobuf.read(new ByteArrayInputStream(tooLarge.toByteArray())));

    ByteArrayOutputStream truncated = new ByteArrayOutputStream();
    DataOutputStream output = new DataOutputStream(truncated);
    output.writeInt(12);
    output.write(new byte[] {1, 2, 3});
    throwsType(
        EOFException.class,
        () -> FramedProtobuf.read(new ByteArrayInputStream(truncated.toByteArray())));
  }

  private static void resetAndActionSequencingIsStrict() throws Exception {
    EpisodeSequencer sequencer = new EpisodeSequencer();
    sequencer.startSession("session");
    ResetRequest reset = reset(4, 8, 100000);
    equal(EpisodeSequencer.ResetStatus.ACCEPTED, sequencer.beginReset(reset));
    equal(EpisodeSequencer.ResetStatus.IDEMPOTENT, sequencer.beginReset(reset));
    throwsCode(
        ErrorCode.ERROR_CODE_STALE_REQUEST,
        () -> sequencer.beginReset(reset.toBuilder().setSeed(1).build()));

    EpisodeReady ready = ready(reset, 6.25);
    sequencer.receiveReady(ready);
    check(!sequencer.observeClientStability(true, 20));
    check(!sequencer.observeClientStability(false, 21));
    check(!sequencer.observeClientStability(true, 22));
    check(sequencer.observeClientStability(true, 23));
    equal(EpisodeSequencer.Phase.WAITING_ACTION, sequencer.phase());

    throwsCode(
        ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION,
        () -> sequencer.queueAction(action(reset, 1, 2, Action.ACTION_NOOP)));
    ActionRequest action = action(reset, 0, 1, Action.ACTION_JUMP);
    sequencer.queueAction(action);
    equal(action, sequencer.applyQueuedAction());
    check(sequencer.applyQueuedAction() == null);

    sequencer.receiveState(state(reset, 50, 0, EpisodePhase.EPISODE_PHASE_ACTIVE));
    check(!sequencer.observationDue());
    EpisodeState one = state(reset, 51, 1, EpisodePhase.EPISODE_PHASE_ACTIVE);
    sequencer.receiveState(one);
    check(sequencer.observationDue());
    equal(one, sequencer.completeObservation(24));

    sequencer.queueAction(action(reset, 1, 2, Action.ACTION_NOOP));
    sequencer.applyQueuedAction();
    EpisodeResult result =
        EpisodeResult.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId("session")
            .setEpisodeId(reset.getEpisodeId())
            .setServerTick(75)
            .setElapsedTicks(19)
            .setTerminalReason(TerminalReason.TERMINAL_REASON_SUCCESS)
            .build();
    sequencer.receiveResult(result);
    EpisodeState terminal = sequencer.completeObservation(25);
    equal(EpisodePhase.EPISODE_PHASE_TERMINAL, terminal.getPhase());
    equal(TerminalReason.TERMINAL_REASON_SUCCESS, terminal.getTerminalReason());
  }

  private static void actionDeadlineAbortsInputs() throws Exception {
    EpisodeSequencer sequencer = new EpisodeSequencer();
    sequencer.startSession("session");
    ResetRequest reset = reset(1, 1, 0);
    sequencer.beginReset(reset);
    sequencer.receiveReady(ready(reset, 4.0));
    sequencer.observeClientStability(true, 10);
    sequencer.observeClientStability(true, 11);
    check(!sequencer.actionTimedOut(21));
    check(sequencer.actionTimedOut(22));

    ControlledInputs inputs = new ControlledInputs();
    inputs.apply(Action.ACTION_JUMP);
    check(inputs.forward());
    check(inputs.jump());
    inputs.finishTick();
    check(inputs.forward());
    check(!inputs.jump());
    inputs.releaseAll();
    check(!inputs.forward());
    check(!inputs.jump());
  }

  private static void abortAcknowledgementsAreIdempotent() throws Exception {
    EpisodeSequencer sequencer = new EpisodeSequencer();
    sequencer.startSession("session");
    ResetRequest reset = reset(1, 1, 0);
    sequencer.beginReset(reset);
    sequencer.receiveReady(ready(reset, 4.0));
    sequencer.abort();

    EpisodeState aborted =
        state(reset, 51, 0, EpisodePhase.EPISODE_PHASE_ABORTED).toBuilder()
            .setTerminalReason(TerminalReason.TERMINAL_REASON_INFRASTRUCTURE_ERROR)
            .build();
    EpisodeResult result =
        EpisodeResult.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId(reset.getSessionId())
            .setEpisodeId(reset.getEpisodeId())
            .setServerTick(51)
            .setTerminalReason(TerminalReason.TERMINAL_REASON_INFRASTRUCTURE_ERROR)
            .build();
    sequencer.receiveState(aborted);
    sequencer.receiveResult(result);
    equal(EpisodeSequencer.Phase.ABORTED, sequencer.phase());
  }

  private static void observationsUseCollisionBoxFront() {
    ObservationMath.Sample sample =
        ObservationMath.sample(
            7.7, 64.0, 0.2, 8.3, 0.8, 0.1, 0.42, -0.03, true, 14.0, 1.0, 0.0, 64.0);
    close(5.7, sample.signedWallDistance());
    close(0.0, sample.relativeFeetHeight());
    close(0.42, sample.verticalVelocity());
    close(0.1, sample.laneVelocity());
    check(sample.onGround());
    check(ObservationMath.resetStateMatches(sample, 5.7, 0.0));
    check(!ObservationMath.resetStateMatches(sample, 5.7, 1.0e-4));
  }

  private static void configurationAndIdentityAreStable() throws Exception {
    ClientConfiguration configuration =
        new ClientConfiguration("0.0.0.0", 64123, "jump-paper:25565", Path.of("/tmp/ready"));
    equal("0.0.0.0", configuration.trainerBindAddress());
    equal("jump-paper:25565", configuration.paperAddress());
    equal("jumpbot-17", ClientIdentity.fromPodName("jump-client-17"));
    throwsType(IllegalArgumentException.class, () -> ClientIdentity.fromPodName("jump-client"));
    throwsType(
        IllegalArgumentException.class,
        () -> new ClientConfiguration("", 64123, "paper:25565", Path.of("ready")));
  }

  private static void readinessLifecycleIsAtomic() throws Exception {
    Path directory = Files.createTempDirectory("jump-readiness-test");
    Path path = directory.resolve("ready");
    ReadinessFile readiness = new ReadinessFile(path);
    readiness.remove();
    check(!Files.exists(path));
    readiness.markReady("protocol=3\n");
    equal("protocol=3\n", Files.readString(path));
    check(!Files.exists(directory.resolve("ready.tmp")));
    readiness.remove();
    check(!Files.exists(path));
    Files.delete(directory);
  }

  private static void reconnectBackoffIsBounded() throws Exception {
    ReconnectBackoff backoff = new ReconnectBackoff(20, 600);
    equal(20L, backoff.delayTicks(0));
    equal(40L, backoff.delayTicks(1));
    equal(320L, backoff.delayTicks(4));
    equal(600L, backoff.delayTicks(5));
    equal(600L, backoff.delayTicks(100));
    throwsType(IllegalArgumentException.class, () -> backoff.delayTicks(-1));
  }

  private static ResetRequest reset(long requestId, long episodeId, long seed) {
    return ResetRequest.newBuilder()
        .setProtocolVersion(PROTOCOL_VERSION)
        .setRequestId(requestId)
        .setSessionId("session")
        .setEpisodeId(episodeId)
        .setSeed(seed)
        .setClientTick(10)
        .build();
  }

  private static EpisodeReady ready(ResetRequest reset, double gap) {
    return EpisodeReady.newBuilder()
        .setProtocolVersion(PROTOCOL_VERSION)
        .setRequestId(reset.getRequestId())
        .setSessionId(reset.getSessionId())
        .setEpisodeId(reset.getEpisodeId())
        .setSeed(reset.getSeed())
        .setStartingGap(gap)
        .setWallNearCoordinate(14.0)
        .setWallFarCoordinate(15.0)
        .setWallMinCrossCoordinate(-1.0)
        .setWallMaxCrossCoordinate(2.0)
        .setLaneDirectionX(1.0)
        .setLaneDirectionZ(0.0)
        .setStandingFeetY(64.0)
        .setInitialServerTick(40)
        .build();
  }

  private static ActionRequest action(
      ResetRequest reset, long observationSequence, long actionSequence, Action action) {
    return ActionRequest.newBuilder()
        .setProtocolVersion(PROTOCOL_VERSION)
        .setSessionId(reset.getSessionId())
        .setEpisodeId(reset.getEpisodeId())
        .setObservationSequence(observationSequence)
        .setActionSequence(actionSequence)
        .setAction(action)
        .build();
  }

  private static EpisodeState state(
      ResetRequest reset, long serverTick, int elapsedTicks, EpisodePhase phase) {
    return EpisodeState.newBuilder()
        .setProtocolVersion(PROTOCOL_VERSION)
        .setSessionId(reset.getSessionId())
        .setEpisodeId(reset.getEpisodeId())
        .setServerTick(serverTick)
        .setElapsedTicks(elapsedTicks)
        .setPhase(phase)
        .build();
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
    if (Math.abs(expected - actual) > 1.0e-9) {
      throw new AssertionError("expected " + expected + ", got " + actual);
    }
  }

  private static void throwsIo(ThrowingRunnable runnable) throws Exception {
    throwsType(IOException.class, runnable);
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
