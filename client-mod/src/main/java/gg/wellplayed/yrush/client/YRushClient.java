package gg.wellplayed.yrush.client;

import com.google.protobuf.ByteString;
import gg.wellplayed.yrush.client.core.ActionVector;
import gg.wellplayed.yrush.client.core.ClientConfiguration;
import gg.wellplayed.yrush.client.core.ControlledInputs;
import gg.wellplayed.yrush.client.core.LoopbackServer;
import gg.wellplayed.yrush.client.core.ProtocolViolation;
import gg.wellplayed.yrush.client.core.ReadinessFile;
import gg.wellplayed.yrush.client.core.ReconnectBackoff;
import gg.wellplayed.yrush.client.core.RoundSequencer;
import gg.wellplayed.yrush.client.core.VoxelOrientation;
import gg.wellplayed.yrush.client.core.YRushPacket;
import gg.wellplayed.yrush.protocol.v1.ActionApplied;
import gg.wellplayed.yrush.protocol.v1.ActionRequest;
import gg.wellplayed.yrush.protocol.v1.ConnectionHello;
import gg.wellplayed.yrush.protocol.v1.ConnectionReady;
import gg.wellplayed.yrush.protocol.v1.EpisodeReady;
import gg.wellplayed.yrush.protocol.v1.EpisodeResult;
import gg.wellplayed.yrush.protocol.v1.ErrorCode;
import gg.wellplayed.yrush.protocol.v1.Observation;
import gg.wellplayed.yrush.protocol.v1.ProtocolError;
import gg.wellplayed.yrush.protocol.v1.RoundDirection;
import gg.wellplayed.yrush.protocol.v1.RoundPhase;
import gg.wellplayed.yrush.protocol.v1.WireMessage;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.UUID;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
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
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** Persistent Fabric bridge for one member of the shared YRush client pool. */
public final class YRushClient implements ClientModInitializer {
  private static final Logger LOGGER = LoggerFactory.getLogger("yrush-client");
  private static final int PROTOCOL_VERSION = RoundSequencer.PROTOCOL_VERSION;
  private static final int CONNECT_ATTEMPT_TIMEOUT_TICKS = 600;

  private final ClientConfiguration configuration = ClientConfiguration.fromSystemProperties();
  private final RoundSequencer sequencer = new RoundSequencer();
  private final ControlledInputs inputs = new ControlledInputs();
  private final ReadinessFile readiness = new ReadinessFile(configuration.readinessFile());
  private final ReconnectBackoff reconnectBackoff = new ReconnectBackoff(20, 600);
  private final LoopbackServer trainer =
      new LoopbackServer(
          configuration.trainerBindAddress(), configuration.trainerPort(), new TrainerListener());

  private long clientTick;
  private long nextConnectTick;
  private long connectRequestTick;
  private int reconnectAttempt;
  private int actionTicksRemaining;
  private boolean minecraftConnectRequested;
  private ConnectionHello hello;
  private ConnectionReady ready;

  @Override
  public void onInitializeClient() {
    readiness.remove();
    PayloadTypeRegistry.clientboundPlay().register(YRushStatePayload.TYPE, YRushStatePayload.CODEC);
    ClientPlayNetworking.registerGlobalReceiver(
        YRushStatePayload.TYPE,
        (payload, context) -> receiveYRush(payload.data(), context.client()));
    ClientPlayConnectionEvents.JOIN.register((listener, sender, client) -> joinedPaper(client));
    ClientPlayConnectionEvents.DISCONNECT.register((listener, client) -> disconnectedPaper(client));
    ClientTickEvents.START_CLIENT_TICK.register(this::startTick);
    ClientTickEvents.END_CLIENT_TICK.register(this::endTick);
    ClientLifecycleEvents.CLIENT_STOPPING.register(this::shutdownClient);
    try {
      trainer.start();
    } catch (IOException exception) {
      throw new IllegalStateException("cannot bind the trainer listener", exception);
    }
    LOGGER.info(
        "Trainer listener bound to {}:{}; connecting to {}",
        configuration.trainerBindAddress(),
        configuration.trainerPort(),
        configuration.paperAddress());
    if (Boolean.getBoolean("yrush.client.offline")) {
      LOGGER.info("Offline authentication is enabled for the isolated YRush server");
    }
  }

