package gg.wellplayed.jump.client;

import com.google.protobuf.InvalidProtocolBufferException;
import gg.wellplayed.jump.client.core.ClientConfiguration;
import gg.wellplayed.jump.client.core.ControlledInputs;
import gg.wellplayed.jump.client.core.EpisodeSequencer;
import gg.wellplayed.jump.client.core.FramedProtobuf;
import gg.wellplayed.jump.client.core.LoopbackServer;
import gg.wellplayed.jump.client.core.ObservationMath;
import gg.wellplayed.jump.client.core.ProtocolViolation;
import gg.wellplayed.jump.client.core.ReadinessFile;
import gg.wellplayed.jump.client.core.ReconnectBackoff;
import gg.wellplayed.jump.protocol.v1.Action;
import gg.wellplayed.jump.protocol.v1.ActionApplied;
import gg.wellplayed.jump.protocol.v1.ActionRequest;
import gg.wellplayed.jump.protocol.v1.ConnectionHello;
import gg.wellplayed.jump.protocol.v1.ConnectionReady;
import gg.wellplayed.jump.protocol.v1.EpisodePhase;
import gg.wellplayed.jump.protocol.v1.EpisodeReady;
import gg.wellplayed.jump.protocol.v1.EpisodeState;
import gg.wellplayed.jump.protocol.v1.ErrorCode;
import gg.wellplayed.jump.protocol.v1.Observation;
import gg.wellplayed.jump.protocol.v1.ProtocolError;
import gg.wellplayed.jump.protocol.v1.Shutdown;
import gg.wellplayed.jump.protocol.v1.TerminalReason;
import gg.wellplayed.jump.protocol.v1.WireMessage;
import java.io.IOException;
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

/** Fabric entry point for one persistent tick-synchronous trainer bridge. */
public final class JumpBenchmarkClient implements ClientModInitializer {
  private static final Logger LOGGER = LoggerFactory.getLogger("jump-benchmark-client");
  private static final int PROTOCOL_VERSION = 3;
  private static final int CONNECT_ATTEMPT_TIMEOUT_TICKS = 600;

  private final ClientConfiguration configuration = ClientConfiguration.fromSystemProperties();
  private final EpisodeSequencer sequencer = new EpisodeSequencer();
  private final ControlledInputs inputs = new ControlledInputs();
  private final ReadinessFile readiness = new ReadinessFile(configuration.readinessFile());
  private final ReconnectBackoff reconnectBackoff = new ReconnectBackoff(20, 600);
  private final LoopbackServer trainer =
      new LoopbackServer(
          configuration.trainerBindAddress(), configuration.trainerPort(), new TrainerListener());

  private long clientTick;
  private long lastCompletedClientTick;
  private long actionAppliedClientTick;
  private long lastHelloAttemptTick;
  private long lastStabilityLogTick;
  private long nextConnectTick;
  private long connectRequestTick;
  private ConnectionHello hello;
  private ConnectionReady connectionReady;
  private WireMessage pendingPaperAction;
  private boolean jumpPressedThisTick;
  private boolean minecraftConnectRequested;
  private int reconnectAttempt;

