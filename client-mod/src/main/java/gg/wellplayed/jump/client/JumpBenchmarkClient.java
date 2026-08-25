package gg.wellplayed.jump.client;

import com.google.protobuf.ByteString;
import com.google.protobuf.InvalidProtocolBufferException;
import gg.wellplayed.jump.client.core.ControlledInputs;
import gg.wellplayed.jump.client.core.EpisodeRecordingCoordinator;
import gg.wellplayed.jump.client.core.EpisodeSequencer;
import gg.wellplayed.jump.client.core.ExpectedDisconnects;
import gg.wellplayed.jump.client.core.FramedProtobuf;
import gg.wellplayed.jump.client.core.LoopbackServer;
import gg.wellplayed.jump.client.core.ObservationMath;
import gg.wellplayed.jump.client.core.ProtocolViolation;
import gg.wellplayed.jump.client.core.ReplayModStatus;
import gg.wellplayed.jump.protocol.v1.Action;
import gg.wellplayed.jump.protocol.v1.ActionApplied;
import gg.wellplayed.jump.protocol.v1.ActionRequest;
import gg.wellplayed.jump.protocol.v1.BatchComplete;
import gg.wellplayed.jump.protocol.v1.CommandFinalize;
import gg.wellplayed.jump.protocol.v1.ConnectionHello;
import gg.wellplayed.jump.protocol.v1.ConnectionReady;
import gg.wellplayed.jump.protocol.v1.EpisodeArtifact;
import gg.wellplayed.jump.protocol.v1.EpisodePhase;
import gg.wellplayed.jump.protocol.v1.EpisodeReady;
import gg.wellplayed.jump.protocol.v1.EpisodeState;
import gg.wellplayed.jump.protocol.v1.ErrorCode;
import gg.wellplayed.jump.protocol.v1.Observation;
import gg.wellplayed.jump.protocol.v1.ProtocolError;
import gg.wellplayed.jump.protocol.v1.TerminalReason;
import gg.wellplayed.jump.protocol.v1.WireMessage;
import java.io.IOException;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.ConnectScreen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.multiplayer.resolver.ServerAddress;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.network.chat.Component;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** Fabric entry point for the tick-synchronous trainer bridge and episode recorder. */
public final class JumpBenchmarkClient implements ClientModInitializer {
  private static final Logger LOGGER = LoggerFactory.getLogger("jump-benchmark-client");
  private static final int PROTOCOL_VERSION = 2;
  private static final int DEFAULT_PORT = 64123;
  private static final long DEFAULT_FINALIZATION_TIMEOUT_MILLIS = 300_000L;
  private static final long FINALIZATION_TIMEOUT_MILLIS =
      Long.getLong("jump.client.finalizationTimeoutMillis", DEFAULT_FINALIZATION_TIMEOUT_MILLIS);
  private static final long FINALIZATION_POLL_MILLIS = 100L;

  private final EpisodeSequencer sequencer = new EpisodeSequencer();
  private final ControlledInputs inputs = new ControlledInputs();
  private final ExpectedDisconnects expectedTrainerDisconnects = new ExpectedDisconnects();
  private final EpisodeRecordingCoordinator recordings =
      new EpisodeRecordingCoordinator(configuredReplayDirectory());
  private final LoopbackServer trainer =
      new LoopbackServer(
          Integer.getInteger("jump.client.port", DEFAULT_PORT), new TrainerListener());

  private long clientTick;
  private long lastCompletedClientTick;
  private long actionAppliedClientTick;
  private long lastHelloAttemptTick;
  private long lastStabilityLogTick;
  private ConnectionHello hello;
  private ConnectionReady connectionReady;
  private WireMessage pendingPaperAction;
  private boolean jumpPressedThisTick;
  private boolean minecraftConnectRequested;
  private boolean replayStartupCallbackRegistered;
  private boolean replayStartupComplete;
  private boolean recordingSessionInitialized;
  private volatile boolean finalizerRunning;

