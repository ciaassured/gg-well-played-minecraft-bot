package gg.wellplayed.yrush.client.core;

import gg.wellplayed.yrush.protocol.v1.ActionRequest;
import gg.wellplayed.yrush.protocol.v1.ArmEpisode;
import gg.wellplayed.yrush.protocol.v1.ErrorCode;
import gg.wellplayed.yrush.protocol.v1.PlayerOutcome;
import java.util.Objects;

/** Pure ordering state for arms, YRush lifecycle packets, actions, and cleanup. */
public final class RoundSequencer {
  public static final int PROTOCOL_VERSION = 1;
  public static final int ACTION_HOLD_TICKS = 4;
  public static final int ACTION_DEADLINE_TICKS = 200;

  public enum Phase {
    IDLE,
    ARMED,
    COUNTDOWN,
    WAITING_ACTION,
    ACTION_QUEUED,
    ACTION_RUNNING,
    TERMINAL,
    SKIPPING,
    ABORTED
  }

  public enum Event {
    NONE,
    EPISODE_STARTED,
    ELIMINATED,
    ROUND_COMPLETED,
    CLEANED
  }

  public enum ArmStatus {
    ACCEPTED,
    IDEMPOTENT
  }

  private String sessionId = "";
  private ArmEpisode pendingArm;
  private ArmEpisode activeArm;
  private ArmEpisode completedArm;
  private ActionRequest queuedAction;
  private ActionRequest runningAction;
  private YRushPacket latestPacket;
  private YRushPacket.Direction direction;
  private int targetY;
  private int activePlayers;
  private int totalPlayers;
  private long observationSequence;
  private long actionSequence;
  private long observationClientTick;
  private long roundStartedClientTick;
  private long packetClientTick;
  private long completedObservationSequence;
  private long completedActionSequence;
  private long initialRoundSeconds = 1;
  private double bestRemainingTargetDistance = Double.POSITIVE_INFINITY;
  private boolean countdownSeen;
  private boolean skipPendingUntilInactive;
  private boolean terminalReported;
  private boolean terminalRaceActionConsumed;
  private Phase phase = Phase.IDLE;

  /** Starts a new trainer session while retaining the latest server lifecycle phase. */
  public void startSession(String newSessionId) {
    if (newSessionId == null || newSessionId.isBlank()) {
      throw new IllegalArgumentException("session id must not be blank");
    }
    sessionId = newSessionId;
    pendingArm = null;
    activeArm = null;
    completedArm = null;
    queuedAction = null;
    runningAction = null;
    observationSequence = 0;
    actionSequence = 0;
    terminalReported = false;
    terminalRaceActionConsumed = false;
    countdownSeen = false;
    skipPendingUntilInactive = false;
    phase = Phase.IDLE;
  }

  public ArmStatus arm(ArmEpisode arm) throws ProtocolViolation {
    Objects.requireNonNull(arm, "arm");
    requireVersion(arm.getProtocolVersion());
    requireSession(arm.getSessionId());
    if (arm.getRequestId() == 0 || arm.getRoundSequence() == 0) {
      throw violation(ErrorCode.ERROR_CODE_INVALID_MESSAGE, "arm identifiers must be positive");
    }
    if (pendingArm != null) {
      if (pendingArm.equals(arm)) {
        return ArmStatus.IDEMPOTENT;
      }
      if (arm.getRequestId() <= pendingArm.getRequestId()
          || arm.getRoundSequence() <= pendingArm.getRoundSequence()) {
        throw violation(ErrorCode.ERROR_CODE_STALE_REQUEST, "arm request is stale");
      }
      throw violation(ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION, "an episode is already armed");
    }
    if (activeArm != null && !terminalReported) {
      throw violation(ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION, "current episode is still active");
    }
    long previousSequence =
        activeArm != null
            ? activeArm.getRoundSequence()
            : completedArm == null ? 0 : completedArm.getRoundSequence();
    if (arm.getRoundSequence() <= previousSequence) {
      throw violation(ErrorCode.ERROR_CODE_STALE_ROUND, "round sequence is stale");
    }
    pendingArm = arm;
    YRushPacket.Phase serverPhase = latestPacket == null ? null : latestPacket.phase();
    if (serverPhase == YRushPacket.Phase.ACTIVE
        || serverPhase == YRushPacket.Phase.ROUND_COMPLETE) {
      skipPendingUntilInactive = true;
      phase = Phase.SKIPPING;
    } else if (serverPhase == YRushPacket.Phase.LOCKED_COUNTDOWN) {
      countdownSeen = true;
      phase = Phase.COUNTDOWN;
    } else {
      phase = Phase.ARMED;
    }
    return ArmStatus.ACCEPTED;
  }

