package gg.wellplayed.jump.client.core;

import gg.wellplayed.jump.protocol.v1.CommandFinalize;
import gg.wellplayed.jump.protocol.v1.EpisodeRecordingStatus;
import gg.wellplayed.jump.protocol.v1.ErrorCode;
import gg.wellplayed.jump.protocol.v1.ResetRequest;
import gg.wellplayed.jump.protocol.v1.RetentionAcknowledgement;
import gg.wellplayed.jump.protocol.v1.TerminalReason;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/** Pure episode-marker state and finalized Replay Mod staging-file coordination. */
public final class EpisodeRecordingCoordinator {
  public static final int PROTOCOL_VERSION = 2;
  public static final String START_CUT_MARKER = "_RM_START_CUT";
  public static final String END_CUT_MARKER = "_RM_END_CUT";
  public static final String SPLIT_MARKER = "_RM_SPLIT";
  public static final String EPISODE_MARKER_PREFIX = "JUMP_EPISODE_V2_";

  private static final Pattern EPISODE_MARKER_PATTERN =
      Pattern.compile("JUMP_EPISODE_V2_(\\d+)_(\\d+)_(\\d+)");
  private static final int REQUIRED_STABLE_POLLS = 2;

  public interface MarkerWriter {
    long currentDurationMillis() throws IOException;

    void addMarker(String name, int timeMillis) throws IOException;
  }

  public enum BeginStatus {
    ACCEPTED,
    IDEMPOTENT
  }

  public record Episode(
      int ordinal,
      long episodeId,
      long seed,
      EpisodeRecordingStatus recordingStatus,
      TerminalReason terminalReason) {}

  public record Artifact(
      long requestId,
      String sessionId,
      int ordinal,
      long episodeId,
      long seed,
      EpisodeRecordingStatus recordingStatus,
      TerminalReason terminalReason,
      Path stagingPath,
      long sizeBytes,
      byte[] sha256) {
    public Artifact {
      sha256 = sha256.clone();
    }

    @Override
    public byte[] sha256() {
      return sha256.clone();
    }
  }

  public record Acknowledgement(boolean retained, String detail) {}

  private record Fingerprint(long size, long modifiedMillis) {}

  private static final class MutableEpisode {
    private final int ordinal;
    private final long episodeId;
    private final long seed;
    private EpisodeRecordingStatus status =
        EpisodeRecordingStatus.EPISODE_RECORDING_STATUS_UNSPECIFIED;
    private TerminalReason reason = TerminalReason.TERMINAL_REASON_UNSPECIFIED;

    private MutableEpisode(int ordinal, long episodeId, long seed) {
      this.ordinal = ordinal;
      this.episodeId = episodeId;
      this.seed = seed;
    }

    private Episode immutable() {
      return new Episode(ordinal, episodeId, seed, status, reason);
    }
  }

  private final Path replayDirectory;
  private final List<MutableEpisode> episodes = new ArrayList<>();
  private final List<String> warnings = new ArrayList<>();
  private final Map<Path, Fingerprint> initialFiles = new HashMap<>();
  private final Map<Path, Fingerprint> stableFingerprints = new HashMap<>();
  private final Map<Path, Integer> stablePolls = new HashMap<>();

  private String sessionId = "";
  private MutableEpisode active;
  private CommandFinalize finalization;
  private Artifact outstanding;
  private Acknowledgement acknowledgement;
  private int lastMarkerTime;

  public EpisodeRecordingCoordinator(Path replayDirectory) {
    this.replayDirectory = replayDirectory.toAbsolutePath().normalize();
  }

  public synchronized void beginSession(String newSessionId, MarkerWriter markers)
      throws IOException, ProtocolViolation {
    if (newSessionId.isBlank()) {
      throw violation("recording session id is blank");
    }
    if (finalization != null) {
      throw violation("cannot begin a recording session while finalization is active");
    }
    Files.createDirectories(replayDirectory);
    sessionId = newSessionId;
    episodes.clear();
    warnings.clear();
    initialFiles.clear();
    initialFiles.putAll(snapshotReplayFiles());
    stableFingerprints.clear();
    stablePolls.clear();
    active = null;
    outstanding = null;
    acknowledgement = null;
    lastMarkerTime = 0;
    markers.addMarker(START_CUT_MARKER, 0);
  }

