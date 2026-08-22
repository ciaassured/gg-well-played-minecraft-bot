package gg.wellplayed.jump.client.core;

import gg.wellplayed.jump.protocol.v1.CaptureRequest;
import gg.wellplayed.jump.protocol.v1.ErrorCode;
import gg.wellplayed.jump.protocol.v1.Shutdown;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Comparator;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/** Pure state and file validation for one Replay Mod capture at a time. */
public final class ReplayCaptureCoordinator {
  public static final long SHOWCASE_SEED = 100_000L;

  public enum BeginStatus {
    ACCEPTED,
    IDEMPOTENT
  }

  public record Artifact(
      long requestId,
      String sessionId,
      String checkpointId,
      long episodeId,
      Path replayFile,
      long sizeBytes,
      byte[] sha256,
      boolean reconnectMinecraft) {
    public Artifact {
      sha256 = sha256.clone();
    }

    @Override
    public byte[] sha256() {
      return sha256.clone();
    }
  }

  private record Fingerprint(long size, long modifiedMillis) {}

  private static final class ActiveCapture {
    private final CaptureRequest request;
    private final String sessionId;
    private final Map<Path, Fingerprint> initialFiles;
    private boolean finalizing;
    private boolean reconnectMinecraft;
    private Path stableCandidate;
    private Fingerprint stableFingerprint;
    private int stablePolls;

    private ActiveCapture(
        CaptureRequest request, String sessionId, Map<Path, Fingerprint> initialFiles) {
      this.request = request;
      this.sessionId = sessionId;
      this.initialFiles = initialFiles;
    }
  }

  private final Path replayDirectory;
  private ActiveCapture active;

  public ReplayCaptureCoordinator(Path replayDirectory) {
    this.replayDirectory = replayDirectory.toAbsolutePath().normalize();
  }

  public synchronized BeginStatus begin(CaptureRequest request, String currentSessionId)
      throws ProtocolViolation, IOException {
    validateRequest(request, currentSessionId);
    if (active != null) {
      if (!active.finalizing
          && active.sessionId.equals(currentSessionId)
          && active.request.equals(request)) {
        return BeginStatus.IDEMPOTENT;
      }
      throw violation("another replay capture is already active");
    }
    Files.createDirectories(replayDirectory);
    active = new ActiveCapture(request, currentSessionId, snapshotFiles());
    return BeginStatus.ACCEPTED;
  }

  public synchronized void beginFinalization(Shutdown shutdown) throws ProtocolViolation {
    if (active == null) {
      throw violation("no replay capture is active");
    }
    if (active.finalizing) {
      throw violation("replay capture is already finalizing");
    }
    if (shutdown.getProtocolVersion() != 1
        || shutdown.getRequestId() == 0
        || !active.sessionId.equals(shutdown.getSessionId())
        || active.request.getEpisodeId() != shutdown.getEpisodeId()
        || !shutdown.getDisconnectMinecraft()
        || shutdown.getReason().isBlank()) {
      throw violation("capture shutdown does not match the active capture");
    }
    active.finalizing = true;
    active.reconnectMinecraft = shutdown.getReconnectMinecraft();
  }

  public synchronized boolean finalizing() {
    return active != null && active.finalizing;
  }

  public synchronized boolean active() {
    return active != null;
  }

  public synchronized Optional<Artifact> pollFinalized() throws IOException {
    if (active == null || !active.finalizing) {
      return Optional.empty();
    }
    Map<Path, Fingerprint> currentFiles = snapshotFiles();
    Path candidate =
        currentFiles.entrySet().stream()
            .filter(entry -> changedSinceCaptureBegan(entry.getKey(), entry.getValue()))
            .filter(entry -> isValidReplay(entry.getKey()))
            .max(
                Map.Entry.<Path, Fingerprint>comparingByValue(
                        Comparator.comparingLong(Fingerprint::modifiedMillis)
                            .thenComparingLong(Fingerprint::size))
                    .thenComparing(entry -> entry.getKey().toString()))
            .map(Map.Entry::getKey)
            .orElse(null);
    if (candidate == null) {
      resetStability();
      return Optional.empty();
    }

    Fingerprint fingerprint = currentFiles.get(candidate);
    if (candidate.equals(active.stableCandidate) && fingerprint.equals(active.stableFingerprint)) {
      active.stablePolls++;
    } else {
      active.stableCandidate = candidate;
      active.stableFingerprint = fingerprint;
      active.stablePolls = 1;
    }
    if (active.stablePolls < 2) {
      return Optional.empty();
    }

    return Optional.of(
        new Artifact(
            active.request.getRequestId(),
            active.sessionId,
            active.request.getCheckpointId(),
            active.request.getEpisodeId(),
            candidate,
            fingerprint.size(),
            sha256(candidate),
            active.reconnectMinecraft));
  }

  public synchronized void complete() {
    active = null;
  }

  public synchronized void abort() {
    active = null;
  }

  private void validateRequest(CaptureRequest request, String currentSessionId)
      throws ProtocolViolation {
    if (request.getProtocolVersion() != 1
        || request.getRequestId() == 0
        || request.getEpisodeId() == 0
        || request.getCheckpointId().isBlank()
        || request.getSeed() != SHOWCASE_SEED
        || currentSessionId.isBlank()
        || !currentSessionId.equals(request.getSessionId())) {
      throw violation("invalid replay capture request");
    }
  }

  private Map<Path, Fingerprint> snapshotFiles() throws IOException {
    Map<Path, Fingerprint> files = new HashMap<>();
    if (!Files.isDirectory(replayDirectory)) {
      return files;
    }
    try (var paths = Files.walk(replayDirectory)) {
      paths
          .filter(Files::isRegularFile)
          .filter(path -> path.getFileName().toString().endsWith(".mcpr"))
          .forEach(
              path -> {
                try {
                  Path normalized = path.toAbsolutePath().normalize();
                  files.put(
                      normalized,
                      new Fingerprint(
                          Files.size(normalized),
                          Files.getLastModifiedTime(normalized).toMillis()));
                } catch (IOException ignored) {
                  // A file still being moved by Replay Mod is retried on the next poll.
                }
              });
    }
    return files;
  }

  private boolean changedSinceCaptureBegan(Path path, Fingerprint current) {
    Fingerprint initial = active.initialFiles.get(path);
    return initial == null || !initial.equals(current);
  }

  private static boolean isValidReplay(Path path) {
    try (ZipFile replay = new ZipFile(path.toFile())) {
      ZipEntry metadata = replay.getEntry("metaData.json");
      ZipEntry packets = replay.getEntry("recording.tmcpr");
      return metadata != null
          && !metadata.isDirectory()
          && packets != null
          && !packets.isDirectory();
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

  private void resetStability() {
    active.stableCandidate = null;
    active.stableFingerprint = null;
    active.stablePolls = 0;
  }

  private static ProtocolViolation violation(String message) {
    return new ProtocolViolation(ErrorCode.ERROR_CODE_INVALID_MESSAGE, message);
  }
}
