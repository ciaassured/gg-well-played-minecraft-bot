package gg.wellplayed.jump.client.core;

import java.io.IOException;
import java.lang.reflect.InvocationTargetException;

/** Narrow reflection boundary around the always-present Replay Mod recorder. */
public final class ReplayModStatus {
  private static final String RECORDING_CLASS = "com.replaymod.recording.ReplayModRecording";

  private ReplayModStatus() {}

  public static boolean startupReady() {
    return connectionHandler() != null;
  }

  public static boolean recording() {
    return packetListener() != null;
  }

  public static EpisodeRecordingCoordinator.MarkerWriter markerWriter() throws IOException {
    Object listener = packetListener();
    if (listener == null) {
      throw new IOException("Replay Mod packet recorder is unavailable");
    }
    return new ReflectiveMarkerWriter(listener);
  }

  public static boolean runAfterStartup(Runnable callback) {
    Object instance = coreInstance("com.replaymod.core.ReplayMod");
    if (instance == null) {
      return false;
    }
    try {
      instance.getClass().getMethod("runPostStartup", Runnable.class).invoke(instance, callback);
      return true;
    } catch (IllegalAccessException | InvocationTargetException | NoSuchMethodException exception) {
      return false;
    }
  }

  private static Object connectionHandler() {
    Object instance = coreInstance(RECORDING_CLASS);
    if (instance == null) {
      return null;
    }
    try {
      return instance.getClass().getMethod("getConnectionEventHandler").invoke(instance);
    } catch (IllegalAccessException
        | InvocationTargetException
        | NoSuchMethodException
        | LinkageError exception) {
      return null;
    }
  }

  private static Object packetListener() {
    Object handler = connectionHandler();
    if (handler == null) {
      return null;
    }
    try {
      return handler.getClass().getMethod("getPacketListener").invoke(handler);
    } catch (IllegalAccessException
        | InvocationTargetException
        | NoSuchMethodException
        | LinkageError exception) {
      return null;
    }
  }

  private static Object coreInstance(String className) {
    try {
      Class<?> type = Class.forName(className);
      return type.getField("instance").get(null);
    } catch (ClassNotFoundException
        | IllegalAccessException
        | NoSuchFieldException
        | LinkageError exception) {
      return null;
    }
  }

  private record ReflectiveMarkerWriter(Object listener)
      implements EpisodeRecordingCoordinator.MarkerWriter {
    @Override
    public long currentDurationMillis() throws IOException {
      Object result = invoke("getCurrentDuration", new Class<?>[0]);
      if (result instanceof Long value) {
        return value;
      }
      throw new IOException("Replay Mod returned an invalid recording duration");
    }

    @Override
    public void addMarker(String name, int timeMillis) throws IOException {
      invoke("addMarker", new Class<?>[] {String.class, int.class}, name, timeMillis);
    }

    private Object invoke(String method, Class<?>[] parameterTypes, Object... arguments)
        throws IOException {
      try {
        return listener.getClass().getMethod(method, parameterTypes).invoke(listener, arguments);
      } catch (IllegalAccessException | NoSuchMethodException exception) {
        throw new IOException("Replay Mod marker API is unavailable", exception);
      } catch (InvocationTargetException exception) {
        Throwable cause = exception.getCause();
        if (cause instanceof IOException ioException) {
          throw ioException;
        }
        throw new IOException("Replay Mod marker operation failed", cause);
      }
    }
  }
}
