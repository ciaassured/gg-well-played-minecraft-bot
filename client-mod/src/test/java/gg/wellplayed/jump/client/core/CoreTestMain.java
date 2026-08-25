package gg.wellplayed.jump.client.core;

import com.google.protobuf.ByteString;
import gg.wellplayed.jump.protocol.v1.Action;
import gg.wellplayed.jump.protocol.v1.ActionRequest;
import gg.wellplayed.jump.protocol.v1.CommandFinalize;
import gg.wellplayed.jump.protocol.v1.ConnectionHello;
import gg.wellplayed.jump.protocol.v1.EpisodePhase;
import gg.wellplayed.jump.protocol.v1.EpisodeReady;
import gg.wellplayed.jump.protocol.v1.EpisodeRecordingStatus;
import gg.wellplayed.jump.protocol.v1.EpisodeResult;
import gg.wellplayed.jump.protocol.v1.EpisodeState;
import gg.wellplayed.jump.protocol.v1.ErrorCode;
import gg.wellplayed.jump.protocol.v1.ResetRequest;
import gg.wellplayed.jump.protocol.v1.RetentionAcknowledgement;
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
import java.util.List;
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
    episodeMarkersAndMultiFileFinalizationAreStrict();
    System.out.println("client core assertions: " + assertions);
  }

  private static void framingRoundTripsAndRejectsBadLengths() throws Exception {
    WireMessage original =
        WireMessage.newBuilder()
            .setProtocolVersion(2)
            .setConnectionHello(
                ConnectionHello.newBuilder().setProtocolVersion(2).setSessionId("session"))
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
            .setProtocolVersion(2)
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

  private static void episodeMarkersAndMultiFileFinalizationAreStrict() throws Exception {
    Path directory = Files.createTempDirectory("jump-episode-recording-test");
    try {
      Path oldReplay = directory.resolve("old.mcpr");
      writeReplay(oldReplay, "OLD_EPISODE");
      FakeMarkers markers = new FakeMarkers();
      EpisodeRecordingCoordinator coordinator = new EpisodeRecordingCoordinator(directory);
      coordinator.beginSession("session", markers);

      ResetRequest success = reset(70, 71, 100_000);
      equal(
          EpisodeRecordingCoordinator.BeginStatus.ACCEPTED,
          coordinator.beginEpisode(success, markers));
      equal(
          EpisodeRecordingCoordinator.BeginStatus.IDEMPOTENT,
          coordinator.beginEpisode(success, markers));
      coordinator.completeEpisode(
          success.getEpisodeId(), TerminalReason.TERMINAL_REASON_SUCCESS, markers);

      ResetRequest failed = reset(72, 73, 100_001);
      coordinator.beginEpisode(failed, markers);
      coordinator.completeEpisode(
          failed.getEpisodeId(), TerminalReason.TERMINAL_REASON_MISSED_JUMP, markers);
      ResetRequest timedOut = reset(74, 75, 100_002);
      coordinator.beginEpisode(timedOut, markers);
      coordinator.completeEpisode(
          timedOut.getEpisodeId(), TerminalReason.TERMINAL_REASON_TIME_LIMIT, markers);

      ResetRequest overlapped = reset(76, 77, 100_003);
      ResetRequest interrupted = reset(78, 79, 100_004);
      coordinator.beginEpisode(overlapped, markers);
      coordinator.beginEpisode(interrupted, markers);
      coordinator.interruptActive(markers);

      List<EpisodeRecordingCoordinator.Episode> episodes = coordinator.episodeSnapshot();
      equal(5, episodes.size());
      equal(
          EpisodeRecordingStatus.EPISODE_RECORDING_STATUS_COMPLETE,
          episodes.get(0).recordingStatus());
      equal(TerminalReason.TERMINAL_REASON_SUCCESS, episodes.get(0).terminalReason());
      equal(TerminalReason.TERMINAL_REASON_MISSED_JUMP, episodes.get(1).terminalReason());
      equal(TerminalReason.TERMINAL_REASON_TIME_LIMIT, episodes.get(2).terminalReason());
      equal(
          EpisodeRecordingStatus.EPISODE_RECORDING_STATUS_PARTIAL,
          episodes.get(3).recordingStatus());
      equal(TerminalReason.TERMINAL_REASON_INFRASTRUCTURE_ERROR, episodes.get(4).terminalReason());
      equal(EpisodeRecordingCoordinator.START_CUT_MARKER + "@0", markers.written.getFirst());
      check(markers.written.stream().filter(value -> value.startsWith("_RM_SPLIT@")).count() == 5);

      coordinator.beginFinalization(
          CommandFinalize.newBuilder()
              .setProtocolVersion(2)
              .setRequestId(80)
              .setSessionId("session")
              .setReason("command complete")
              .setTransferTimeoutSeconds(300)
              .build(),
          markers);
      equal(300_000L, coordinator.transferTimeoutMillis(400_000L));
      equal(5_000L, coordinator.transferTimeoutMillis(5_000L));
      Files.writeString(directory.resolve("broken.mcpr"), "not a replay", StandardCharsets.UTF_8);
      Path volatileRecording = directory.resolve("recording/session.mcpr.tmp/changed");
      Files.createDirectories(volatileRecording);
      writeReplay(
          volatileRecording.resolve("transient.mcpr"),
          "JUMP_EPISODE_V2_0_"
              + episodes.getFirst().episodeId()
              + "_"
              + episodes.getFirst().seed());
      for (EpisodeRecordingCoordinator.Episode episode : episodes) {
        writeReplay(
            directory.resolve("episode-" + episode.ordinal() + ".mcpr"),
            "JUMP_EPISODE_V2_"
                + episode.ordinal()
                + "_"
                + episode.episodeId()
                + "_"
                + episode.seed());
      }
      check(coordinator.pollFinalizedArtifacts().isEmpty());
      List<EpisodeRecordingCoordinator.Artifact> artifacts =
          coordinator.pollFinalizedArtifacts().orElseThrow();
      equal(5, artifacts.size());
      equal(71L, artifacts.getFirst().episodeId());
      equal(32, artifacts.getFirst().sha256().length);

      EpisodeRecordingCoordinator.Artifact first = artifacts.getFirst();
      coordinator.beginOffer(first);
      throwsCode(
          ErrorCode.ERROR_CODE_INVALID_MESSAGE,
          () ->
              coordinator.acknowledge(
                  RetentionAcknowledgement.newBuilder()
                      .setProtocolVersion(2)
                      .setRequestId(80)
                      .setSessionId("session")
                      .setOrdinal(first.ordinal())
                      .setEpisodeId(first.episodeId())
                      .setSha256(ByteString.copyFrom(new byte[32]))
                      .setRetained(true)
                      .build()));
      coordinator.acknowledge(
          RetentionAcknowledgement.newBuilder()
              .setProtocolVersion(2)
              .setRequestId(80)
              .setSessionId("session")
              .setOrdinal(first.ordinal())
              .setEpisodeId(first.episodeId())
              .setSha256(ByteString.copyFrom(first.sha256()))
              .setRetained(true)
              .build());
      check(coordinator.takeAcknowledgement().orElseThrow().retained());
      coordinator.deleteRetainedArtifact(first);
      check(!Files.exists(first.stagingPath()));

      EpisodeRecordingCoordinator.Artifact second = artifacts.get(1);
      coordinator.beginOffer(second);
      coordinator.acknowledge(
          RetentionAcknowledgement.newBuilder()
              .setProtocolVersion(2)
              .setRequestId(80)
              .setSessionId("session")
              .setOrdinal(second.ordinal())
              .setEpisodeId(second.episodeId())
              .setSha256(ByteString.copyFrom(second.sha256()))
              .setRetained(false)
              .setDetail("copy failed")
              .build());
      check(!coordinator.takeAcknowledgement().orElseThrow().retained());
      check(Files.exists(second.stagingPath()));
      check(coordinator.finalizing());
      coordinator.completeFinalization();
      check(!coordinator.finalizing());
      check(!ReplayModStatus.startupReady());
      check(!ReplayModStatus.recording());
    } finally {
      try (var paths = Files.walk(directory)) {
        paths.sorted(Comparator.reverseOrder()).forEach(CoreTestMain::deleteUnchecked);
      }
    }
  }

  private static void writeReplay(Path path, String marker) throws IOException {
    try (ZipOutputStream output = new ZipOutputStream(Files.newOutputStream(path))) {
      output.putNextEntry(new ZipEntry("metaData.json"));
      output.write("{}".getBytes(StandardCharsets.UTF_8));
      output.closeEntry();
      output.putNextEntry(new ZipEntry("recording.tmcpr"));
      output.write(new byte[] {1, 2, 3});
      output.closeEntry();
      output.putNextEntry(new ZipEntry("markers.json"));
      output.write(("[{\"name\":\"" + marker + "\"}]").getBytes(StandardCharsets.UTF_8));
      output.closeEntry();
    }
  }

  private static final class FakeMarkers implements EpisodeRecordingCoordinator.MarkerWriter {
    private final List<String> written = new java.util.ArrayList<>();
    private long duration = 100;

    @Override
    public long currentDurationMillis() {
      return duration++;
    }

    @Override
    public void addMarker(String name, int timeMillis) {
      written.add(name + "@" + timeMillis);
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
        .setProtocolVersion(2)
        .setRequestId(requestId)
        .setSessionId("session")
        .setEpisodeId(episodeId)
        .setSeed(seed)
        .setClientTick(10)
        .build();
  }

  private static EpisodeReady ready(ResetRequest reset, double gap) {
    return EpisodeReady.newBuilder()
        .setProtocolVersion(2)
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
        .setProtocolVersion(2)
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
        .setProtocolVersion(2)
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