  private void joinedPaper(Minecraft client) {
    releaseAll(client);
    minecraftConnectRequested = true;
    reconnectAttempt = 0;
    LocalPlayer player = client.player;
    if (player == null) {
      throw new IllegalStateException("Minecraft join completed without a local player");
    }
    readiness.markReady(
        "protocol=1\nplayer_uuid="
            + player.getUUID()
            + "\nplayer_name="
            + player.getName().getString()
            + "\nserver="
            + configuration.paperAddress()
            + "\n");
    LOGGER.info(
        "Joined Paper as {}; listening for {}",
        player.getName().getString(),
        YRushStatePayload.TYPE.id());
    beginTrainerSession(client);
  }

  private void disconnectedPaper(Minecraft client) {
    readiness.remove();
    releaseAll(client);
    minecraftConnectRequested = false;
    sendError(
        ErrorCode.ERROR_CODE_MINECRAFT_DISCONNECTED,
        "Minecraft disconnected from the YRush server",
        true);
    sequencer.abort();
    hello = null;
    ready = null;
    scheduleReconnect("Paper disconnected");
  }

  private void beginTrainerSession(Minecraft client) {
    if (!trainer.connected() || client.player == null || client.getConnection() == null) {
      return;
    }
    if (hello != null && ready != null) {
      return;
    }
    String sessionId = UUID.randomUUID().toString();
    sequencer.startSession(sessionId);
    hello =
        ConnectionHello.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId(sessionId)
            .setClientNonce(UUID.randomUUID().toString())
            .setClientTick(clientTick)
            .build();
    ready =
        ConnectionReady.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId(sessionId)
            .setMinecraftVersion("26.2")
            .setPlayerUuid(client.player.getUUID().toString())
            .setPlayerName(client.player.getName().getString())
            .setClientTick(clientTick)
            .build();
    sendTrainer(envelope().setConnectionHello(hello).build(), client);
    sendTrainer(envelope().setConnectionReady(ready).build(), client);
  }

  private void receiveTrainer(WireMessage message, Minecraft client) {
    if (message.getProtocolVersion() != PROTOCOL_VERSION) {
      protocolFailure(client, ErrorCode.ERROR_CODE_VERSION_MISMATCH, "expected protocol version 1");
      return;
    }
    try {
      switch (message.getPayloadCase()) {
        case ARM_EPISODE -> {
          releaseAll(client);
          sequencer.arm(message.getArmEpisode());
        }
        case ACTION_REQUEST -> sequencer.queueAction(message.getActionRequest());
        case SHUTDOWN -> {
          releaseAll(client);
          sequencer.abort();
        }
        default ->
            throw new ProtocolViolation(
                ErrorCode.ERROR_CODE_INVALID_MESSAGE, "payload is not valid from Python to Fabric");
      }
    } catch (ProtocolViolation exception) {
      protocolFailure(client, exception.code(), exception.getMessage());
    }
  }

  private void receiveYRush(byte[] data, Minecraft client) {
    final String json;
    try {
      json =
          StandardCharsets.UTF_8
              .newDecoder()
              .onMalformedInput(CodingErrorAction.REPORT)
              .onUnmappableCharacter(CodingErrorAction.REPORT)
              .decode(ByteBuffer.wrap(data))
              .toString();
    } catch (CharacterCodingException exception) {
      protocolFailure(client, ErrorCode.ERROR_CODE_INVALID_MESSAGE, "YRush sent invalid UTF-8");
      return;
    }
    try {
      YRushPacket packet = YRushPacket.parse(json);
      RoundSequencer.Event event = sequencer.receive(packet, clientTick);
      switch (event) {
        case EPISODE_STARTED -> startEpisode(client);
        case ELIMINATED, ROUND_COMPLETED -> finishEpisode(client);
        case CLEANED -> releaseAll(client);
        case NONE -> {
          if (!packet.playerActive()) {
            releaseAll(client);
          }
        }
      }
    } catch (ProtocolViolation exception) {
      protocolFailure(client, exception.code(), exception.getMessage());
    }
  }

