package gg.wellplayed.jump.client;

import com.google.protobuf.ByteString;
import com.google.protobuf.InvalidProtocolBufferException;
import gg.wellplayed.jump.client.core.ControlledInputs;
import gg.wellplayed.jump.client.core.EpisodeSequencer;
import gg.wellplayed.jump.client.core.FramedProtobuf;
import gg.wellplayed.jump.client.core.LoopbackServer;
import gg.wellplayed.jump.client.core.ObservationMath;
import gg.wellplayed.jump.client.core.ProtocolViolation;
import gg.wellplayed.jump.client.core.ReplayCaptureCoordinator;
import gg.wellplayed.jump.client.core.ReplayModStatus;
import gg.wellplayed.jump.protocol.v1.Action;
import gg.wellplayed.jump.protocol.v1.ActionApplied;
import gg.wellplayed.jump.protocol.v1.ActionRequest;
import gg.wellplayed.jump.protocol.v1.CaptureComplete;
import gg.wellplayed.jump.protocol.v1.CaptureReady;
import gg.wellplayed.jump.protocol.v1.ClientMode;
import gg.wellplayed.jump.protocol.v1.ConnectionHello;
import gg.wellplayed.jump.protocol.v1.ConnectionReady;
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
import java.util.Locale;
import java.util.Optional;
import java.util.UUID;
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
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** Fabric entry point for the tick-synchronous trainer bridge. */
public final class JumpBenchmarkClient implements ClientModInitializer {
  private static final Logger LOGGER = LoggerFactory.getLogger("jump-benchmark-client");
  private static final int PROTOCOL_VERSION = 1;
  private static final int DEFAULT_PORT = 64123;
  private static final long REPLAY_FINALIZATION_TIMEOUT_MILLIS =
      Long.getLong("jump.client.replayFinalizeTimeoutMillis", 45_000L);

