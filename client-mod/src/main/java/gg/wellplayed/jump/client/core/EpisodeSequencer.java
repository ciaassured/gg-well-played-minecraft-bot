package gg.wellplayed.jump.client.core;

import gg.wellplayed.jump.protocol.v1.ActionRequest;
import gg.wellplayed.jump.protocol.v1.EpisodePhase;
import gg.wellplayed.jump.protocol.v1.EpisodeReady;
import gg.wellplayed.jump.protocol.v1.EpisodeResult;
import gg.wellplayed.jump.protocol.v1.EpisodeState;
import gg.wellplayed.jump.protocol.v1.ErrorCode;
import gg.wellplayed.jump.protocol.v1.ResetRequest;
import java.util.Objects;

/** Pure client-side reset, stability, action, and observation sequencer. */
public final class EpisodeSequencer {
  private static final int PROTOCOL_VERSION = 3;
  public static final int STABLE_TICKS_REQUIRED = 2;
  public static final int ACTION_DEADLINE_TICKS = 10;

  public enum Phase {
    IDLE,
    RESETTING,
    STABILIZING,
    WAITING_ACTION,
    ACTION_QUEUED,
    ACTION_APPLIED,
    TERMINAL,
    ABORTED
  }

  public enum ResetStatus {
    ACCEPTED,
    IDEMPOTENT
  }

  private String sessionId = "";
  private ResetRequest reset;
  private EpisodeReady ready;
  private ActionRequest queuedAction;
  private EpisodeState serverState;
  private EpisodeResult serverResult;
  private Phase phase = Phase.IDLE;
  private int stableTicks;
  private long observationSequence;
  private long actionSequence;
  private long observationClientTick;
  private int emittedElapsedTicks;

  public void startSession(String newSessionId) {
    if (newSessionId == null || newSessionId.isBlank()) {
      throw new IllegalArgumentException("session id must not be blank");
    }
    sessionId = newSessionId;
    reset = null;
    ready = null;
    queuedAction = null;
    serverState = null;
    serverResult = null;
    phase = Phase.IDLE;
    stableTicks = 0;
    observationSequence = 0;
    actionSequence = 0;
    observationClientTick = 0;
    emittedElapsedTicks = 0;
  }

  public ResetStatus beginReset(ResetRequest request) throws ProtocolViolation {
    requireVersion(request.getProtocolVersion());
    requireSession(request.getSessionId());
    if (request.getRequestId() == 0 || request.getEpisodeId() == 0) {
      throw violation(ErrorCode.ERROR_CODE_INVALID_MESSAGE, "reset identifiers must be positive");
    }
    if (reset != null && request.getRequestId() < reset.getRequestId()) {
      throw violation(ErrorCode.ERROR_CODE_STALE_REQUEST, "reset request is stale");
    }
    if (reset != null && request.getRequestId() == reset.getRequestId()) {
      if (request.equals(reset)) {
        return ResetStatus.IDEMPOTENT;
      }
      throw violation(
          ErrorCode.ERROR_CODE_STALE_REQUEST, "reset request id was reused with different data");
    }
    if (reset != null && request.getEpisodeId() <= reset.getEpisodeId()) {
      throw violation(ErrorCode.ERROR_CODE_STALE_EPISODE, "episode id is stale");
    }

    this.reset = request;
    ready = null;
    queuedAction = null;
    serverState = null;
    serverResult = null;
    phase = Phase.RESETTING;
    stableTicks = 0;
    observationSequence = 0;
    actionSequence = 0;
    observationClientTick = request.getClientTick();
    emittedElapsedTicks = 0;
    return ResetStatus.ACCEPTED;
  }

  public void receiveReady(EpisodeReady ready) throws ProtocolViolation {
    Objects.requireNonNull(ready, "ready");
    requireVersion(ready.getProtocolVersion());
    requireSession(ready.getSessionId());
    if (reset == null
        || ready.getRequestId() != reset.getRequestId()
        || ready.getEpisodeId() != reset.getEpisodeId()
        || ready.getSeed() != reset.getSeed()) {
      throw violation(ErrorCode.ERROR_CODE_STALE_EPISODE, "episode ready does not match reset");
    }
    if (phase != Phase.RESETTING && phase != Phase.STABILIZING) {
      throw violation(ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION, "episode ready is out of order");
    }
    this.ready = ready;
    phase = Phase.STABILIZING;
    stableTicks = 0;
  }

  /** Returns true exactly once when two matching stable client ticks have been seen. */
  public boolean observeClientStability(boolean matchingAndStable, long clientTick) {
    if (phase != Phase.STABILIZING) {
      return false;
    }
    stableTicks = matchingAndStable ? stableTicks + 1 : 0;
    if (stableTicks < STABLE_TICKS_REQUIRED) {
      return false;
    }
    phase = Phase.WAITING_ACTION;
    observationClientTick = clientTick;
    return true;
  }

