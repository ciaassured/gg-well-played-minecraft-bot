package gg.wellplayed.jump.client;

import com.google.protobuf.InvalidProtocolBufferException;
import gg.wellplayed.jump.client.core.ControlledInputs;
import gg.wellplayed.jump.client.core.EpisodeSequencer;
import gg.wellplayed.jump.client.core.FramedProtobuf;
import gg.wellplayed.jump.client.core.LoopbackServer;
import gg.wellplayed.jump.client.core.ObservationMath;
import gg.wellplayed.jump.client.core.ProtocolViolation;
import gg.wellplayed.jump.protocol.v1.Action;
import gg.wellplayed.jump.protocol.v1.ActionApplied;
import gg.wellplayed.jump.protocol.v1.ActionRequest;
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
import java.util.Locale;
import java.util.UUID;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.minecraft.client.Minecraft;
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

  private final EpisodeSequencer sequencer = new EpisodeSequencer();
  private final ControlledInputs inputs = new ControlledInputs();
  private final ClientMode mode = configuredMode();
  private final LoopbackServer trainer =
      new LoopbackServer(
          Integer.getInteger("jump.client.port", DEFAULT_PORT), new TrainerListener());

  private long clientTick;
  private ConnectionHello hello;
  private ConnectionReady connectionReady;
  private boolean jumpPressedThisTick;

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
    sendPaper(client, envelope);
    sendTrainerIfConnected(envelope, client);
  }

  private void disconnectedPaper(Minecraft client) {
    releaseAll(client);
    sequencer.abort();
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
        case EPISODE_READY -> sequencer.receiveReady(message.getEpisodeReady());
        case EPISODE_STATE -> sequencer.receiveState(message.getEpisodeState());
        case EPISODE_RESULT -> sequencer.receiveResult(message.getEpisodeResult());
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
    inputs.finishTick();
    syncInputs(client);
    jumpPressedThisTick = false;
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
    sendPaper(client, envelope);
    sendTrainerIfConnected(envelope, client);
  }

  private void endTick(Minecraft client) {
    if (jumpPressedThisTick && client.options != null) {
      inputs.finishTick();
      syncInputs(client);
      jumpPressedThisTick = false;
    }
    if (client.player == null) {
      releaseAll(client);
      return;
    }

    if (sequencer.phase() == EpisodeSequencer.Phase.STABILIZING) {
      EpisodeReady ready = sequencer.ready();
      ObservationMath.Sample sample = sample(client.player, ready);
      Vec3 velocity = client.player.getDeltaMovement();
      boolean stable =
          ObservationMath.resetStateMatches(sample, ready.getStartingGap(), velocity.lengthSqr());
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
      }
      return;
    }

    if (sequencer.observationDue()) {
      EpisodeState state = sequencer.completeObservation(clientTick);
      sendObservation(client, state);
      if (state.getPhase() == EpisodePhase.EPISODE_PHASE_TERMINAL
          || state.getPhase() == EpisodePhase.EPISODE_PHASE_ABORTED) {
        releaseAll(client);
      }
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
                if (cause != null) {
                  LOGGER.warn("Trainer disconnected: {}", cause.toString());
                }
              });
    }
  }
}
