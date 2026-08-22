package gg.wellplayed.jump.client.core;

import java.lang.reflect.InvocationTargetException;

/** Reflection boundary that keeps Replay Mod optional in training mode. */
public final class ReplayModStatus {
  private static final String RECORDING_CLASS = "com.replaymod.recording.ReplayModRecording";

  private ReplayModStatus() {}

  public static boolean startupReady() {
    return connectionHandler() != null;
  }

  public static boolean recording() {
    Object handler = connectionHandler();
    if (handler == null) {
      return false;
    }
    try {
      return handler.getClass().getMethod("getPacketListener").invoke(handler) != null;
    } catch (IllegalAccessException | InvocationTargetException | NoSuchMethodException exception) {
      return false;
    }
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
}