  @Override
  public void onInitializeClient() {
    PayloadTypeRegistry.serverboundPlay().register(BenchmarkPayload.TYPE, BenchmarkPayload.CODEC);
    PayloadTypeRegistry.clientboundPlay().register(BenchmarkPayload.TYPE, BenchmarkPayload.CODEC);
    ClientPlayNetworking.registerGlobalReceiver(
        BenchmarkPayload.TYPE,
        (payload, context) -> receivePaper(payload.data(), context.client()));
    ClientPlayConnectionEvents.JOIN.register((listener, sender, client) -> joinedPaper(client));
    ClientPlayConnectionEvents.DISCONNECT.register((listener, client) -> disconnectedPaper(client));
    ClientTickEvents.START_CLIENT_TICK.register(this::startTick);
    ClientTickEvents.END_CLIENT_TICK.register(this::endTick);
    try {
      trainer.start();
    } catch (IOException exception) {
      throw new IllegalStateException("cannot listen for trainer on loopback", exception);
    }
    LOGGER.info(
        "Jump benchmark client ready on 127.0.0.1:{}; waiting for Replay Mod startup",
        DEFAULT_PORT);
  }

  private void joinedPaper(Minecraft client) {
    releaseAll(client);
    minecraftConnectRequested = true;
    recordingSessionInitialized = false;
    String sessionId = UUID.randomUUID().toString();
    sequencer.startSession(sessionId);
    hello =
        ConnectionHello.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId(sessionId)
            .setClientNonce(UUID.randomUUID().toString())
            .setClientTick(clientTick)
            .build();
    connectionReady = null;
    WireMessage message = envelope().setConnectionHello(hello).build();
    lastHelloAttemptTick = clientTick;
    LOGGER.info(
        "Joined Paper; jump:control sendable={}",
        ClientPlayNetworking.canSend(BenchmarkPayload.TYPE));
    sendPaper(client, message);
    initializeRecordingSessionIfReady(client);
  }

  private void disconnectedPaper(Minecraft client) {
    releaseAll(client);
    sequencer.abort();
    minecraftConnectRequested = false;
    recordingSessionInitialized = false;
    if (recordings.finalizing()) {
      hello = null;
      connectionReady = null;
      finalizeReplayAsync(client);
      return;
    }

    sendError(
        client,
        ErrorCode.ERROR_CODE_INTERNAL,
        "Minecraft disconnected from the benchmark server",
        true);
    beginUnexpectedFinalization();
    hello = null;
    connectionReady = null;
    finalizeReplayAsync(client);
  }

  private void receiveTrainer(WireMessage message, Minecraft client) {
    if (message.getProtocolVersion() != PROTOCOL_VERSION) {
      sendError(
          client, ErrorCode.ERROR_CODE_VERSION_MISMATCH, "expected protocol version 2", false);
      return;
    }
    try {
      switch (message.getPayloadCase()) {
        case RESET_REQUEST -> {
          releaseAll(client);
          sequencer.beginReset(message.getResetRequest());
          recordEpisodeStart(message.getResetRequest());
          sendPaper(client, message);
        }
        case ACTION_REQUEST -> sequencer.queueAction(message.getActionRequest());
        case COMMAND_FINALIZE -> beginCommandFinalization(message.getCommandFinalize(), client);
        case RETENTION_ACKNOWLEDGEMENT ->
            recordings.acknowledge(message.getRetentionAcknowledgement());
        case SHUTDOWN -> {
          interruptRecording();
          releaseAll(client);
          sequencer.abort();
          sendPaper(client, message);
        }
        default ->
            throw new ProtocolViolation(
                ErrorCode.ERROR_CODE_INVALID_MESSAGE, "payload is not valid from Python to Fabric");
      }
    } catch (ProtocolViolation exception) {
      releaseAll(client);
      sendError(client, exception.code(), exception.getMessage(), false);
    }
  }

  private void beginCommandFinalization(CommandFinalize request, Minecraft client)
      throws ProtocolViolation {
    if (client.getConnection() == null) {
      throw new ProtocolViolation(
          ErrorCode.ERROR_CODE_INTERNAL,
          "cannot finalize recordings while Minecraft is disconnected");
    }
    try {
      recordings.beginFinalization(request, ReplayModStatus.markerWriter());
    } catch (IOException exception) {
      recordingWarning("could not write the final episode split markers", exception);
      recordings.beginFinalizationBestEffort(request);
    }
    releaseAll(client);
    sequencer.abort();
    LOGGER.info(
        "Disconnecting from Paper to finalize Replay Mod recordings: {}", request.getReason());
    client.getConnection().getConnection().disconnect(Component.literal(request.getReason()));
  }