  private void startEpisode(Minecraft client) {
    releaseAll(client);
    selectMiningTool(client);
    EpisodeReady episodeReady =
        EpisodeReady.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setRequestId(sequencer.activeArm().getRequestId())
            .setSessionId(sequencer.sessionId())
            .setRoundSequence(sequencer.activeArm().getRoundSequence())
            .setPolicyVersion(sequencer.activeArm().getPolicyVersion())
            .setClientTick(clientTick)
            .setDirection(protocolDirection())
            .setTargetY(sequencer.targetY())
            .setActivePlayers(sequencer.activePlayers())
            .setTotalPlayers(sequencer.totalPlayers())
            .setActionHoldTicks(RoundSequencer.ACTION_HOLD_TICKS)
            .build();
    sendTrainer(envelope().setEpisodeReady(episodeReady).build(), client);
    sendObservation(client, RoundPhase.ROUND_PHASE_ACTIVE);
  }

  private void finishEpisode(Minecraft client) throws ProtocolViolation {
    actionTicksRemaining = 0;
    releaseAll(client);
    sendObservation(client, RoundPhase.ROUND_PHASE_COMPLETE);
    EpisodeResult result =
        EpisodeResult.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId(sequencer.sessionId())
            .setRoundSequence(sequencer.activeArm().getRoundSequence())
            .setPolicyVersion(sequencer.activeArm().getPolicyVersion())
            .setClientTick(clientTick)
            .setObservationSequence(sequencer.observationSequence())
            .setOutcome(sequencer.terminalOutcome())
            .setWinnerUuid(sequencer.winnerUuid())
            .setParticipantCount(sequencer.totalPlayers())
            .setCompletionTimeSeconds(
                Math.max(0.0, (clientTick - sequencer.roundStartedClientTick()) / 20.0))
            .setBestRemainingTargetDistance(sequencer.bestRemainingTargetDistance())
            .build();
    sendTrainer(envelope().setEpisodeResult(result).build(), client);
  }

  private void startTick(Minecraft client) {
    clientTick++;
    client.getFramerateLimitTracker().onInputReceived();
    if (minecraftConnectRequested
        && client.getConnection() == null
        && clientTick - connectRequestTick >= CONNECT_ATTEMPT_TIMEOUT_TICKS) {
      minecraftConnectRequested = false;
      scheduleReconnect("Paper connection attempt timed out");
    }
    if (!minecraftConnectRequested
        && client.getConnection() == null
        && clientTick >= nextConnectTick) {
      connectMinecraft(client);
    }
    if (sequencer.actionTimedOut(clientTick)) {
      protocolFailure(
          client, ErrorCode.ERROR_CODE_ACTION_TIMEOUT, "trainer missed action deadline");
      return;
    }

    ActionRequest request = sequencer.beginAction();
    if (request == null) {
      return;
    }
    if (client.player == null || client.getConnection() == null) {
      protocolFailure(
          client, ErrorCode.ERROR_CODE_MINECRAFT_DISCONNECTED, "cannot apply action offline");
      return;
    }
    ActionVector action = ActionVector.fromChoices(request.getActionList());
    inputs.apply(action);
    applyViewDelta(client.player, action);
    actionTicksRemaining = RoundSequencer.ACTION_HOLD_TICKS;
    syncInputs(client);
  }

  private void endTick(Minecraft client) {
    if (client.player == null) {
      releaseAll(client);
      return;
    }
    if (actionTicksRemaining <= 0) {
      return;
    }
    actionTicksRemaining--;
    if (actionTicksRemaining == 0 && sequencer.phase() == RoundSequencer.Phase.ACTION_RUNNING) {
      releaseAll(client);
      ActionRequest request = sequencer.completeAction(clientTick);
      ActionApplied acknowledgement =
          ActionApplied.newBuilder()
              .setProtocolVersion(PROTOCOL_VERSION)
              .setSessionId(sequencer.sessionId())
              .setRoundSequence(request.getRoundSequence())
              .setPolicyVersion(request.getPolicyVersion())
              .setClientTick(clientTick)
              .setObservationSequence(request.getObservationSequence())
              .setActionSequence(request.getActionSequence())
              .addAllAction(request.getActionList())
              .setHoldTicks(RoundSequencer.ACTION_HOLD_TICKS)
              .build();
      sendTrainer(envelope().setActionApplied(acknowledgement).build(), client);
      sendObservation(client, RoundPhase.ROUND_PHASE_ACTIVE);
    }
  }