  public synchronized BeginStatus beginEpisode(ResetRequest request, MarkerWriter markers)
      throws IOException, ProtocolViolation {
    validateReset(request);
    if (active != null && active.episodeId == request.getEpisodeId()) {
      if (active.seed == request.getSeed()) {
        return BeginStatus.IDEMPOTENT;
      }
      throw violation("episode id was reused with a different seed");
    }
    if (episodes.stream().anyMatch(episode -> episode.episodeId == request.getEpisodeId())) {
      throw violation("episode id is not newer than the current recording");
    }
    if (active != null) {
      closeActive(
          EpisodeRecordingStatus.EPISODE_RECORDING_STATUS_PARTIAL,
          TerminalReason.TERMINAL_REASON_INFRASTRUCTURE_ERROR,
          markers);
    }
    MutableEpisode episode =
        new MutableEpisode(episodes.size(), request.getEpisodeId(), request.getSeed());
    markers.addMarker(END_CUT_MARKER, nextMarkerTime(markers));
    markers.addMarker(markerName(episode), nextMarkerTime(markers));
    episodes.add(episode);
    active = episode;
    return BeginStatus.ACCEPTED;
  }

  public synchronized void completeEpisode(
      long episodeId, TerminalReason reason, MarkerWriter markers)
      throws IOException, ProtocolViolation {
    if (active == null || active.episodeId != episodeId) {
      throw violation("terminal episode does not match the active recording");
    }
    if (reason == TerminalReason.TERMINAL_REASON_UNSPECIFIED
        || reason == TerminalReason.TERMINAL_REASON_INFRASTRUCTURE_ERROR) {
      throw violation("completed recording has no benchmark terminal reason");
    }
    closeActive(EpisodeRecordingStatus.EPISODE_RECORDING_STATUS_COMPLETE, reason, markers);
  }

  public synchronized void interruptActive(MarkerWriter markers)
      throws IOException, ProtocolViolation {
    if (active != null) {
      closeActive(
          EpisodeRecordingStatus.EPISODE_RECORDING_STATUS_PARTIAL,
          TerminalReason.TERMINAL_REASON_INFRASTRUCTURE_ERROR,
          markers);
    }
  }

  public synchronized int beginFinalization(CommandFinalize request, MarkerWriter markers)
      throws IOException, ProtocolViolation {
    if (finalization != null) {
      if (finalization.equals(request)) {
        return episodes.size();
      }
      throw violation("another command finalization is active");
    }
    if (request.getProtocolVersion() != PROTOCOL_VERSION
        || request.getRequestId() == 0
        || !sessionId.equals(request.getSessionId())
        || request.getReason().isBlank()
        || request.getTransferTimeoutSeconds() == 0) {
      throw violation("invalid command finalization request");
    }
    long activeEpisodeId = active == null ? 0 : active.episodeId;
    if (request.getActiveEpisodeId() != activeEpisodeId) {
      throw violation("command finalization does not identify the active episode");
    }
    interruptActive(markers);
    finalization = request;
    return episodes.size();
  }

  public synchronized int beginFinalizationBestEffort(CommandFinalize request)
      throws ProtocolViolation {
    validateFinalizationRequest(request);
    if (active != null) {
      active.status = EpisodeRecordingStatus.EPISODE_RECORDING_STATUS_PARTIAL;
      active.reason = TerminalReason.TERMINAL_REASON_INFRASTRUCTURE_ERROR;
      active = null;
    }
    finalization = request;
    return episodes.size();
  }

  public synchronized void beginUnexpectedFinalization(MarkerWriter markers)
      throws IOException, ProtocolViolation {
    if (finalization != null) {
      return;
    }
    interruptActive(markers);
    finalization =
        CommandFinalize.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId(sessionId)
            .setReason("trainer disconnected unexpectedly")
            .setInterrupted(true)
            .setTransferTimeoutSeconds(300)
            .build();
  }

  public synchronized void beginUnexpectedFinalizationBestEffort() {
    if (finalization != null) {
      return;
    }
    if (active != null) {
      active.status = EpisodeRecordingStatus.EPISODE_RECORDING_STATUS_PARTIAL;
      active.reason = TerminalReason.TERMINAL_REASON_INFRASTRUCTURE_ERROR;
      active = null;
    }
    finalization =
        CommandFinalize.newBuilder()
            .setProtocolVersion(PROTOCOL_VERSION)
            .setSessionId(sessionId)
            .setReason("trainer disconnected unexpectedly")
            .setInterrupted(true)
            .setTransferTimeoutSeconds(300)
            .build();
  }