  private void receivePaper(byte[] data, Minecraft client) {
    if (data.length == 0 || data.length > FramedProtobuf.MAX_MESSAGE_BYTES) {
      releaseAll(client);
      interruptRecording();
      sendError(client, ErrorCode.ERROR_CODE_INVALID_MESSAGE, "invalid Paper payload size", false);
      return;
    }
    final WireMessage message;
    try {
      message = WireMessage.parseFrom(data);
    } catch (InvalidProtocolBufferException exception) {
      releaseAll(client);
      interruptRecording();
      sendError(client, ErrorCode.ERROR_CODE_INVALID_MESSAGE, "Paper sent invalid protobuf", false);
      return;
    }
    if (message.getProtocolVersion() != PROTOCOL_VERSION) {
      releaseAll(client);
      interruptRecording();
      sendError(client, ErrorCode.ERROR_CODE_VERSION_MISMATCH, "Paper protocol mismatch", false);
      return;
    }
    try {
      switch (message.getPayloadCase()) {
        case CONNECTION_READY -> {
          ConnectionReady ready = message.getConnectionReady();
          if (!sequencer.sessionId().equals(ready.getSessionId())
              || !"26.2".equals(ready.getMinecraftVersion())) {
            throw new ProtocolViolation(
                ErrorCode.ERROR_CODE_STALE_REQUEST,
                "Paper connection acknowledgement does not match");
          }
          connectionReady = ready;
          sendHandshakeIfReady(client);
        }
        case EPISODE_READY -> {
          sequencer.receiveReady(message.getEpisodeReady());
          lastStabilityLogTick = clientTick;
          LOGGER.debug(
              "Paper episode {} is ready; checking client reset state",
              message.getEpisodeReady().getEpisodeId());
        }
        case EPISODE_STATE -> {
          sequencer.receiveState(message.getEpisodeState());
          emitObservationIfActionTickComplete(client);
        }
        case EPISODE_RESULT -> {
          sequencer.receiveResult(message.getEpisodeResult());
          emitObservationIfActionTickComplete(client);
        }
        case ERROR -> {
          releaseAll(client);
          interruptRecording();
          sendTrainerIfConnected(message, client);
        }
        case SHUTDOWN -> {
          releaseAll(client);
          interruptRecording();
          sequencer.abort();
          sendTrainerIfConnected(message, client);
        }
        default ->
            throw new ProtocolViolation(
                ErrorCode.ERROR_CODE_INVALID_MESSAGE, "payload is not valid from Paper to Fabric");
      }
    } catch (ProtocolViolation exception) {
      releaseAll(client);
      interruptRecording();
      sendError(client, exception.code(), exception.getMessage(), false);
    }
  }