  private void sendObservation(Minecraft client, RoundPhase phase) {
    if (client.player == null || sequencer.activeArm() == null) {
      protocolFailure(client, ErrorCode.ERROR_CODE_INTERNAL, "observation state is unavailable");
      return;
    }
    LocalPlayer player = client.player;
    AABB box = player.getBoundingBox();
    double feetX = (box.minX + box.maxX) * 0.5;
    double feetY = box.minY;
    double feetZ = (box.minZ + box.maxZ) * 0.5;
    int blockX = floor(feetX);
    int blockY = floor(feetY);
    int blockZ = floor(feetZ);
    VoxelOrientation.Axes axes = VoxelOrientation.fromYaw(player.getYRot());
    Vec3 velocity = player.getDeltaMovement();
    double targetDifference = sequencer.targetY() - feetY;
    sequencer.recordTargetDistance(Math.abs(targetDifference));
    byte[] blocks =
        VoxelOrientation.encode(
            blockX,
            blockY,
            blockZ,
            player.getYRot(),
            (x, y, z) -> blockProperties(player, x, y, z));
    Observation observation =
        Observation.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId(sequencer.sessionId())
            .setRoundSequence(sequencer.activeArm().getRoundSequence())
            .setPolicyVersion(sequencer.activeArm().getPolicyVersion())
            .setClientTick(clientTick)
            .setObservationSequence(sequencer.observationSequence())
            .setActionSequence(sequencer.actionSequence())
            .setPhase(phase)
            .setBlockProperties(ByteString.copyFrom(blocks))
            .setSignedTargetHeightDifference(targetDifference)
            .setForwardVelocity(axes.forwardVelocity(velocity.x, velocity.z))
            .setStrafeVelocity(axes.strafeVelocity(velocity.x, velocity.z))
            .setVerticalVelocity(velocity.y)
            .setFractionalX(feetX - Math.floor(feetX))
            .setFractionalY(feetY - Math.floor(feetY))
            .setFractionalZ(feetZ - Math.floor(feetZ))
            .setGrounded(player.onGround())
            .setRemainingTimeFraction(sequencer.remainingTimeFraction(clientTick))
            .setYawResidualDegrees(axes.yawResidual())
            .setPitchDegrees(player.getXRot())
            .setHealthFraction(
                unitFraction(player.getHealth() / Math.max(1.0F, player.getMaxHealth())))
            .setAirFraction(
                unitFraction(
                    player.getAirSupply() / (double) Math.max(1, player.getMaxAirSupply())))
            .setActivePlayers(sequencer.activePlayers())
            .setTotalPlayers(sequencer.totalPlayers())
            .build();
    sendTrainer(envelope().setObservation(observation).build(), client);
  }

  private static VoxelOrientation.BlockProperties blockProperties(
      LocalPlayer player, int x, int y, int z) {
    BlockPos position = new BlockPos(x, y, z);
    BlockState state = player.level().getBlockState(position);
    String path =
        BuiltInRegistries.BLOCK.getKey(state.getBlock()).getPath().toLowerCase(Locale.ROOT);
    boolean collision = !state.getCollisionShape(player.level(), position).isEmpty();
    boolean fluid = !state.getFluidState().isEmpty();
    boolean hazard =
        path.contains("lava")
            || path.contains("fire")
            || path.contains("cactus")
            || path.contains("magma")
            || path.contains("campfire")
            || path.contains("sweet_berry")
            || path.contains("wither_rose")
            || path.contains("powder_snow");
    boolean breakable =
        !state.isAir() && !fluid && state.getDestroySpeed(player.level(), position) >= 0.0F;
    return new VoxelOrientation.BlockProperties(collision, fluid, hazard, breakable);
  }