  public synchronized boolean finalizing() {
    return finalization != null;
  }

  public synchronized int expectedArtifactCount() {
    return episodes.size();
  }

  public synchronized long finalizationRequestId() {
    return finalization == null ? 0 : finalization.getRequestId();
  }

  public synchronized String sessionId() {
    return sessionId;
  }

  public synchronized long transferTimeoutMillis(long configuredMaximumMillis) {
    if (finalization == null) {
      return configuredMaximumMillis;
    }
    long requested = Math.multiplyExact((long) finalization.getTransferTimeoutSeconds(), 1_000L);
    return Math.min(requested, configuredMaximumMillis);
  }

  public synchronized List<Episode> episodeSnapshot() {
    return episodes.stream().map(MutableEpisode::immutable).toList();
  }

  public synchronized Optional<List<Artifact>> pollFinalizedArtifacts() throws IOException {
    if (finalization == null) {
      return Optional.empty();
    }
    Map<Long, Path> candidates = new HashMap<>();
    for (Path path : currentOutputFiles()) {
      List<EpisodeMarker> markers = readEpisodeMarkers(path);
      if (markers.size() != 1 || !isValidReplay(path)) {
        continue;
      }
      EpisodeMarker marker = markers.getFirst();
      MutableEpisode expected = episodeById(marker.episodeId());
      if (expected == null
          || expected.ordinal != marker.ordinal()
          || expected.seed != marker.seed()
          || !changedSinceSessionBegan(path)) {
        continue;
      }
      Fingerprint fingerprint = fingerprint(path);
      if (fingerprint.equals(stableFingerprints.get(path))) {
        stablePolls.merge(path, 1, Integer::sum);
      } else {
        stableFingerprints.put(path, fingerprint);
        stablePolls.put(path, 1);
      }
      if (stablePolls.get(path) >= REQUIRED_STABLE_POLLS) {
        Path prior = candidates.put(marker.episodeId(), path);
        if (prior != null && !prior.equals(path)) {
          candidates.remove(marker.episodeId());
          warnings.add("multiple finalized files identify episode " + marker.episodeId());
        }
      }
    }
    if (candidates.size() != episodes.size()) {
      return Optional.empty();
    }
    List<Artifact> artifacts = new ArrayList<>();
    for (MutableEpisode episode : episodes) {
      Path path = candidates.get(episode.episodeId);
      if (path == null) {
        return Optional.empty();
      }
      Fingerprint fingerprint = fingerprint(path);
      artifacts.add(
          new Artifact(
              finalization.getRequestId(),
              sessionId,
              episode.ordinal,
              episode.episodeId,
              episode.seed,
              episode.status,
              episode.reason,
              path,
              fingerprint.size,
              sha256(path)));
    }
    return Optional.of(List.copyOf(artifacts));
  }

  public synchronized List<Artifact> finalizedArtifactsAvailableAtTimeout() throws IOException {
    if (finalization == null) {
      return List.of();
    }
    List<Artifact> artifacts = new ArrayList<>();
    for (Path path : currentOutputFiles()) {
      List<EpisodeMarker> markers = readEpisodeMarkers(path);
      if (markers.size() != 1 || !isValidReplay(path)) {
        continue;
      }
      EpisodeMarker marker = markers.getFirst();
      MutableEpisode episode = episodeById(marker.episodeId());
      if (episode == null
          || episode.ordinal != marker.ordinal()
          || episode.seed != marker.seed()
          || !changedSinceSessionBegan(path)) {
        continue;
      }
      Fingerprint fingerprint = fingerprint(path);
      artifacts.add(
          new Artifact(
              finalization.getRequestId(),
              sessionId,
              episode.ordinal,
              episode.episodeId,
              episode.seed,
              episode.status,
              episode.reason,
              path,
              fingerprint.size,
              sha256(path)));
    }
    artifacts.sort(Comparator.comparingInt(Artifact::ordinal));
    Set<Integer> seen = new HashSet<>();
    return artifacts.stream().filter(artifact -> seen.add(artifact.ordinal())).toList();
  }

  public synchronized void beginOffer(Artifact artifact) throws ProtocolViolation {
    if (finalization == null || outstanding != null || acknowledgement != null) {
      throw violation("cannot offer an artifact in the current recording state");
    }
    if (artifact.requestId() != finalization.getRequestId()
        || !artifact.sessionId().equals(sessionId)) {
      throw violation("artifact does not belong to the active finalization");
    }
    outstanding = artifact;
  }