  public Event receive(YRushPacket packet, long clientTick) throws ProtocolViolation {
    Objects.requireNonNull(packet, "packet");
    YRushPacket.Phase previous = latestPacket == null ? null : latestPacket.phase();
    latestPacket = packet;
    packetClientTick = clientTick;
    if (packet.direction() != null) {
      direction = packet.direction();
      targetY = packet.targetY();
      activePlayers = packet.activePlayers();
      totalPlayers = packet.totalPlayers();
    }

    return switch (packet.phase()) {
      case INACTIVE -> receiveInactive(previous);
      case LOCKED_COUNTDOWN -> receiveCountdown(previous);
      case ACTIVE -> receiveActive(previous, packet, clientTick);
      case ROUND_COMPLETE -> receiveComplete(previous, packet);
    };
  }

  private Event receiveInactive(YRushPacket.Phase previous) throws ProtocolViolation {
    if (previous == YRushPacket.Phase.ACTIVE && activeArm != null && !terminalReported) {
      throw violation(
          ErrorCode.ERROR_CODE_ROUND_ORDERING, "INACTIVE arrived without ROUND_COMPLETE");
    }
    boolean cleaned = previous == YRushPacket.Phase.ROUND_COMPLETE || terminalReported;
    if (terminalReported) {
      activeArm = null;
      terminalReported = false;
    }
    queuedAction = null;
    runningAction = null;
    countdownSeen = false;
    skipPendingUntilInactive = false;
    phase = pendingArm == null ? Phase.IDLE : Phase.ARMED;
    return cleaned ? Event.CLEANED : Event.NONE;
  }

  private Event receiveCountdown(YRushPacket.Phase previous) throws ProtocolViolation {
    if (previous == YRushPacket.Phase.ACTIVE || previous == YRushPacket.Phase.ROUND_COMPLETE) {
      throw violation(
          ErrorCode.ERROR_CODE_ROUND_ORDERING,
          "LOCKED_COUNTDOWN must follow INACTIVE, not " + previous);
    }
    if (pendingArm != null && !skipPendingUntilInactive) {
      countdownSeen = true;
      phase = Phase.COUNTDOWN;
    }
    return Event.NONE;
  }

  private Event receiveActive(YRushPacket.Phase previous, YRushPacket packet, long clientTick)
      throws ProtocolViolation {
    if (activeArm != null) {
      if (!packet.playerActive() && !terminalReported) {
        markTerminal();
        return Event.ELIMINATED;
      }
      return Event.NONE;
    }
    if (pendingArm == null
        || skipPendingUntilInactive
        || previous != YRushPacket.Phase.LOCKED_COUNTDOWN
        || !countdownSeen) {
      skipPendingUntilInactive = true;
      phase = Phase.SKIPPING;
      return Event.NONE;
    }
    if (!packet.playerActive()) {
      throw violation(ErrorCode.ERROR_CODE_ROUND_ORDERING, "new participant is already inactive");
    }
    activeArm = pendingArm;
    pendingArm = null;
    observationSequence = 0;
    actionSequence = 0;
    observationClientTick = clientTick;
    roundStartedClientTick = clientTick;
    initialRoundSeconds = Math.max(1L, packet.secondsRemaining());
    bestRemainingTargetDistance = Double.POSITIVE_INFINITY;
    terminalReported = false;
    phase = Phase.WAITING_ACTION;
    return Event.EPISODE_STARTED;
  }

  private Event receiveComplete(YRushPacket.Phase previous, YRushPacket packet)
      throws ProtocolViolation {
    if (previous != YRushPacket.Phase.ACTIVE && previous != YRushPacket.Phase.ROUND_COMPLETE) {
      throw violation(
          ErrorCode.ERROR_CODE_ROUND_ORDERING, "ROUND_COMPLETE arrived without an active round");
    }
    if (activeArm == null) {
      phase = Phase.SKIPPING;
      return Event.NONE;
    }
    if (terminalReported) {
      if (packet.playerOutcome() != YRushPacket.Outcome.ELIMINATED) {
        throw violation(
            ErrorCode.ERROR_CODE_ROUND_ORDERING,
            "eliminated player received a conflicting final outcome");
      }
      return Event.NONE;
    }
    markTerminal();
    return Event.ROUND_COMPLETED;
  }

  public void queueAction(ActionRequest action) throws ProtocolViolation {
    Objects.requireNonNull(action, "action");
    requireVersion(action.getProtocolVersion());
    requireSession(action.getSessionId());
    try {
      ActionVector.fromChoices(action.getActionList());
    } catch (IllegalArgumentException exception) {
      throw violation(ErrorCode.ERROR_CODE_INVALID_MESSAGE, exception.getMessage());
    }
    if (completedArm != null
        && action.getRoundSequence() == completedArm.getRoundSequence()
        && action.getPolicyVersion() == completedArm.getPolicyVersion()) {
      if (!terminalRaceActionConsumed
          && action.getObservationSequence() == completedObservationSequence
          && action.getActionSequence() == completedActionSequence + 1) {
        // The round ended while this request was in flight. The terminal observation/result
        // already tells the trainer that no complete four-tick transition was produced.
        terminalRaceActionConsumed = true;
        return;
      }
      throw violation(
          ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION,
          "terminal action does not match the final observation sequence");
    }
    if (activeArm == null
        || action.getRoundSequence() != activeArm.getRoundSequence()
        || action.getPolicyVersion() != activeArm.getPolicyVersion()) {
      throw violation(
          ErrorCode.ERROR_CODE_STALE_ROUND, "action belongs to another round or policy");
    }
    if (phase != Phase.WAITING_ACTION) {
      throw violation(ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION, "client is not awaiting an action");
    }
    if (action.getObservationSequence() != observationSequence
        || action.getActionSequence() != actionSequence + 1) {
      throw violation(
          ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION,
          "action does not match the current observation sequence");
    }
    queuedAction = action;
    phase = Phase.ACTION_QUEUED;
  }