  private final EpisodeSequencer sequencer = new EpisodeSequencer();
  private final ControlledInputs inputs = new ControlledInputs();
  private final ClientMode mode = configuredMode();
  private final ReplayCaptureCoordinator captures =
      new ReplayCaptureCoordinator(configuredReplayDirectory());
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
  private boolean recordingEnabled = mode == ClientMode.CLIENT_MODE_RECORDING;
  private boolean recordingConnectRequested;
  private boolean replayStartupCallbackRegistered;
  private boolean replayStartupComplete;

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
    LOGGER.info("Jump benchmark client ready in {} mode on 127.0.0.1:{}", modeName(), DEFAULT_PORT);
  }

  private void joinedPaper(Minecraft client) {
    releaseAll(client);
    if (mode == ClientMode.CLIENT_MODE_RECORDING) {
      recordingConnectRequested = true;
    }
    String sessionId = UUID.randomUUID().toString();
    sequencer.startSession(sessionId);
    hello =
        ConnectionHello.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId(sessionId)
            .setMode(mode)
            .setClientNonce(UUID.randomUUID().toString())
            .setClientTick(clientTick)
            .build();
    connectionReady = null;
    WireMessage envelope = envelope().setConnectionHello(hello).build();
    lastHelloAttemptTick = clientTick;
    LOGGER.info(
        "Joined Paper; jump:control sendable={}",
        ClientPlayNetworking.canSend(BenchmarkPayload.TYPE));
    sendPaper(client, envelope);
    sendTrainerIfConnected(envelope, client);
  }

  private void disconnectedPaper(Minecraft client) {
    releaseAll(client);
    sequencer.abort();
    recordingConnectRequested = false;
    if (captures.finalizing()) {
      hello = null;
      connectionReady = null;
      finalizeReplayAsync(client);
      return;
    }
    recordingEnabled = false;
    sendError(
        client,
        ErrorCode.ERROR_CODE_INTERNAL,
        "Minecraft disconnected from the benchmark server",
        true);
    hello = null;
    connectionReady = null;
  }

  private void receiveTrainer(WireMessage message, Minecraft client) {
    if (message.getProtocolVersion() != PROTOCOL_VERSION) {
      sendError(
          client, ErrorCode.ERROR_CODE_VERSION_MISMATCH, "expected protocol version 1", false);
      return;
    }
    try {
      switch (message.getPayloadCase()) {
        case RESET_REQUEST -> {
          releaseAll(client);
          sequencer.beginReset(message.getResetRequest());
          sendPaper(client, message);
        }
        case ACTION_REQUEST -> sequencer.queueAction(message.getActionRequest());
        case SHUTDOWN -> {
          if (captures.active() && message.getShutdown().getDisconnectMinecraft()) {
            if (sequencer.phase() != EpisodeSequencer.Phase.TERMINAL) {
              throw new ProtocolViolation(
                  ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION,
                  "capture cannot finalize before its episode is terminal");
            }
            if (client.getConnection() == null) {
              throw new ProtocolViolation(
                  ErrorCode.ERROR_CODE_INTERNAL,
                  "capture cannot finalize while Minecraft is disconnected");
            }
            captures.beginFinalization(message.getShutdown());
            recordingEnabled = message.getShutdown().getReconnectMinecraft();
            releaseAll(client);
            sequencer.abort();
            client
                .getConnection()
                .getConnection()
                .disconnect(
                    net.minecraft.network.chat.Component.literal(
                        message.getShutdown().getReason()));
            break;
          }
          if (message.getShutdown().getReconnectMinecraft()) {
            throw new ProtocolViolation(
                ErrorCode.ERROR_CODE_INVALID_MESSAGE,
                "reconnect_minecraft is only valid while finalizing a capture");
          }
          releaseAll(client);
          sequencer.abort();
          sendPaper(client, message);
          if (message.getShutdown().getDisconnectMinecraft() && client.getConnection() != null) {
            client
                .getConnection()
                .getConnection()
                .disconnect(
                    net.minecraft.network.chat.Component.literal(
                        message.getShutdown().getReason()));
          }
        }
        case CAPTURE_REQUEST -> {
          if (mode != ClientMode.CLIENT_MODE_RECORDING) {
            throw new ProtocolViolation(
                ErrorCode.ERROR_CODE_NOT_RECORDING,
                "capture requires a client started with --mode recording");
          }
          if (connectionReady == null || !ReplayModStatus.recording()) {
            throw new ProtocolViolation(
                ErrorCode.ERROR_CODE_NOT_RECORDING,
                "Replay Mod has not started recording this connection");
          }
          try {
            captures.begin(message.getCaptureRequest(), sequencer.sessionId());
          } catch (IOException exception) {
            throw new ProtocolViolation(
                ErrorCode.ERROR_CODE_INTERNAL,
                "cannot inspect Replay Mod directory: " + exception.getMessage());
          }
          sendTrainer(
              envelope()
                  .setCaptureReady(
                      CaptureReady.newBuilder()
                          .setProtocolVersion(PROTOCOL_VERSION)
                          .setRequestId(message.getCaptureRequest().getRequestId())
                          .setSessionId(sequencer.sessionId())
                          .setCheckpointId(message.getCaptureRequest().getCheckpointId())
                          .setClientTick(clientTick))
                  .build());
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

  private void receivePaper(byte[] data, Minecraft client) {
    if (data.length == 0 || data.length > FramedProtobuf.MAX_MESSAGE_BYTES) {
      releaseAll(client);
      sendError(client, ErrorCode.ERROR_CODE_INVALID_MESSAGE, "invalid Paper payload size", false);
      return;
    }
    final WireMessage message;
    try {
      message = WireMessage.parseFrom(data);
    } catch (InvalidProtocolBufferException exception) {
      releaseAll(client);
      sendError(client, ErrorCode.ERROR_CODE_INVALID_MESSAGE, "Paper sent invalid protobuf", false);
      return;
    }
    if (message.getProtocolVersion() != PROTOCOL_VERSION) {
      releaseAll(client);
      sendError(client, ErrorCode.ERROR_CODE_VERSION_MISMATCH, "Paper protocol mismatch", false);
      return;
    }
    try {
      switch (message.getPayloadCase()) {
        case CONNECTION_READY -> {
          ConnectionReady ready = message.getConnectionReady();
          if (!sequencer.sessionId().equals(ready.getSessionId())
              || ready.getMode() != mode
              || !"26.2".equals(ready.getMinecraftVersion())) {
            throw new ProtocolViolation(
                ErrorCode.ERROR_CODE_STALE_REQUEST,
                "Paper connection acknowledgement does not match");
          }
          connectionReady = ready;
          sendTrainerIfConnected(message, client);
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
          sendTrainerIfConnected(message, client);
        }
        case SHUTDOWN -> {
          releaseAll(client);
          sequencer.abort();
          sendTrainerIfConnected(message, client);
        }
        default ->
            throw new ProtocolViolation(
                ErrorCode.ERROR_CODE_INVALID_MESSAGE, "payload is not valid from Paper to Fabric");
      }
    } catch (ProtocolViolation exception) {
      releaseAll(client);
      sendError(client, exception.code(), exception.getMessage(), false);
    }
  }

  private void startTick(Minecraft client) {
    clientTick++;
    // A headless benchmark has no physical mouse or keyboard input. Keep the
    // vanilla inactivity tracker active so it does not silently cap the client
    // at 10 FPS after ten minutes and starve tick/network task processing.
    client.getFramerateLimitTracker().onInputReceived();
    if (mode == ClientMode.CLIENT_MODE_RECORDING && !replayStartupCallbackRegistered) {
      replayStartupCallbackRegistered =
          ReplayModStatus.runAfterStartup(
              () ->
                  client.execute(
                      () -> {
                        replayStartupComplete = true;
                        LOGGER.info("Replay Mod post-startup work is complete");
                      }));
    }
    if (mode == ClientMode.CLIENT_MODE_RECORDING
        && recordingEnabled
        && !recordingConnectRequested
        && client.getConnection() == null
        && !captures.finalizing()
        && replayStartupComplete) {
      connectRecordingClient(client);
    }
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

  private void sendPaper(Minecraft client, WireMessage message) {
    try {
      if (client.getConnection() == null || !ClientPlayNetworking.canSend(BenchmarkPayload.TYPE)) {
        throw new IllegalStateException("Paper does not accept jump:control");
      }
      ClientPlayNetworking.send(new BenchmarkPayload(message.toByteArray()));
    } catch (RuntimeException exception) {
      releaseAll(client);
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

  private void sendTrainer(WireMessage message) {
    try {
      trainer.send(message);
    } catch (IOException exception) {
      throw new IllegalStateException("trainer transport failed", exception);
    }
  }

  private void finalizeReplayAsync(Minecraft client) {
    Thread.ofVirtual()
        .name("jump-replay-finalizer")
        .start(
            () -> {
              long deadline = System.currentTimeMillis() + REPLAY_FINALIZATION_TIMEOUT_MILLIS;
              try {
                while (System.currentTimeMillis() < deadline) {
                  Optional<ReplayCaptureCoordinator.Artifact> completed = captures.pollFinalized();
                  if (completed.isPresent()) {
                    ReplayCaptureCoordinator.Artifact artifact = completed.orElseThrow();
                    sendTrainer(
                        envelope()
                            .setCaptureComplete(
                                CaptureComplete.newBuilder()
                                    .setProtocolVersion(PROTOCOL_VERSION)
                                    .setRequestId(artifact.requestId())
                                    .setSessionId(artifact.sessionId())
                                    .setCheckpointId(artifact.checkpointId())
                                    .setEpisodeId(artifact.episodeId())
                                    .setReplayFile(artifact.replayFile().toString())
                                    .setSha256(ByteString.copyFrom(artifact.sha256()))
                                    .setSizeBytes(artifact.sizeBytes()))
                            .build());
                    captures.complete();
                    LOGGER.info(
                        "Finalized replay for {} at {}",
                        artifact.checkpointId(),
                        artifact.replayFile());
                    if (artifact.reconnectMinecraft() && trainer.connected()) {
                      client.execute(() -> connectRecordingClient(client));
                    }
                    return;
                  }
                  Thread.sleep(100L);
                }
                throw new IOException("Replay Mod did not finalize a valid .mcpr before timeout");
              } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                reportCaptureFailure(client, "replay finalization was interrupted");
              } catch (IOException | RuntimeException exception) {
                reportCaptureFailure(client, exception.getMessage());
              }
            });
  }

  private void reportCaptureFailure(Minecraft client, String description) {
    captures.abort();
    recordingEnabled = false;
    client.execute(
        () ->
            sendError(
                client,
                ErrorCode.ERROR_CODE_INTERNAL,
                description == null ? "replay finalization failed" : description,
                false));
  }

  private void connectRecordingClient(Minecraft client) {
    if (mode != ClientMode.CLIENT_MODE_RECORDING
        || !recordingEnabled
        || recordingConnectRequested
        || client.getConnection() != null) {
      return;
    }
    String address = System.getProperty("jump.client.server", "127.0.0.1:25565");
    ServerData server = new ServerData("Jump Benchmark", address, ServerData.Type.OTHER);
    recordingConnectRequested = true;
    LOGGER.info("Replay Mod is ready; connecting recording client to {}", address);
    ConnectScreen.startConnecting(
        new TitleScreen(), client, ServerAddress.parseString(address), server, true, null);
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

  private static ClientMode configuredMode() {
    return switch (System.getProperty("jump.client.mode", "training").toLowerCase(Locale.ROOT)) {
      case "training" -> ClientMode.CLIENT_MODE_TRAINING;
      case "recording" -> ClientMode.CLIENT_MODE_RECORDING;
      default ->
          throw new IllegalArgumentException("jump.client.mode must be training or recording");
    };
  }

  private static Path configuredReplayDirectory() {
    return Path.of(System.getProperty("jump.client.replayDir", "replay_recordings"));
  }

  private String modeName() {
    return mode == ClientMode.CLIENT_MODE_RECORDING ? "recording" : "training";
  }

  private final class TrainerListener implements LoopbackServer.Listener {
    @Override
    public void connected() {
      Minecraft.getInstance()
          .execute(
              () -> {
                Minecraft client = Minecraft.getInstance();
                if (hello != null) {
                  sendTrainerIfConnected(envelope().setConnectionHello(hello).build(), client);
                }
                if (connectionReady != null) {
                  sendTrainerIfConnected(
                      envelope().setConnectionReady(connectionReady).build(), client);
                }
              });
    }

    @Override
    public void message(WireMessage message) {
      Minecraft.getInstance().execute(() -> receiveTrainer(message, Minecraft.getInstance()));
    }

    @Override
    public void disconnected(Throwable cause) {
      Minecraft.getInstance()
          .execute(
              () -> {
                Minecraft client = Minecraft.getInstance();
                releaseAll(client);
                sequencer.abort();
                boolean captureWasActive = captures.active();
                captures.abort();
                recordingEnabled = false;
                if (captureWasActive && client.getConnection() != null) {
                  client
                      .getConnection()
                      .getConnection()
                      .disconnect(
                          net.minecraft.network.chat.Component.literal(
                              "trainer disconnected during replay capture"));
                }
                if (cause != null) {
                  LOGGER.warn("Trainer disconnected: {}", cause.toString());
                }
              });
    }
  }
}
