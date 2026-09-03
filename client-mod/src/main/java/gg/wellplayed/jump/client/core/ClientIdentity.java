package gg.wellplayed.jump.client.core;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Stable offline identity derived from a StatefulSet pod ordinal. */
public final class ClientIdentity {
  private static final Pattern ORDINAL = Pattern.compile(".*-(\\d+)$");

  private ClientIdentity() {}

  public static String fromPodName(String podName) {
    if (podName == null) {
      throw new IllegalArgumentException("pod name must not be null");
    }
    Matcher match = ORDINAL.matcher(podName);
    if (!match.matches()) {
      throw new IllegalArgumentException("pod name has no StatefulSet ordinal: " + podName);
    }
    return "jumpbot-" + match.group(1);
  }
}