  @Override
  public void onInitializeClient() {
    readiness.remove();
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
      throw new IllegalStateException("cannot bind the trainer listener", exception);
    }
    LOGGER.info(
        "Trainer listener bound to {}:{}; waiting for Paper protocol acknowledgement",
        configuration.trainerBindAddress(),
        configuration.trainerPort());
    if (Boolean.getBoolean("jump.client.offline")) {
      LOGGER.info("Offline authentication is intentional for the isolated benchmark server");
    }
  }

  private void joinedPaper(Minecraft client) {
    readiness.remove();
    releaseAll(client);
    minecraftConnectRequested = true;
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
    lastHelloAttemptTick = clientTick;
    LOGGER.info(
        "Joined Paper; jump:control sendable={}",
        ClientPlayNetworking.canSend(BenchmarkPayload.TYPE));
    sendPaper(client, envelope().setConnectionHello(hello).build());
  }

  private void disconnectedPaper(Minecraft client) {
    readiness.remove();
    releaseAll(client);
    sequencer.abort();
    minecraftConnectRequested = false;
    sendError(
        client,
        ErrorCode.ERROR_CODE_INTERNAL,
        "Minecraft disconnected from the benchmark server",
        true);
    hello = null;
    connectionReady = null;
    scheduleReconnect("Paper disconnected");
  }

  private void receiveTrainer(WireMessage message, Minecraft client) {
    if (message.getProtocolVersion() != PROTOCOL_VERSION) {
      sendError(
          client, ErrorCode.ERROR_CODE_VERSION_MISMATCH, "expected protocol version 3", false);
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
        case SHUTDOWN -> abortActiveEpisode(client, message.getShutdown().getReason());
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
      paperProtocolFailure(client, "invalid Paper payload size");
      return;
    }
    final WireMessage message;
    try {
      message = WireMessage.parseFrom(data);
    } catch (InvalidProtocolBufferException exception) {
      paperProtocolFailure(client, "Paper sent invalid protobuf");
      return;
    }
    if (message.getProtocolVersion() != PROTOCOL_VERSION) {
      paperProtocolFailure(client, "Paper protocol mismatch");
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
          reconnectAttempt = 0;
          readiness.markReady(
              "protocol=3\nsession="
                  + ready.getSessionId()
                  + "\nserver="
                  + configuration.paperAddress()
                  + "\n");
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
          EpisodeSequencer.Phase previousPhase = sequencer.phase();
          sequencer.receiveState(message.getEpisodeState());
          if (previousPhase == EpisodeSequencer.Phase.WAITING_ACTION
              && sequencer.phase() == EpisodeSequencer.Phase.ABORTED) {
            releaseAll(client);
            sendError(
                client,
                ErrorCode.ERROR_CODE_ACTION_TIMEOUT,
                "benchmark action deadline elapsed",
                true);
          } else {
            emitObservationIfActionTickComplete(client);
          }
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
          readiness.remove();
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
    client.getFramerateLimitTracker().onInputReceived();
    if (minecraftConnectRequested
        && client.getConnection() == null
        && hello == null
        && clientTick - connectRequestTick >= CONNECT_ATTEMPT_TIMEOUT_TICKS) {
      minecraftConnectRequested = false;
      scheduleReconnect("Paper connection attempt timed out");
    }
    if (!minecraftConnectRequested
        && client.getConnection() == null
        && clientTick >= nextConnectTick) {
      connectMinecraft(client);
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
    WireMessage completedAction = envelope().setActionApplied(applied).build();
    pendingPaperAction = completedAction;
    sendTrainerIfConnected(completedAction, client);
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

  private void sendHandshakeIfReady(Minecraft client) {
    if (hello == null || connectionReady == null) {
      return;
    }
    sendTrainerIfConnected(envelope().setConnectionHello(hello).build(), client);
    sendTrainerIfConnected(envelope().setConnectionReady(connectionReady).build(), client);
  }

  private void abortActiveEpisode(Minecraft client, String reason) {
    releaseAll(client);
    EpisodeSequencer.Phase phase = sequencer.phase();
    boolean active =
        phase != EpisodeSequencer.Phase.IDLE
            && phase != EpisodeSequencer.Phase.TERMINAL
            && phase != EpisodeSequencer.Phase.ABORTED;
    if (active && client.getConnection() != null && hello != null) {
      Shutdown.Builder shutdown =
          Shutdown.newBuilder()
              .setProtocolVersion(PROTOCOL_VERSION)
              .setRequestId(Math.max(1, clientTick))
              .setSessionId(sequencer.sessionId())
              .setReason(reason == null || reason.isBlank() ? "trainer disconnected" : reason);
      if (sequencer.reset() != null) {
        shutdown.setEpisodeId(sequencer.reset().getEpisodeId());
      }
      sendPaper(client, envelope().setShutdown(shutdown).build());
    }
    if (active) {
      sequencer.abort();
    }
  }

  private void connectMinecraft(Minecraft client) {
    if (minecraftConnectRequested || client.getConnection() != null) {
      return;
    }
    String address = configuration.paperAddress();
    ServerData server = new ServerData("Jump Benchmark", address, ServerData.Type.OTHER);
    minecraftConnectRequested = true;
    connectRequestTick = clientTick;
    LOGGER.info("Connecting the persistent client to {}", address);
    ConnectScreen.startConnecting(
        new TitleScreen(), client, ServerAddress.parseString(address), server, true, null);
  }

  private void scheduleReconnect(String detail) {
    long delay = reconnectBackoff.delayTicks(reconnectAttempt++);
    nextConnectTick = clientTick + delay;
    LOGGER.warn("{}; reconnecting in {} ticks", detail, delay);
  }

  private void paperProtocolFailure(Minecraft client, String detail) {
    readiness.remove();
    releaseAll(client);
    sequencer.abort();
    sendError(client, ErrorCode.ERROR_CODE_INVALID_MESSAGE, detail, false);
  }

  private void sendPaper(Minecraft client, WireMessage message) {
    try {
      if (client.getConnection() == null || !ClientPlayNetworking.canSend(BenchmarkPayload.TYPE)) {
        throw new IllegalStateException("Paper does not accept jump:control");
      }
      ClientPlayNetworking.send(new BenchmarkPayload(message.toByteArray()));
    } catch (RuntimeException exception) {
      readiness.remove();
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

  private final class TrainerListener implements LoopbackServer.Listener {
    @Override
    public void connected(long connectionId) {
      Minecraft.getInstance()
          .execute(
              () -> {
                if (trainer.connectionId() == connectionId) {
                  sendHandshakeIfReady(Minecraft.getInstance());
                }
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
      Minecraft.getInstance()
          .execute(
              () -> {
                Minecraft client = Minecraft.getInstance();
                abortActiveEpisode(client, "trainer disconnected");
                if (cause == null) {
                  LOGGER.info("Trainer disconnected; Minecraft remains connected");
                } else {
                  LOGGER.warn("Trainer disconnected: {}", cause.toString());
                }
              });
    }
  }
}