  public synchronized void acknowledge(RetentionAcknowledgement message) throws ProtocolViolation {
    if (outstanding == null || acknowledgement != null) {
      throw violation("there is no outstanding episode artifact");
    }
    if (message.getProtocolVersion() != PROTOCOL_VERSION
        || message.getRequestId() != outstanding.requestId()
        || !message.getSessionId().equals(outstanding.sessionId())
        || message.getOrdinal() != outstanding.ordinal()
        || message.getEpisodeId() != outstanding.episodeId()
        || !Arrays.equals(message.getSha256().toByteArray(), outstanding.sha256())
        || (!message.getRetained() && message.getDetail().isBlank())) {
      throw violation("retention acknowledgement does not match its artifact");
    }
    acknowledgement = new Acknowledgement(message.getRetained(), message.getDetail());
    notifyAll();
  }

  public synchronized Optional<Acknowledgement> takeAcknowledgement() {
    if (acknowledgement == null) {
      return Optional.empty();
    }
    Acknowledgement completed = acknowledgement;
    acknowledgement = null;
    outstanding = null;
    return Optional.of(completed);
  }

  public synchronized void abandonOutstanding(String warning) {
    if (outstanding != null) {
      warnings.add(warning);
    }
    acknowledgement = null;
    outstanding = null;
  }

  public synchronized void deleteRetainedArtifact(Artifact artifact) throws IOException {
    Path path = artifact.stagingPath().toAbsolutePath().normalize();
    if (!path.startsWith(replayDirectory) || path.startsWith(replayDirectory.resolve("raw"))) {
      throw new IOException("refusing to delete replay outside the output staging directory");
    }
    Files.deleteIfExists(path);
  }

  public synchronized void deleteCurrentRawSources() throws IOException {
    Path raw = replayDirectory.resolve("raw");
    if (!Files.isDirectory(raw)) {
      return;
    }
    Set<Long> episodeIds = new HashSet<>();
    for (MutableEpisode episode : episodes) {
      episodeIds.add(episode.episodeId);
    }
    try (var paths = Files.walk(raw)) {
      for (Path path : paths.filter(Files::isRegularFile).toList()) {
        if (!path.getFileName().toString().endsWith(".mcpr") || !changedSinceSessionBegan(path)) {
          continue;
        }
        List<EpisodeMarker> markers = readEpisodeMarkers(path);
        if (episodeIds.isEmpty()
            || markers.stream().anyMatch(marker -> episodeIds.contains(marker.episodeId()))) {
          Files.deleteIfExists(path);
        }
      }
    }
  }

  public synchronized void addWarning(String warning) {
    if (warning != null && !warning.isBlank() && !warnings.contains(warning)) {
      warnings.add(warning);
    }
  }

  public synchronized List<String> warnings() {
    return List.copyOf(warnings);
  }

  public synchronized void completeFinalization() {
    finalization = null;
    outstanding = null;
    acknowledgement = null;
    active = null;
  }

  private void closeActive(
      EpisodeRecordingStatus status, TerminalReason reason, MarkerWriter markers)
      throws IOException, ProtocolViolation {
    if (active == null) {
      throw violation("there is no active episode recording");
    }
    markers.addMarker(START_CUT_MARKER, nextMarkerTime(markers));
    markers.addMarker(SPLIT_MARKER, nextMarkerTime(markers));
    active.status = status;
    active.reason = reason;
    active = null;
  }

  private int nextMarkerTime(MarkerWriter markers) throws IOException {
    long current = markers.currentDurationMillis();
    long next = Math.max(current, (long) lastMarkerTime + 1L);
    if (next > Integer.MAX_VALUE - 1L) {
      throw new IOException("Replay Mod marker time exceeds the supported range");
    }
    lastMarkerTime = (int) next;
    return lastMarkerTime;
  }

  private void validateReset(ResetRequest request) throws ProtocolViolation {
    if (finalization != null
        || request.getProtocolVersion() != PROTOCOL_VERSION
        || request.getRequestId() == 0
        || request.getEpisodeId() == 0
        || !sessionId.equals(request.getSessionId())) {
      throw violation("invalid reset for episode recording");
    }
  }