  public void queueAction(ActionRequest action) throws ProtocolViolation {
    Objects.requireNonNull(action, "action");
    requireVersion(action.getProtocolVersion());
    requireSession(action.getSessionId());
    requireEpisode(action.getEpisodeId());
    if (phase != Phase.WAITING_ACTION) {
      throw violation(
          ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION, "client is not waiting for an action");
    }
    if (action.getObservationSequence() != observationSequence
        || action.getActionSequence() != actionSequence + 1) {
      throw violation(
          ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION,
          "action does not match the current observation sequence");
    }
    switch (action.getAction()) {
      case ACTION_NOOP, ACTION_JUMP -> {
        // Valid benchmark actions.
      }
      default ->
          throw violation(ErrorCode.ERROR_CODE_INVALID_MESSAGE, "action must be NOOP or JUMP");
    }
    queuedAction = action;
    phase = Phase.ACTION_QUEUED;
  }

  /** Removes the queued action for application on this client tick. */
  public ActionRequest applyQueuedAction() {
    if (phase != Phase.ACTION_QUEUED) {
      return null;
    }
    ActionRequest action = queuedAction;
    queuedAction = null;
    actionSequence = action.getActionSequence();
    serverState = null;
    serverResult = null;
    phase = Phase.ACTION_APPLIED;
    return action;
  }

  public void receiveState(EpisodeState state) throws ProtocolViolation {
    Objects.requireNonNull(state, "state");
    requireVersion(state.getProtocolVersion());
    requireSession(state.getSessionId());
    requireEpisode(state.getEpisodeId());
    if (phase != Phase.ACTION_APPLIED) {
      if (phase == Phase.TERMINAL && state.getPhase() == EpisodePhase.EPISODE_PHASE_TERMINAL) {
        return;
      }
      if (phase == Phase.ABORTED && state.getPhase() == EpisodePhase.EPISODE_PHASE_ABORTED) {
        return;
      }
      throw violation(
          ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION, "episode state has no applied action");
    }
    if (state.getElapsedTicks() < emittedElapsedTicks) {
      throw violation(
          ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION, "server elapsed ticks moved backwards");
    }
    if (serverState == null || state.getServerTick() >= serverState.getServerTick()) {
      serverState = state;
    }
  }

  public void receiveResult(EpisodeResult result) throws ProtocolViolation {
    Objects.requireNonNull(result, "result");
    requireVersion(result.getProtocolVersion());
    requireSession(result.getSessionId());
    requireEpisode(result.getEpisodeId());
    if (phase != Phase.ACTION_APPLIED && phase != Phase.TERMINAL && phase != Phase.ABORTED) {
      throw violation(
          ErrorCode.ERROR_CODE_SEQUENCE_VIOLATION, "episode result has no applied action");
    }
    if (phase == Phase.ABORTED) {
      return;
    }
    serverResult = result;
  }

  public boolean observationDue() {
    if (phase != Phase.ACTION_APPLIED) {
      return false;
    }
    if (serverResult != null) {
      return true;
    }
    return serverState != null && serverState.getElapsedTicks() > emittedElapsedTicks;
  }

  public EpisodeState completeObservation(long clientTick) {
    if (!observationDue()) {
      throw new IllegalStateException("no observation is due");
    }
    EpisodeState state = serverState;
    if (serverResult != null) {
      state =
          EpisodeState.newBuilder()
              .setProtocolVersion(serverResult.getProtocolVersion())
              .setSessionId(serverResult.getSessionId())
              .setEpisodeId(serverResult.getEpisodeId())
              .setServerTick(serverResult.getServerTick())
              .setElapsedTicks(serverResult.getElapsedTicks())
              .setPhase(EpisodePhase.EPISODE_PHASE_TERMINAL)
              .setTerminalReason(serverResult.getTerminalReason())
              .build();
    }
    observationSequence = actionSequence;
    emittedElapsedTicks = state.getElapsedTicks();
    observationClientTick = clientTick;
    phase =
        state.getPhase() == EpisodePhase.EPISODE_PHASE_TERMINAL
            ? Phase.TERMINAL
            : state.getPhase() == EpisodePhase.EPISODE_PHASE_ABORTED
                ? Phase.ABORTED
                : Phase.WAITING_ACTION;
    serverState = null;
    serverResult = null;
    return state;
  }

  public boolean actionTimedOut(long clientTick) {
    return phase == Phase.WAITING_ACTION
        && clientTick - observationClientTick > ACTION_DEADLINE_TICKS;
  }

  public void abort() {
    queuedAction = null;
    serverState = null;
    serverResult = null;
    phase = Phase.ABORTED;
  }

  public String sessionId() {
    return sessionId;
  }

  public ResetRequest reset() {
    return reset;
  }

  public EpisodeReady ready() {
    return ready;
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

  private void requireEpisode(long episodeId) throws ProtocolViolation {
    if (reset == null || reset.getEpisodeId() != episodeId) {
      throw violation(ErrorCode.ERROR_CODE_STALE_EPISODE, "message belongs to another episode");
    }
  }

  private static ProtocolViolation violation(ErrorCode code, String message) {
    return new ProtocolViolation(code, message);
  }
}
