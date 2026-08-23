package gg.wellplayed.jump.server;

import com.google.protobuf.InvalidProtocolBufferException;
import gg.wellplayed.jump.protocol.v1.ActionApplied;
import gg.wellplayed.jump.protocol.v1.ClientMode;
import gg.wellplayed.jump.protocol.v1.ConnectionHello;
import gg.wellplayed.jump.protocol.v1.ConnectionReady;
import gg.wellplayed.jump.protocol.v1.EpisodePhase;
import gg.wellplayed.jump.protocol.v1.EpisodeReady;
import gg.wellplayed.jump.protocol.v1.EpisodeResult;
import gg.wellplayed.jump.protocol.v1.EpisodeState;
import gg.wellplayed.jump.protocol.v1.ErrorCode;
import gg.wellplayed.jump.protocol.v1.ProtocolError;
import gg.wellplayed.jump.protocol.v1.ResetRequest;
import gg.wellplayed.jump.protocol.v1.Shutdown;
import gg.wellplayed.jump.protocol.v1.TerminalReason;
import gg.wellplayed.jump.protocol.v1.WireMessage;
import gg.wellplayed.jump.server.core.ArenaGeometry;
import gg.wellplayed.jump.server.core.EpisodeController;
import gg.wellplayed.jump.server.core.EpisodeController.ActionStatus;
import gg.wellplayed.jump.server.core.EpisodeController.EndReason;
import gg.wellplayed.jump.server.core.EpisodeController.Phase;
import gg.wellplayed.jump.server.core.EpisodeController.ResetCommand;
import gg.wellplayed.jump.server.core.EpisodeController.ResetStatus;
import gg.wellplayed.jump.server.core.EpisodeController.TickSnapshot;
import gg.wellplayed.jump.server.core.SeededGap;
import io.papermc.paper.event.player.AsyncPlayerSpawnLocationEvent;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.logging.Level;
import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.entity.Entity;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.block.BlockBreakEvent;
import org.bukkit.event.block.BlockPlaceEvent;
import org.bukkit.event.entity.CreatureSpawnEvent;
import org.bukkit.event.entity.EntityDamageEvent;
import org.bukkit.event.entity.FoodLevelChangeEvent;
import org.bukkit.event.player.PlayerChangedWorldEvent;
import org.bukkit.event.player.PlayerDropItemEvent;
import org.bukkit.event.player.PlayerInteractEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.event.player.PlayerRegisterChannelEvent;
import org.bukkit.event.weather.WeatherChangeEvent;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.plugin.messaging.PluginMessageListener;