  private void startTick(Minecraft client) {
    clientTick++;
    // A headless benchmark has no physical mouse or keyboard input. Keep the
    // vanilla inactivity tracker active so it does not silently cap the client
    // at 10 FPS after ten minutes and starve tick/network task processing.
    client.getFramerateLimitTracker().onInputReceived();
    if (!replayStartupCallbackRegistered) {
      replayStartupCallbackRegistered =
          ReplayModStatus.runAfterStartup(
              () ->
                  client.execute(
                      () -> {
                        replayStartupComplete = true;
                        LOGGER.info("Replay Mod post-startup work is complete");
                      }));
    }
    if (replayStartupComplete
        && !minecraftConnectRequested
        && client.getConnection() == null
        && !finalizerRunning
        && !recordings.finalizing()) {
      connectMinecraft(client);
    }
    initializeRecordingSessionIfReady(client);

    inputs.finishTick();
    syncInputs(client);
    jumpPressedThisTick = false;
    if (hello != null
        && connectionReady == null
        && clientTick - lastHelloAttemptTick >= 20
        && client.getConnection() != null
        && ClientPlayNetworking.canSend(BenchmarkPayload.TYPE)) {
      lastHelloAttemptTick = clientTick;
      LOGGER.info("Retrying Paper benchmark hello at client tick {}", clientTick);
      sendPaper(client, envelope().setConnectionHello(hello).build());
    }
    if (sequencer.actionTimedOut(clientTick)) {
      releaseAll(client);
      interruptRecording();
      sequencer.abort();
      sendError(
          client, ErrorCode.ERROR_CODE_ACTION_TIMEOUT, "trainer missed action deadline", true);
      return;
    }

    ActionRequest action = sequencer.applyQueuedAction();
    if (action == null) {
      return;
    }
    if (client.player == null || connectionReady == null) {
      releaseAll(client);
      interruptRecording();
      sequencer.abort();
      sendError(
          client, ErrorCode.ERROR_CODE_INTERNAL, "cannot apply action while disconnected", true);
      return;
    }

    inputs.apply(action.getAction());
    actionAppliedClientTick = clientTick;
    jumpPressedThisTick = action.getAction() == Action.ACTION_JUMP;
    syncInputs(client);
    ActionApplied applied =
        ActionApplied.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId(sequencer.sessionId())
            .setEpisodeId(action.getEpisodeId())
            .setClientTick(clientTick)
            .setServerTick(action.getServerTick())
            .setObservationSequence(action.getObservationSequence())
            .setActionSequence(action.getActionSequence())
            .setRequestedAction(action.getAction())
            .build();
    WireMessage envelope = envelope().setActionApplied(applied).build();
    pendingPaperAction = envelope;
    sendTrainerIfConnected(envelope, client);
  }

  private void endTick(Minecraft client) {
    lastCompletedClientTick = clientTick;
    if (jumpPressedThisTick && client.options != null) {
      inputs.finishTick();
      syncInputs(client);
      jumpPressedThisTick = false;
    }
    if (pendingPaperAction != null) {
      WireMessage completedAction = pendingPaperAction;
      pendingPaperAction = null;
      sendPaper(client, completedAction);
    }
    if (client.player == null) {
      releaseAll(client);
      return;
    }

    if (sequencer.phase() == EpisodeSequencer.Phase.STABILIZING) {
      EpisodeReady ready = sequencer.ready();
      ObservationMath.Sample sample = sample(client.player, ready);
      Vec3 velocity = client.player.getDeltaMovement();
      double horizontalSpeedSquared = velocity.x * velocity.x + velocity.z * velocity.z;
      boolean stable =
          ObservationMath.resetStateMatches(sample, ready.getStartingGap(), horizontalSpeedSquared);
      if (sequencer.observeClientStability(stable, clientTick)) {
        EpisodeReady synchronizedReady = ready.toBuilder().setClientTick(clientTick).build();
        sendTrainerIfConnected(envelope().setEpisodeReady(synchronizedReady).build(), client);
        sendObservation(
            client,
            EpisodeState.newBuilder()
                .setProtocolVersion(PROTOCOL_VERSION)
                .setSessionId(sequencer.sessionId())
                .setEpisodeId(ready.getEpisodeId())
                .setServerTick(ready.getInitialServerTick())
                .setElapsedTicks(0)
                .setPhase(EpisodePhase.EPISODE_PHASE_READY)
                .setTerminalReason(TerminalReason.TERMINAL_REASON_UNSPECIFIED)
                .build());
      } else if (!stable && clientTick - lastStabilityLogTick >= 20) {
        lastStabilityLogTick = clientTick;
        LOGGER.warn(
            "Waiting for client reset state: distance={}, height={}, horizontalSpeedSquared={},"
                + " onGround={}",
            sample.signedWallDistance(),
            sample.relativeFeetHeight(),
            horizontalSpeedSquared,
            sample.onGround());
      }
      return;
    }

    emitObservationIfActionTickComplete(client);
  }

  private void emitObservationIfActionTickComplete(Minecraft client) {
    if (!sequencer.observationDue() || lastCompletedClientTick < actionAppliedClientTick) {
      return;
    }
    EpisodeState state = sequencer.completeObservation(clientTick);
    sendObservation(client, state);
    if (state.getPhase() == EpisodePhase.EPISODE_PHASE_TERMINAL
        || state.getPhase() == EpisodePhase.EPISODE_PHASE_ABORTED) {
      releaseAll(client);
    }
  }