  private void validateFinalizationRequest(CommandFinalize request) throws ProtocolViolation {
    if (finalization != null) {
      if (finalization.equals(request)) {
        return;
      }
      throw violation("another command finalization is active");
    }
    if (request.getProtocolVersion() != PROTOCOL_VERSION
        || request.getRequestId() == 0
        || !sessionId.equals(request.getSessionId())
        || request.getReason().isBlank()
        || request.getTransferTimeoutSeconds() == 0) {
      throw violation("invalid command finalization request");
    }
    long activeEpisodeId = active == null ? 0 : active.episodeId;
    if (request.getActiveEpisodeId() != activeEpisodeId) {
      throw violation("command finalization does not identify the active episode");
    }
  }

  private MutableEpisode episodeById(long episodeId) {
    return episodes.stream()
        .filter(episode -> episode.episodeId == episodeId)
        .findFirst()
        .orElse(null);
  }

  private static String markerName(MutableEpisode episode) {
    return EPISODE_MARKER_PREFIX
        + episode.ordinal
        + "_"
        + Long.toUnsignedString(episode.episodeId)
        + "_"
        + Long.toUnsignedString(episode.seed);
  }

  private record EpisodeMarker(int ordinal, long episodeId, long seed) {}

  private static List<EpisodeMarker> readEpisodeMarkers(Path path) {
    List<EpisodeMarker> markers = new ArrayList<>();
    try (ZipFile replay = new ZipFile(path.toFile())) {
      ZipEntry entry = replay.getEntry("markers.json");
      if (entry == null || entry.isDirectory()) {
        return markers;
      }
      String json;
      try (InputStream input = replay.getInputStream(entry)) {
        json = new String(input.readAllBytes(), StandardCharsets.UTF_8);
      }
      Matcher matcher = EPISODE_MARKER_PATTERN.matcher(json);
      while (matcher.find()) {
        markers.add(
            new EpisodeMarker(
                Integer.parseInt(matcher.group(1)),
                Long.parseUnsignedLong(matcher.group(2)),
                Long.parseUnsignedLong(matcher.group(3))));
      }
    } catch (IOException | NumberFormatException ignored) {
      // Replay Mod may still be writing or moving the archive; retry on a later poll.
    }
    return markers;
  }

  private Map<Path, Fingerprint> snapshotReplayFiles() throws IOException {
    Map<Path, Fingerprint> files = new HashMap<>();
    for (Path path : directReplayFiles(replayDirectory)) {
      files.put(path, fingerprint(path));
    }
    for (Path path : directReplayFiles(replayDirectory.resolve("raw"))) {
      files.put(path, fingerprint(path));
    }
    return files;
  }

  private List<Path> currentOutputFiles() throws IOException {
    return directReplayFiles(replayDirectory);
  }

  private static List<Path> directReplayFiles(Path directory) throws IOException {
    if (!Files.isDirectory(directory)) {
      return List.of();
    }
    try (var paths = Files.list(directory)) {
      return paths
          .filter(Files::isRegularFile)
          .map(path -> path.toAbsolutePath().normalize())
          .filter(path -> path.getFileName().toString().endsWith(".mcpr"))
          .sorted()
          .toList();
    }
  }

  private boolean changedSinceSessionBegan(Path path) throws IOException {
    Path normalized = path.toAbsolutePath().normalize();
    Fingerprint initial = initialFiles.get(normalized);
    return initial == null || !initial.equals(fingerprint(normalized));
  }

  private static Fingerprint fingerprint(Path path) throws IOException {
    return new Fingerprint(Files.size(path), Files.getLastModifiedTime(path).toMillis());
  }

  private static boolean isValidReplay(Path path) {
    try (ZipFile replay = new ZipFile(path.toFile())) {
      ZipEntry metadata = replay.getEntry("metaData.json");
      ZipEntry packets = replay.getEntry("recording.tmcpr");
      return metadata != null
          && !metadata.isDirectory()
          && packets != null
          && !packets.isDirectory()
          && packets.getSize() > 0;
    } catch (IOException exception) {
      return false;
    }
  }

  private static byte[] sha256(Path path) throws IOException {
    final MessageDigest digest;
    try {
      digest = MessageDigest.getInstance("SHA-256");
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("Java runtime has no SHA-256 provider", exception);
    }
    try (InputStream input = Files.newInputStream(path)) {
      byte[] buffer = new byte[8192];
      int count;
      while ((count = input.read(buffer)) >= 0) {
        if (count > 0) {
          digest.update(buffer, 0, count);
        }
      }
    }
    return digest.digest();
  }

  private static ProtocolViolation violation(String message) {
    return new ProtocolViolation(ErrorCode.ERROR_CODE_INVALID_MESSAGE, message);
  }
}
