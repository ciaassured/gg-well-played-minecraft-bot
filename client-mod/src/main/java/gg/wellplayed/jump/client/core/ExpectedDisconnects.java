package gg.wellplayed.jump.client.core;

import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/** Associates protocol-complete trainer sessions with their subsequent transport close. */
public final class ExpectedDisconnects {
  private final Set<Long> connectionIds = ConcurrentHashMap.newKeySet();

  public void expect(long connectionId) {
    requireConnectionId(connectionId);
    connectionIds.add(connectionId);
  }

  public void cancel(long connectionId) {
    requireConnectionId(connectionId);
    connectionIds.remove(connectionId);
  }

  public boolean consume(long connectionId) {
    requireConnectionId(connectionId);
    return connectionIds.remove(connectionId);
  }

  private static void requireConnectionId(long connectionId) {
    if (connectionId <= 0) {
      throw new IllegalArgumentException("connection id must be positive");
    }
  }
}