  private void sendObservation(Minecraft client, EpisodeState state) {
    EpisodeReady ready = sequencer.ready();
    if (client.player == null || ready == null) {
      releaseAll(client);
      interruptRecording();
      sendError(client, ErrorCode.ERROR_CODE_INTERNAL, "observation state is unavailable", true);
      return;
    }
    ObservationMath.Sample sample = sample(client.player, ready);
    Observation observation =
        Observation.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId(sequencer.sessionId())
            .setEpisodeId(state.getEpisodeId())
            .setClientTick(clientTick)
            .setServerTick(state.getServerTick())
            .setObservationSequence(sequencer.observationSequence())
            .setActionSequence(sequencer.actionSequence())
            .setPhase(state.getPhase())
            .setTerminalReason(state.getTerminalReason())
            .setSignedWallDistance(sample.signedWallDistance())
            .setRelativeFeetHeight(sample.relativeFeetHeight())
            .setVerticalVelocity(sample.verticalVelocity())
            .setLaneVelocity(sample.laneVelocity())
            .setOnGround(sample.onGround())
            .setElapsedTicks(state.getElapsedTicks())
            .build();
    sendTrainerIfConnected(envelope().setObservation(observation).build(), client);
    if (state.getPhase() == EpisodePhase.EPISODE_PHASE_TERMINAL) {
      recordEpisodeComplete(state.getEpisodeId(), state.getTerminalReason());
    } else if (state.getPhase() == EpisodePhase.EPISODE_PHASE_ABORTED) {
      interruptRecording();
    }
  }

  private static ObservationMath.Sample sample(LocalPlayer player, EpisodeReady ready) {
    AABB box = player.getBoundingBox();
    Vec3 velocity = player.getDeltaMovement();
    return ObservationMath.sample(
        box.minX,
        box.minY,
        box.minZ,
        box.maxX,
        box.maxZ,
        velocity.x,
        velocity.y,
        velocity.z,
        player.onGround(),
        ready.getWallNearCoordinate(),
        ready.getLaneDirectionX(),
        ready.getLaneDirectionZ(),
        ready.getStandingFeetY());
  }

  private void initializeRecordingSessionIfReady(Minecraft client) {
    if (recordingSessionInitialized
        || hello == null
        || client.getConnection() == null
        || !ReplayModStatus.recording()) {
      return;
    }
    try {
      recordings.beginSession(hello.getSessionId(), ReplayModStatus.markerWriter());
      LOGGER.info("Replay Mod is logically stopped and ready for episode markers");
    } catch (IOException | ProtocolViolation exception) {
      recordingWarning("could not initialize episode recording markers", exception);
    }
    recordingSessionInitialized = true;
    sendHandshakeIfReady(client);
  }

  private void sendHandshakeIfReady(Minecraft client) {
    if (!recordingSessionInitialized || hello == null) {
      return;
    }
    sendTrainerIfConnected(envelope().setConnectionHello(hello).build(), client);
    if (connectionReady != null) {
      sendTrainerIfConnected(envelope().setConnectionReady(connectionReady).build(), client);
    }
  }

  private void recordEpisodeStart(gg.wellplayed.jump.protocol.v1.ResetRequest request) {
    try {
      recordings.beginEpisode(request, ReplayModStatus.markerWriter());
    } catch (IOException | ProtocolViolation exception) {
      recordingWarning(
          "could not begin replay markers for episode " + request.getEpisodeId(), exception);
    }
  }

  private void recordEpisodeComplete(long episodeId, TerminalReason reason) {
    try {
      recordings.completeEpisode(episodeId, reason, ReplayModStatus.markerWriter());
    } catch (IOException | ProtocolViolation exception) {
      recordingWarning("could not close replay markers for episode " + episodeId, exception);
    }
  }

  private void interruptRecording() {
    try {
      recordings.interruptActive(ReplayModStatus.markerWriter());
    } catch (IOException | ProtocolViolation exception) {
      recordingWarning("could not mark the active replay as partial", exception);
    }
  }