  public ActionRequest beginAction() {
    if (phase != Phase.ACTION_QUEUED) {
      return null;
    }
    ActionRequest action = queuedAction;
    queuedAction = null;
    runningAction = action;
    phase = Phase.ACTION_RUNNING;
    return action;
  }

  public ActionRequest completeAction(long clientTick) {
    if (phase != Phase.ACTION_RUNNING || runningAction == null) {
      throw new IllegalStateException("no action is running");
    }
    ActionRequest completed = runningAction;
    runningAction = null;
    actionSequence++;
    observationSequence = actionSequence;
    observationClientTick = clientTick;
    phase = Phase.WAITING_ACTION;
    return completed;
  }

  public boolean actionTimedOut(long clientTick) {
    return phase == Phase.WAITING_ACTION
        && clientTick - observationClientTick > ACTION_DEADLINE_TICKS;
  }

  public void recordTargetDistance(double distance) {
    if (Double.isFinite(distance) && distance >= 0.0) {
      bestRemainingTargetDistance = Math.min(bestRemainingTargetDistance, distance);
    }
  }

  public double remainingTimeFraction(long clientTick) {
    if (latestPacket == null || latestPacket.secondsRemaining() == null) {
      return 0.0;
    }
    double ticksRemaining =
        latestPacket.secondsRemaining() * 20.0 - (clientTick - packetClientTick);
    return Math.max(0.0, Math.min(1.0, ticksRemaining / (initialRoundSeconds * 20.0)));
  }

  public PlayerOutcome terminalOutcome() throws ProtocolViolation {
    if (!terminalReported || latestPacket == null) {
      throw violation(ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION, "episode is not terminal");
    }
    if (latestPacket.phase() == YRushPacket.Phase.ACTIVE && !latestPacket.playerActive()) {
      return PlayerOutcome.PLAYER_OUTCOME_ELIMINATED;
    }
    return switch (latestPacket.playerOutcome()) {
      case WON -> PlayerOutcome.PLAYER_OUTCOME_WON;
      case LOST -> PlayerOutcome.PLAYER_OUTCOME_LOST;
      case ELIMINATED -> PlayerOutcome.PLAYER_OUTCOME_ELIMINATED;
      case DRAW -> PlayerOutcome.PLAYER_OUTCOME_DRAW;
      case STOPPED -> PlayerOutcome.PLAYER_OUTCOME_STOPPED;
      case null ->
          throw violation(
              ErrorCode.ERROR_CODE_INVALID_MESSAGE, "terminal packet has no player outcome");
    };
  }

  public void abort() {
    pendingArm = null;
    activeArm = null;
    completedArm = null;
    queuedAction = null;
    runningAction = null;
    phase = Phase.ABORTED;
  }

  private void markTerminal() {
    completedArm = activeArm;
    completedObservationSequence = observationSequence;
    completedActionSequence = actionSequence;
    terminalRaceActionConsumed = queuedAction != null || runningAction != null;
    queuedAction = null;
    runningAction = null;
    terminalReported = true;
    phase = Phase.TERMINAL;
  }

  public String sessionId() {
    return sessionId;
  }

  public ArmEpisode activeArm() {
    return activeArm;
  }

  public Phase phase() {
    return phase;
  }

  public long observationSequence() {
    return observationSequence;
  }

  public long actionSequence() {
    return actionSequence;
  }

  public YRushPacket.Direction direction() {
    return direction;
  }

  public int targetY() {
    return targetY;
  }

  public int activePlayers() {
    return activePlayers;
  }

  public int totalPlayers() {
    return totalPlayers;
  }

  public long roundStartedClientTick() {
    return roundStartedClientTick;
  }

  public double bestRemainingTargetDistance() {
    return Double.isFinite(bestRemainingTargetDistance) ? bestRemainingTargetDistance : 0.0;
  }

  public String winnerUuid() {
    return latestPacket == null || latestPacket.winnerUuid() == null
        ? ""
        : latestPacket.winnerUuid();
  }

  private void requireVersion(int version) throws ProtocolViolation {
    if (version != PROTOCOL_VERSION) {
      throw violation(
          ErrorCode.ERROR_CODE_VERSION_MISMATCH, "expected protocol version " + PROTOCOL_VERSION);
    }
  }

  private void requireSession(String candidate) throws ProtocolViolation {
    if (!sessionId.equals(candidate)) {
      throw violation(ErrorCode.ERROR_CODE_STALE_REQUEST, "message belongs to another session");
    }
  }

  private static ProtocolViolation violation(ErrorCode code, String message) {
    return new ProtocolViolation(code, message);
  }
}
