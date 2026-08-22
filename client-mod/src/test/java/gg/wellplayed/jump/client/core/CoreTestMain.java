package gg.wellplayed.jump.client.core;

import gg.wellplayed.jump.protocol.v1.Action;
import gg.wellplayed.jump.protocol.v1.ActionRequest;
import gg.wellplayed.jump.protocol.v1.CaptureRequest;
import gg.wellplayed.jump.protocol.v1.ClientMode;
import gg.wellplayed.jump.protocol.v1.ConnectionHello;
import gg.wellplayed.jump.protocol.v1.EpisodePhase;
import gg.wellplayed.jump.protocol.v1.EpisodeReady;
import gg.wellplayed.jump.protocol.v1.EpisodeResult;
import gg.wellplayed.jump.protocol.v1.EpisodeState;
import gg.wellplayed.jump.protocol.v1.ErrorCode;
import gg.wellplayed.jump.protocol.v1.ResetRequest;
import gg.wellplayed.jump.protocol.v1.Shutdown;
import gg.wellplayed.jump.protocol.v1.TerminalReason;
import gg.wellplayed.jump.protocol.v1.WireMessage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/** Dependency-free test runner used by Gradle and the Nix flake checks. */
public final class CoreTestMain {
  private static int assertions;

  private CoreTestMain() {}

  public static void main(String[] args) throws Exception {
    framingRoundTripsAndRejectsBadLengths();
    resetAndActionSequencingIsStrict();
    actionDeadlineAbortsInputs();
    observationsUseCollisionBoxFront();
    replayCaptureFindsOnlyFinalizedFile();
    System.out.println("client core assertions: " + assertions);
  }

  private static void framingRoundTripsAndRejectsBadLengths() throws Exception {
    WireMessage original =
        WireMessage.newBuilder()
            .setProtocolVersion(1)
            .setConnectionHello(
                ConnectionHello.newBuilder()
                    .setProtocolVersion(1)
                    .setSessionId("session")
                    .setMode(ClientMode.CLIENT_MODE_TRAINING))
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

    EpisodeState zero = state(reset, 50, 0, EpisodePhase.EPISODE_PHASE_ACTIVE);
    sequencer.receiveState(zero);
    check(!sequencer.observationDue());
    EpisodeState one = state(reset, 51, 1, EpisodePhase.EPISODE_PHASE_ACTIVE);
    sequencer.receiveState(one);
    check(sequencer.observationDue());
    equal(one, sequencer.completeObservation(24));
    equal(1L, sequencer.observationSequence());
    equal(EpisodeSequencer.Phase.WAITING_ACTION, sequencer.phase());

    ActionRequest second = action(reset, 1, 2, Action.ACTION_NOOP);
    sequencer.queueAction(second);
    sequencer.applyQueuedAction();
    EpisodeResult result =
        EpisodeResult.newBuilder()
            .setProtocolVersion(1)
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
    equal(19, terminal.getElapsedTicks());
    equal(EpisodeSequencer.Phase.TERMINAL, sequencer.phase());
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
    inputs.apply(Action.ACTION_NOOP);
    check(inputs.forward());
    check(!inputs.jump());
    inputs.releaseAll();
    check(!inputs.forward());
    check(!inputs.jump());
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

    ObservationMath.Sample diagonal =
        ObservationMath.sample(
            0.0,
            65.5,
            0.0,
            2.0,
            2.0,
            0.2,
            -0.1,
            0.4,
            false,
            10.0,
            Math.sqrt(0.5),
            Math.sqrt(0.5),
            64.0);
    close(10.0 - 2.0 * Math.sqrt(2.0), diagonal.signedWallDistance());
    close(1.5, diagonal.relativeFeetHeight());
    close(0.6 / Math.sqrt(2.0), diagonal.laneVelocity());
  }

  private static void replayCaptureFindsOnlyFinalizedFile() throws Exception {
    Path directory = Files.createTempDirectory("jump-replay-capture-test");
    try {
      Path oldReplay = directory.resolve("old.mcpr");
      writeReplay(oldReplay);
      ReplayCaptureCoordinator coordinator = new ReplayCaptureCoordinator(directory);
      CaptureRequest request =
          CaptureRequest.newBuilder()
              .setProtocolVersion(1)
              .setRequestId(70)
              .setSessionId("session")
              .setCheckpointId("untrained")
              .setEpisodeId(71)
              .setSeed(ReplayCaptureCoordinator.SHOWCASE_SEED)
              .build();
      equal(ReplayCaptureCoordinator.BeginStatus.ACCEPTED, coordinator.begin(request, "session"));
      equal(ReplayCaptureCoordinator.BeginStatus.IDEMPOTENT, coordinator.begin(request, "session"));
      throwsCode(
          ErrorCode.ERROR_CODE_INVALID_MESSAGE,
          () -> coordinator.begin(request.toBuilder().setCheckpointId("other").build(), "session"));

      coordinator.beginFinalization(
          Shutdown.newBuilder()
              .setProtocolVersion(1)
              .setRequestId(72)
              .setSessionId("session")
              .setEpisodeId(71)
              .setReason("checkpoint showcase complete")
              .setDisconnectMinecraft(true)
              .setReconnectMinecraft(true)
              .build());
      Files.writeString(directory.resolve("broken.mcpr"), "not a replay", StandardCharsets.UTF_8);
      Path captured = directory.resolve("captured.mcpr");
      writeReplay(captured);
      check(coordinator.pollFinalized().isEmpty());
      ReplayCaptureCoordinator.Artifact artifact = coordinator.pollFinalized().orElseThrow();
      equal(captured.toAbsolutePath().normalize(), artifact.replayFile());
      equal("untrained", artifact.checkpointId());
      equal(71L, artifact.episodeId());
      equal(Files.size(captured), artifact.sizeBytes());
      equal(32, artifact.sha256().length);
      check(artifact.reconnectMinecraft());
      check(coordinator.finalizing());
      coordinator.complete();
      check(!coordinator.finalizing());
      check(!ReplayModStatus.startupReady());
      check(!ReplayModStatus.recording());
    } finally {
      try (var paths = Files.walk(directory)) {
        paths.sorted(Comparator.reverseOrder()).forEach(CoreTestMain::deleteUnchecked);
      }
    }
  }

  private static void writeReplay(Path path) throws IOException {
    try (ZipOutputStream output = new ZipOutputStream(Files.newOutputStream(path))) {
      output.putNextEntry(new ZipEntry("metaData.json"));
      output.write("{}".getBytes(StandardCharsets.UTF_8));
      output.closeEntry();
      output.putNextEntry(new ZipEntry("recording.tmcpr"));
      output.write(new byte[] {1, 2, 3});
      output.closeEntry();
    }
  }

  private static void deleteUnchecked(Path path) {
    try {
      Files.deleteIfExists(path);
    } catch (IOException exception) {
      throw new AssertionError("cannot delete test path " + path, exception);
    }
  }

  private static ResetRequest reset(long requestId, long episodeId, long seed) {
    return ResetRequest.newBuilder()
        .setProtocolVersion(1)
        .setRequestId(requestId)
        .setSessionId("session")
        .setEpisodeId(episodeId)
        .setSeed(seed)
        .setClientTick(10)
        .build();
  }

  private static EpisodeReady ready(ResetRequest reset, double gap) {
    return EpisodeReady.newBuilder()
        .setProtocolVersion(1)
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
        .setProtocolVersion(1)
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
        .setProtocolVersion(1)
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