  private static void applyViewDelta(LocalPlayer player, ActionVector action) {
    player.setYRot(player.getYRot() + action.yawDelta());
    player.setXRot(Math.max(-90.0F, Math.min(90.0F, player.getXRot() + action.pitchDelta())));
  }

  private static int floor(double value) {
    return (int) Math.floor(value);
  }

  private static double unitFraction(double value) {
    return Math.max(0.0, Math.min(1.0, value));
  }

  private void selectMiningTool(Minecraft client) {
    if (client.player == null) {
      return;
    }
    for (int slot = 0;
        slot < net.minecraft.world.entity.player.Inventory.getSelectionSize();
        slot++) {
      ItemStack stack = client.player.getInventory().getItem(slot);
      String item = BuiltInRegistries.ITEM.getKey(stack.getItem()).getPath();
      if (!stack.isEmpty() && item.endsWith("_pickaxe")) {
        client.player.getInventory().setSelectedSlot(slot);
        return;
      }
    }
  }

  private RoundDirection protocolDirection() {
    return sequencer.direction() == YRushPacket.Direction.UP
        ? RoundDirection.ROUND_DIRECTION_UP
        : RoundDirection.ROUND_DIRECTION_DOWN;
  }

  private void connectMinecraft(Minecraft client) {
    if (minecraftConnectRequested || client.getConnection() != null) {
      return;
    }
    String address = configuration.paperAddress();
    ServerData server = new ServerData("YRush", address, ServerData.Type.OTHER);
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

  private void protocolFailure(Minecraft client, ErrorCode code, String detail) {
    releaseAll(client);
    sendError(code, detail, false);
    sequencer.abort();
  }

  private void sendError(ErrorCode code, String description, boolean retryable) {
    ProtocolError.Builder error =
        ProtocolError.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId(sequencer.sessionId())
            .setClientTick(clientTick)
            .setCode(code)
            .setMessage(description == null ? "unknown client failure" : description)
            .setRetryable(retryable);
    if (sequencer.activeArm() != null) {
      error
          .setRequestId(sequencer.activeArm().getRequestId())
          .setRoundSequence(sequencer.activeArm().getRoundSequence())
          .setPolicyVersion(sequencer.activeArm().getPolicyVersion())
          .setObservationSequence(sequencer.observationSequence())
          .setActionSequence(sequencer.actionSequence());
    }
    sendTrainerWithoutClient(envelope().setError(error).build());
  }

  private void sendTrainer(WireMessage message, Minecraft client) {
    if (!trainer.connected()) {
      releaseAll(client);
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

  private void sendTrainerWithoutClient(WireMessage message) {
    if (!trainer.connected()) {
      return;
    }
    try {
      trainer.send(message);
    } catch (IOException exception) {
      LOGGER.error("Trainer transport failed", exception);
    }
  }

  private void releaseAll(Minecraft client) {
    actionTicksRemaining = 0;
    inputs.releaseAll();
    syncInputs(client);
  }

  private void syncInputs(Minecraft client) {
    if (client.options != null) {
      client.options.keyUp.setDown(inputs.forward());
      client.options.keyDown.setDown(inputs.backward());
      client.options.keyLeft.setDown(inputs.left());
      client.options.keyRight.setDown(inputs.right());
      client.options.keyJump.setDown(inputs.jump());
      client.options.keyAttack.setDown(inputs.attack());
      client.options.keySprint.setDown(inputs.sprint());
    }
  }

  private void shutdownClient(Minecraft client) {
    readiness.remove();
    releaseAll(client);
    sequencer.abort();
    try {
      trainer.close();
    } catch (IOException exception) {
      LOGGER.warn("Could not close trainer listener: {}", exception.toString());
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
                  beginTrainerSession(Minecraft.getInstance());
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
                releaseAll(client);
                sequencer.abort();
                hello = null;
                ready = null;
                if (cause == null) {
                  LOGGER.info("Trainer disconnected; Minecraft remains connected");
                } else {
                  LOGGER.warn("Trainer disconnected: {}", cause.toString());
                }
              });
    }
  }
}