  private void beginUnexpectedFinalization() {
    try {
      recordings.beginUnexpectedFinalization(ReplayModStatus.markerWriter());
    } catch (IOException | ProtocolViolation exception) {
      recordingWarning("could not close the interrupted recording batch", exception);
      recordings.beginUnexpectedFinalizationBestEffort();
    }
  }

  private void finalizeReplayAsync(Minecraft client) {
    if (finalizerRunning) {
      return;
    }
    finalizerRunning = true;
    Thread.ofVirtual()
        .name("jump-replay-finalizer")
        .start(
            () -> {
              int expected = recordings.expectedArtifactCount();
              int offered = 0;
              int retained = 0;
              int preserved = 0;
              long timeoutMillis = recordings.transferTimeoutMillis(FINALIZATION_TIMEOUT_MILLIS);
              long deadlineNanos = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMillis);
              List<EpisodeRecordingCoordinator.Artifact> artifacts = List.of();
              try {
                artifacts = awaitFinalizedArtifacts(deadlineNanos);
                Set<Integer> availableOrdinals = new HashSet<>();
                for (EpisodeRecordingCoordinator.Artifact artifact : artifacts) {
                  availableOrdinals.add(artifact.ordinal());
                }
                for (int ordinal = 0; ordinal < expected; ordinal++) {
                  if (!availableOrdinals.contains(ordinal)) {
                    preserved++;
                    recordings.addWarning(
                        "no finalized staging file was found for episode ordinal " + ordinal);
                  }
                }

                for (EpisodeRecordingCoordinator.Artifact artifact : artifacts) {
                  if (!trainer.connected()
                      || recordings.finalizationRequestId() == 0
                      || deadlineReached(deadlineNanos)) {
                    preserved++;
                    recordings.addWarning(
                        "preserved episode "
                            + artifact.episodeId()
                            + " because no trainer acknowledgement was available before the "
                            + "finalization deadline");
                    continue;
                  }
                  recordings.beginOffer(artifact);
                  if (!sendArtifact(artifact)) {
                    recordings.abandonOutstanding(
                        "preserved episode "
                            + artifact.episodeId()
                            + " after its offer could not be sent");
                    preserved++;
                    continue;
                  }
                  offered++;
                  Optional<EpisodeRecordingCoordinator.Acknowledgement> acknowledgement =
                      awaitAcknowledgement(deadlineNanos);
                  if (acknowledgement.isEmpty()) {
                    recordings.abandonOutstanding(
                        "retention acknowledgement timed out for episode " + artifact.episodeId());
                    preserved++;
                    continue;
                  }
                  EpisodeRecordingCoordinator.Acknowledgement completed =
                      acknowledgement.orElseThrow();
                  if (!completed.retained()) {
                    recordings.addWarning(
                        "trainer did not retain episode "
                            + artifact.episodeId()
                            + ": "
                            + completed.detail());
                    preserved++;
                    continue;
                  }
                  retained++;
                  try {
                    recordings.deleteRetainedArtifact(artifact);
                  } catch (IOException exception) {
                    preserved++;
                    recordingWarning(
                        "canonical copy was retained but its staging source could not be deleted",
                        exception);
                  }
                }

                if (retained == expected && preserved == 0) {
                  try {
                    recordings.deleteCurrentRawSources();
                  } catch (IOException exception) {
                    recordingWarning("could not delete Replay Mod raw staging sources", exception);
                  }
                }
                sendBatchComplete(expected, offered, retained, preserved);
              } catch (IOException | ProtocolViolation | RuntimeException exception) {
                recordingWarning("recording batch finalization failed", exception);
                preserved = Math.max(preserved, expected - retained);
                sendBatchComplete(expected, offered, retained, preserved);
              } finally {
                recordings.completeFinalization();
                client.execute(
                    () -> {
                      finalizerRunning = false;
                      connectMinecraft(client);
                    });
              }
            });
  }

  private List<EpisodeRecordingCoordinator.Artifact> awaitFinalizedArtifacts(long deadlineNanos)
      throws IOException {
    while (!deadlineReached(deadlineNanos)) {
      if (!ReplayModStatus.recording()) {
        Optional<List<EpisodeRecordingCoordinator.Artifact>> completed =
            recordings.pollFinalizedArtifacts();
        if (completed.isPresent()) {
          return completed.orElseThrow();
        }
      }
      try {
        Thread.sleep(FINALIZATION_POLL_MILLIS);
      } catch (InterruptedException exception) {
        Thread.currentThread().interrupt();
        throw new IOException("Replay Mod finalization was interrupted", exception);
      }
    }
    recordings.addWarning("Replay Mod finalization timed out before every episode was available");
    return recordings.finalizedArtifactsAvailableAtTimeout();
  }

  private Optional<EpisodeRecordingCoordinator.Acknowledgement> awaitAcknowledgement(
      long deadlineNanos) throws IOException {
    while (!deadlineReached(deadlineNanos) && trainer.connected()) {
      Optional<EpisodeRecordingCoordinator.Acknowledgement> acknowledgement =
          recordings.takeAcknowledgement();
      if (acknowledgement.isPresent()) {
        return acknowledgement;
      }
      try {
        Thread.sleep(50L);
      } catch (InterruptedException exception) {
        Thread.currentThread().interrupt();
        throw new IOException("artifact acknowledgement wait was interrupted", exception);
      }
    }
    return Optional.empty();
  }

  private static boolean deadlineReached(long deadlineNanos) {
    return deadlineNanos - System.nanoTime() <= 0;
  }

  private boolean sendArtifact(EpisodeRecordingCoordinator.Artifact artifact) {
    return trySendTrainer(
        envelope()
            .setEpisodeArtifact(
                EpisodeArtifact.newBuilder()
                    .setProtocolVersion(PROTOCOL_VERSION)
                    .setRequestId(artifact.requestId())
                    .setSessionId(artifact.sessionId())
                    .setOrdinal(artifact.ordinal())
                    .setEpisodeId(artifact.episodeId())
                    .setSeed(artifact.seed())
                    .setRecordingStatus(artifact.recordingStatus())
                    .setTerminalReason(artifact.terminalReason())
                    .setStagingPath(artifact.stagingPath().toString())
                    .setSizeBytes(artifact.sizeBytes())
                    .setSha256(ByteString.copyFrom(artifact.sha256())))
            .build());
  }

  private boolean sendBatchComplete(int expected, int offered, int retained, int preserved) {
    long requestId = recordings.finalizationRequestId();
    long connectionId = trainer.connectionId();
    if (requestId == 0 || connectionId == 0) {
      return false;
    }
    expectedTrainerDisconnects.expect(connectionId);
    boolean sent =
        trySendTrainer(
            connectionId,
            envelope()
                .setBatchComplete(
                    BatchComplete.newBuilder()
                        .setProtocolVersion(PROTOCOL_VERSION)
                        .setRequestId(requestId)
                        .setSessionId(recordings.sessionId())
                        .setExpectedArtifacts(expected)
                        .setOfferedArtifacts(offered)
                        .setRetainedArtifacts(retained)
                        .setPreservedArtifacts(preserved)
                        .addAllWarnings(recordings.warnings())
                        .setReconnectingMinecraft(true))
                .build());
    if (!sent) {
      expectedTrainerDisconnects.cancel(connectionId);
    }
    return sent;
  }

  private void connectMinecraft(Minecraft client) {
    if (!replayStartupComplete
        || minecraftConnectRequested
        || finalizerRunning
        || recordings.finalizing()
        || client.getConnection() != null) {
      return;
    }
    String address = System.getProperty("jump.client.server", "127.0.0.1:25565");
    ServerData server = new ServerData("Jump Benchmark", address, ServerData.Type.OTHER);
    minecraftConnectRequested = true;
    LOGGER.info("Replay Mod is ready; connecting the persistent client to {}", address);
    ConnectScreen.startConnecting(
        new TitleScreen(), client, ServerAddress.parseString(address), server, true, null);
  }

  private void sendPaper(Minecraft client, WireMessage message) {
    try {
      if (client.getConnection() == null || !ClientPlayNetworking.canSend(BenchmarkPayload.TYPE)) {
        throw new IllegalStateException("Paper does not accept jump:control");
      }
      ClientPlayNetworking.send(new BenchmarkPayload(message.toByteArray()));
    } catch (RuntimeException exception) {
      releaseAll(client);
      interruptRecording();
      sequencer.abort();
      sendError(client, ErrorCode.ERROR_CODE_INTERNAL, exception.getMessage(), true);
    }
  }

  private void sendTrainerIfConnected(WireMessage message, Minecraft client) {
    if (!trainer.connected()) {
      return;
    }
    try {
      trainer.send(message);
    } catch (IOException exception) {
      releaseAll(client);
      sequencer.abort();
      LOGGER.error("Trainer transport failed", exception);
    }
  }

  private boolean trySendTrainer(WireMessage message) {
    return trySendTrainer(trainer.connectionId(), message);
  }

  private boolean trySendTrainer(long connectionId, WireMessage message) {
    try {
      trainer.send(connectionId, message);
      return true;
    } catch (IOException exception) {
      LOGGER.warn(
          "Could not send recording lifecycle message to trainer: {}", exception.toString());
      return false;
    }
  }

  private void sendError(Minecraft client, ErrorCode code, String description, boolean retryable) {
    ProtocolError.Builder error =
        ProtocolError.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId(sequencer.sessionId())
            .setClientTick(clientTick)
            .setCode(code)
            .setMessage(description == null ? "unknown client failure" : description)
            .setRetryable(retryable);
    if (sequencer.reset() != null) {
      error
          .setRequestId(sequencer.reset().getRequestId())
          .setEpisodeId(sequencer.reset().getEpisodeId())
          .setObservationSequence(sequencer.observationSequence())
          .setActionSequence(sequencer.actionSequence());
    }
    sendTrainerIfConnected(envelope().setError(error).build(), client);
  }

  private void recordingWarning(String description, Throwable failure) {
    String detail =
        failure.getMessage() == null ? description : description + ": " + failure.getMessage();
    recordings.addWarning(detail);
    LOGGER.warn(detail, failure);
  }

  private void releaseAll(Minecraft client) {
    pendingPaperAction = null;
    inputs.releaseAll();
    syncInputs(client);
  }

  private void syncInputs(Minecraft client) {
    if (client.options != null) {
      client.options.keyUp.setDown(inputs.forward());
      client.options.keyJump.setDown(inputs.jump());
      client.options.keyLeft.setDown(false);
      client.options.keyRight.setDown(false);
      client.options.keyDown.setDown(false);
      client.options.keySprint.setDown(false);
    }
  }

  private static WireMessage.Builder envelope() {
    return WireMessage.newBuilder().setProtocolVersion(PROTOCOL_VERSION);
  }

  private static Path configuredReplayDirectory() {
    return Path.of(System.getProperty("jump.client.replayDir", "replay_recordings"));
  }

  private final class TrainerListener implements LoopbackServer.Listener {
    @Override
    public void connected(long connectionId) {
      Minecraft.getInstance()
          .execute(
              () -> {
                if (trainer.connectionId() != connectionId) {
                  return;
                }
                Minecraft client = Minecraft.getInstance();
                sendHandshakeIfReady(client);
              });
    }

    @Override
    public void message(long connectionId, WireMessage message) {
      Minecraft.getInstance()
          .execute(
              () -> {
                if (trainer.connectionId() == connectionId) {
                  receiveTrainer(message, Minecraft.getInstance());
                }
              });
    }

    @Override
    public void disconnected(long connectionId, Throwable cause) {
      boolean expected = connectionId > 0 && expectedTrainerDisconnects.consume(connectionId);
      Minecraft.getInstance()
          .execute(
              () -> {
                if (expected) {
                  LOGGER.info("Trainer command connection closed normally");
                  return;
                }
                Minecraft client = Minecraft.getInstance();
                releaseAll(client);
                sequencer.abort();
                if (!recordings.finalizing() && client.getConnection() != null) {
                  beginUnexpectedFinalization();
                  client
                      .getConnection()
                      .getConnection()
                      .disconnect(Component.literal("trainer disconnected unexpectedly"));
                }
                if (cause == null) {
                  LOGGER.warn("Trainer disconnected unexpectedly");
                } else {
                  LOGGER.warn("Trainer disconnected: {}", cause.toString());
                }
              });
    }
  }
}