/** Paper entry point for the authoritative one-block jump benchmark. */
public final class JumpBenchmarkPlugin extends JavaPlugin
    implements PluginMessageListener, Listener {
  static final int PROTOCOL_VERSION = 1;
  static final int MAX_PAYLOAD_BYTES = 1024 * 1024;
  static final String CHANNEL = "jump:control";

  private final ArenaGeometry geometry = ArenaGeometry.STANDARD;
  private final ArenaManager arena = new ArenaManager(geometry);
  private final Map<UUID, PlayerSession> sessions = new HashMap<>();
  private Location initialSpawn;
  private long serverTick;

  @Override
  public void onEnable() {
    getServer().getMessenger().registerIncomingPluginChannel(this, CHANNEL, this);
    getServer().getMessenger().registerOutgoingPluginChannel(this, CHANNEL);
    getServer().getPluginManager().registerEvents(this, this);
    initialSpawn = arena.initialize(getServer().getWorlds().getFirst());
    getServer().getScheduler().runTaskTimer(this, this::tick, 1L, 1L);
    getLogger().info("Jump benchmark enabled (protocol v1, Paper 26.2)");
  }

  @Override
  public void onDisable() {
    for (PlayerSession session : sessions.values()) {
      session.controller.abortInfrastructure();
    }
    sessions.clear();
  }

  @Override
  public void onPluginMessageReceived(String channel, Player player, byte[] data) {
    if (!CHANNEL.equals(channel)) {
      return;
    }
    if (!Bukkit.isPrimaryThread()) {
      byte[] copy = data.clone();
      getServer()
          .getScheduler()
          .runTask(this, () -> onPluginMessageReceived(channel, player, copy));
      return;
    }
    if (data.length > MAX_PAYLOAD_BYTES) {
      sendError(player, null, ErrorCode.ERROR_CODE_INVALID_MESSAGE, "payload exceeds 1 MiB");
      return;
    }

    final WireMessage message;
    try {
      message = WireMessage.parseFrom(data);
    } catch (InvalidProtocolBufferException exception) {
      sendError(player, null, ErrorCode.ERROR_CODE_INVALID_MESSAGE, "invalid protobuf payload");
      return;
    }
    if (message.getProtocolVersion() != PROTOCOL_VERSION) {
      sendError(player, null, ErrorCode.ERROR_CODE_VERSION_MISMATCH, "expected protocol version 1");
      return;
    }

    try {
      switch (message.getPayloadCase()) {
        case CONNECTION_HELLO -> handleHello(player, message.getConnectionHello());
        case RESET_REQUEST -> handleReset(player, message.getResetRequest());
        case ACTION_APPLIED -> handleAction(player, message.getActionApplied());
        case SHUTDOWN -> handleShutdown(player, message.getShutdown());
        default ->
            sendError(
                player,
                sessions.get(player.getUniqueId()),
                ErrorCode.ERROR_CODE_INVALID_MESSAGE,
                "payload is not valid from Fabric to Paper");
      }
    } catch (RuntimeException exception) {
      getLogger().log(Level.SEVERE, "benchmark message failed", exception);
      sendError(
          player,
          sessions.get(player.getUniqueId()),
          ErrorCode.ERROR_CODE_INTERNAL,
          "server failed to process benchmark message");
    }
  }

  private void handleHello(Player player, ConnectionHello hello) {
    getLogger()
        .fine(
            "Received benchmark hello from "
                + player.getName()
                + " for session "
                + hello.getSessionId());
    if (hello.getProtocolVersion() != PROTOCOL_VERSION
        || hello.getSessionId().isBlank()
        || hello.getMode() == ClientMode.CLIENT_MODE_UNSPECIFIED) {
      sendError(player, null, ErrorCode.ERROR_CODE_INVALID_MESSAGE, "invalid connection hello");
      return;
    }
    PlayerSession prior = sessions.get(player.getUniqueId());
    if (prior != null
        && prior.sessionId.equals(hello.getSessionId())
        && prior.mode == hello.getMode()) {
      sendConnectionReady(prior, hello.getClientTick());
      return;
    }
    prior = sessions.remove(player.getUniqueId());
    if (prior != null) {
      prior.controller.abortInfrastructure();
    }
    PlayerSession session = new PlayerSession(player, hello.getSessionId(), hello.getMode());
    sessions.put(player.getUniqueId(), session);
    sendConnectionReady(session, hello.getClientTick());
  }

  @EventHandler
  public void onPlayerRegisterChannel(PlayerRegisterChannelEvent event) {
    if (CHANNEL.equals(event.getChannel())) {
      getLogger().info("Client " + event.getPlayer().getName() + " registered " + CHANNEL);
    }
  }

  private void sendConnectionReady(PlayerSession session, long clientTick) {
    send(
        session.player,
        WireMessage.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setConnectionReady(
                ConnectionReady.newBuilder()
                    .setProtocolVersion(PROTOCOL_VERSION)
                    .setSessionId(session.sessionId)
                    .setMode(session.mode)
                    .setMinecraftVersion("26.2")
                    .setClientTick(clientTick)
                    .setServerTick(serverTick))
            .build());
  }

  private void handleReset(Player player, ResetRequest request) {
    PlayerSession session = requireSession(player, request.getSessionId());
    if (session == null) {
      return;
    }
    if (request.getProtocolVersion() != PROTOCOL_VERSION) {
      sendError(
          player,
          session,
          ErrorCode.ERROR_CODE_VERSION_MISMATCH,
          "reset protocol version mismatch");
      return;
    }

    double gap = SeededGap.fromSeed(request.getSeed());
    ResetCommand command =
        new ResetCommand(
            request.getRequestId(),
            request.getSessionId(),
            request.getEpisodeId(),
            request.getSeed(),
            gap);
    ResetStatus status = session.controller.requestReset(command);
    getLogger()
        .fine(
            "Reset "
                + request.getRequestId()
                + " for episode "
                + request.getEpisodeId()
                + " (seed "
                + request.getSeed()
                + ") is "
                + status.name().toLowerCase());
    switch (status) {
      case ACCEPTED -> {
        session.resetClientTick = request.getClientTick();
        session.cachedReady = null;
        session.expectedSpawnX = arena.prepare(player, gap);
      }
      case IDEMPOTENT -> {
        if (session.cachedReady != null) {
          sendReady(session);
        }
      }
      case STALE_REQUEST ->
          sendError(player, session, ErrorCode.ERROR_CODE_STALE_REQUEST, "stale reset request id");
      case REQUEST_MISMATCH ->
          sendError(
              player,
              session,
              ErrorCode.ERROR_CODE_STALE_REQUEST,
              "request id was reused with different reset data");
      case STALE_EPISODE ->
          sendError(
              player,
              session,
              ErrorCode.ERROR_CODE_STALE_EPISODE,
              "episode id is not newer than the current episode");
    }
  }

  private void handleAction(Player player, ActionApplied action) {
    PlayerSession session = requireSession(player, action.getSessionId());
    if (session == null) {
      return;
    }
    if (action.getEpisodeId() != session.episodeId()) {
      sendError(
          player,
          session,
          ErrorCode.ERROR_CODE_STALE_EPISODE,
          "action episode does not match the active episode");
      return;
    }
    ActionStatus status =
        session.controller.acceptAction(
            action.getObservationSequence(), action.getActionSequence(), serverTick);
    if (status != ActionStatus.ACCEPTED) {
      sendError(
          player,
          session,
          ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION,
          "action rejected: " + status.name().toLowerCase());
      return;
    }
    TickSnapshot snapshot =
        session.controller.tick(serverTick, arena.observe(session.player), geometry);
    sendState(session, snapshot);
    if (snapshot.finishedNow()) {
      sendResult(session, snapshot);
    }
  }

  private void handleShutdown(Player player, Shutdown shutdown) {
    PlayerSession session = requireSession(player, shutdown.getSessionId());
    if (session == null) {
      return;
    }
    TickSnapshot aborted = session.controller.abortInfrastructure();
    sendState(session, aborted);
    if (aborted.finishedNow()) {
      sendResult(session, aborted);
    }
  }

  private void tick() {
    if (!Bukkit.isPrimaryThread()) {
      throw new IllegalStateException("benchmark tick must execute on Paper's main thread");
    }
    serverTick++;
    for (PlayerSession session : sessions.values()) {
      if (!session.player.isOnline()) {
        continue;
      }
      if (session.controller.phase() == Phase.RESETTING) {
        boolean stable = arena.isStable(session.player, session.expectedSpawnX);
        if (session.controller.observeResetStability(
            stable, arena.horizontalSpeedSquared(session.player))) {
          cacheReady(session);
          sendReady(session);
        }
        continue;
      }
      if (session.controller.phase() != Phase.ACTIVE) {
        continue;
      }
      int previousElapsedTicks = session.controller.elapsedTicks();
      TickSnapshot snapshot =
          session.controller.tick(serverTick, arena.observe(session.player), geometry);
      if (snapshot.elapsedTicks() > previousElapsedTicks || snapshot.finishedNow()) {
        sendState(session, snapshot);
      }
      if (snapshot.finishedNow()) {
        sendResult(session, snapshot);
      }
    }
  }

  private void cacheReady(PlayerSession session) {
    ResetCommand command = session.controller.resetCommand();
    session.cachedReady =
        EpisodeReady.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setRequestId(command.requestId())
            .setSessionId(command.sessionId())
            .setEpisodeId(command.episodeId())
            .setSeed(command.seed())
            .setStartingGap(command.startingGap())
            .setWallNearCoordinate(geometry.wallNear())
            .setWallFarCoordinate(geometry.wallFar())
            .setWallMinCrossCoordinate(geometry.laneMinZ())
            .setWallMaxCrossCoordinate(geometry.laneMaxZ() + 1.0)
            .setLaneDirectionX(1.0)
            .setLaneDirectionZ(0.0)
            .setStandingFeetY(geometry.standingFeetY())
            .setClientTick(session.resetClientTick)
            .setInitialServerTick(serverTick)
            .build();
    getLogger()
        .fine(
            "Episode " + command.episodeId() + " is stable and ready at server tick " + serverTick);
  }

  private void sendReady(PlayerSession session) {
    send(
        session.player,
        WireMessage.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setEpisodeReady(session.cachedReady)
            .build());
  }

  private void sendState(PlayerSession session, TickSnapshot snapshot) {
    send(
        session.player,
        WireMessage.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setEpisodeState(
                EpisodeState.newBuilder()
                    .setProtocolVersion(PROTOCOL_VERSION)
                    .setSessionId(session.sessionId)
                    .setEpisodeId(session.episodeId())
                    .setServerTick(serverTick)
                    .setElapsedTicks(snapshot.elapsedTicks())
                    .setPhase(toProtocolPhase(snapshot.phase()))
                    .setTerminalReason(toTerminalReason(snapshot.reason())))
            .build());
  }

  private void sendResult(PlayerSession session, TickSnapshot snapshot) {
    send(
        session.player,
        WireMessage.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setEpisodeResult(
                EpisodeResult.newBuilder()
                    .setProtocolVersion(PROTOCOL_VERSION)
                    .setSessionId(session.sessionId)
                    .setEpisodeId(session.episodeId())
                    .setServerTick(serverTick)
                    .setElapsedTicks(snapshot.elapsedTicks())
                    .setTerminalReason(toTerminalReason(snapshot.reason())))
            .build());
  }

  private PlayerSession requireSession(Player player, String sessionId) {
    PlayerSession session = sessions.get(player.getUniqueId());
    if (session == null || !session.sessionId.equals(sessionId)) {
      sendError(
          player,
          session,
          ErrorCode.ERROR_CODE_STALE_REQUEST,
          "message is not associated with this player's session");
      return null;
    }
    return session;
  }

  private void sendError(Player player, PlayerSession session, ErrorCode code, String description) {
    ProtocolError.Builder error =
        ProtocolError.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setCode(code)
            .setMessage(description)
            .setServerTick(serverTick);
    if (session != null) {
      error.setSessionId(session.sessionId).setEpisodeId(session.episodeId());
      ResetCommand reset = session.controller.resetCommand();
      if (reset != null) {
        error.setRequestId(reset.requestId());
      }
    }
    send(
        player,
        WireMessage.newBuilder().setProtocolVersion(PROTOCOL_VERSION).setError(error).build());
  }

  private void send(Player player, WireMessage message) {
    byte[] bytes = message.toByteArray();
    if (bytes.length > MAX_PAYLOAD_BYTES) {
      throw new IllegalStateException("outgoing benchmark payload exceeds 1 MiB");
    }
    player.sendPluginMessage(this, CHANNEL, bytes);
  }

  private static EpisodePhase toProtocolPhase(Phase phase) {
    return switch (phase) {
      case IDLE -> EpisodePhase.EPISODE_PHASE_UNSPECIFIED;
      case RESETTING -> EpisodePhase.EPISODE_PHASE_RESETTING;
      case READY -> EpisodePhase.EPISODE_PHASE_READY;
      case ACTIVE -> EpisodePhase.EPISODE_PHASE_ACTIVE;
      case TERMINAL -> EpisodePhase.EPISODE_PHASE_TERMINAL;
      case ABORTED -> EpisodePhase.EPISODE_PHASE_ABORTED;
    };
  }

  private static TerminalReason toTerminalReason(EndReason reason) {
    return switch (reason) {
      case NONE -> TerminalReason.TERMINAL_REASON_UNSPECIFIED;
      case SUCCESS -> TerminalReason.TERMINAL_REASON_SUCCESS;
      case MISSED_JUMP -> TerminalReason.TERMINAL_REASON_MISSED_JUMP;
      case TIME_LIMIT -> TerminalReason.TERMINAL_REASON_TIME_LIMIT;
      case INFRASTRUCTURE_ERROR -> TerminalReason.TERMINAL_REASON_INFRASTRUCTURE_ERROR;
    };
  }

  private boolean isBenchmarkPlayer(Entity entity) {
    return entity instanceof Player player && sessions.containsKey(player.getUniqueId());
  }

  @EventHandler
  public void onInitialSpawn(AsyncPlayerSpawnLocationEvent event) {
    Location spawn = initialSpawn;
    if (spawn != null) {
      event.setSpawnLocation(spawn.clone());
    }
  }

  @EventHandler
  public void onDamage(EntityDamageEvent event) {
    if (isBenchmarkPlayer(event.getEntity())) {
      event.setCancelled(true);
    }
  }

  @EventHandler
  public void onFood(FoodLevelChangeEvent event) {
    if (isBenchmarkPlayer(event.getEntity())) {
      event.setCancelled(true);
    }
  }

  @EventHandler
  public void onInteract(PlayerInteractEvent event) {
    if (sessions.containsKey(event.getPlayer().getUniqueId())) {
      event.setCancelled(true);
    }
  }

  @EventHandler
  public void onBreak(BlockBreakEvent event) {
    if (sessions.containsKey(event.getPlayer().getUniqueId())) {
      event.setCancelled(true);
    }
  }

  @EventHandler
  public void onPlace(BlockPlaceEvent event) {
    if (sessions.containsKey(event.getPlayer().getUniqueId())) {
      event.setCancelled(true);
    }
  }

  @EventHandler
  public void onDrop(PlayerDropItemEvent event) {
    if (sessions.containsKey(event.getPlayer().getUniqueId())) {
      event.setCancelled(true);
    }
  }

  @EventHandler
  public void onCreatureSpawn(CreatureSpawnEvent event) {
    event.setCancelled(true);
  }

  @EventHandler
  public void onWeather(WeatherChangeEvent event) {
    if (event.toWeatherState()) {
      event.setCancelled(true);
    }
  }

  @EventHandler
  public void onWorldChange(PlayerChangedWorldEvent event) {
    PlayerSession session = sessions.remove(event.getPlayer().getUniqueId());
    if (session != null) {
      session.controller.abortInfrastructure();
    }
  }

  @EventHandler
  public void onQuit(PlayerQuitEvent event) {
    PlayerSession session = sessions.remove(event.getPlayer().getUniqueId());
    if (session != null) {
      session.controller.abortInfrastructure();
    }
  }

  private static final class PlayerSession {
    private final Player player;
    private final String sessionId;
    private final ClientMode mode;
    private final EpisodeController controller = new EpisodeController();
    private EpisodeReady cachedReady;
    private long resetClientTick;
    private double expectedSpawnX;

    private PlayerSession(Player player, String sessionId, ClientMode mode) {
      this.player = player;
      this.sessionId = sessionId;
      this.mode = mode;
    }

    private long episodeId() {
      ResetCommand command = controller.resetCommand();
      return command == null ? 0 : command.episodeId();
    }
  }
}
