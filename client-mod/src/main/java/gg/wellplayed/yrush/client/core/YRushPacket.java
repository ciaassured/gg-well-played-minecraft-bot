package gg.wellplayed.yrush.client.core;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.JsonPrimitive;
import java.util.Locale;
import java.util.UUID;

/** Strict representation of one YRush schema-1 JSON packet. */
public record YRushPacket(
    int schemaVersion,
    boolean roundActive,
    boolean playerActive,
    Phase phase,
    Direction direction,
    Integer targetY,
    Integer activePlayers,
    Integer totalPlayers,
    Long secondsRemaining,
    Result result,
    Outcome playerOutcome,
    String winnerUuid) {
  public enum Phase {
    LOCKED_COUNTDOWN,
    ACTIVE,
    ROUND_COMPLETE,
    INACTIVE
  }

  public enum Direction {
    UP,
    DOWN
  }

  public enum Result {
    WIN,
    DRAW,
    STOPPED
  }

  public enum Outcome {
    WON,
    LOST,
    ELIMINATED,
    DRAW,
    STOPPED
  }

  public static YRushPacket parse(String json) throws ProtocolViolation {
    if (json == null || json.isBlank() || json.length() > 65_536) {
      throw invalid("YRush packet is blank or too large");
    }
    try {
      JsonElement root = JsonParser.parseString(json);
      if (!root.isJsonObject()) {
        throw invalid("YRush packet must be a JSON object");
      }
      JsonObject packet = root.getAsJsonObject();
      int schemaVersion = requiredInt(packet, "schema_version");
      boolean roundActive = requiredBoolean(packet, "round_active");
      boolean playerActive = requiredBoolean(packet, "player_active");
      Phase phase = enumValue(Phase.class, requiredString(packet, "phase"), "phase");
      Direction direction = optionalEnum(Direction.class, optionalString(packet, "direction"));
      Integer targetY = optionalInt(packet, "target_y");
      Integer activePlayers = optionalInt(packet, "active_players");
      Integer totalPlayers = optionalInt(packet, "total_players");
      Long secondsRemaining = optionalLong(packet, "seconds_remaining");
      Result result = optionalEnum(Result.class, optionalString(packet, "result"));
      Outcome outcome = optionalEnum(Outcome.class, optionalString(packet, "player_outcome"));
      String winnerUuid = optionalString(packet, "winner_uuid");

      if (schemaVersion != 1) {
        throw new ProtocolViolation(
            gg.wellplayed.yrush.protocol.v1.ErrorCode.ERROR_CODE_VERSION_MISMATCH,
            "YRush packet schema must be 1");
      }
      if (phase == Phase.LOCKED_COUNTDOWN || phase == Phase.ACTIVE) {
        if (!roundActive
            || direction == null
            || targetY == null
            || activePlayers == null
            || totalPlayers == null
            || secondsRemaining == null
            || activePlayers < 0
            || totalPlayers < 1
            || activePlayers > totalPlayers
            || secondsRemaining < 0) {
          throw invalid("active YRush packet has incomplete or invalid round state");
        }
        if (result != null || outcome != null || winnerUuid != null) {
          throw invalid("active YRush packet contains terminal fields");
        }
      } else if (phase == Phase.ROUND_COMPLETE) {
        if (roundActive || playerActive || result == null || outcome == null) {
          throw invalid("completed YRush packet has invalid terminal state");
        }
        if (result == Result.WIN && (winnerUuid == null || winnerUuid.isBlank())) {
          throw invalid("winning YRush packet has no winner UUID");
        }
        if (result != Result.WIN && winnerUuid != null) {
          throw invalid("non-winning YRush packet contains a winner UUID");
        }
        if (winnerUuid != null) {
          UUID.fromString(winnerUuid);
        }
      } else if (roundActive || playerActive) {
        throw invalid("inactive YRush packet is marked active");
      }
      return new YRushPacket(
          schemaVersion,
          roundActive,
          playerActive,
          phase,
          direction,
          targetY,
          activePlayers,
          totalPlayers,
          secondsRemaining,
          result,
          outcome,
          winnerUuid);
    } catch (ProtocolViolation exception) {
      throw exception;
    } catch (RuntimeException exception) {
      throw invalid("invalid YRush packet: " + exception.getMessage());
    }
  }

  private static String requiredString(JsonObject packet, String key) throws ProtocolViolation {
    String value = optionalString(packet, key);
    if (value == null) {
      throw invalid("YRush packet is missing " + key);
    }
    return value;
  }

  private static String optionalString(JsonObject packet, String key) throws ProtocolViolation {
    JsonElement value = packet.get(key);
    if (value == null || value.isJsonNull()) {
      return null;
    }
    if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()) {
      throw invalid("YRush packet field " + key + " is not a string");
    }
    return value.getAsString();
  }

  private static boolean requiredBoolean(JsonObject packet, String key) throws ProtocolViolation {
    JsonElement value = packet.get(key);
    if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isBoolean()) {
      throw invalid("YRush packet is missing boolean " + key);
    }
    return value.getAsBoolean();
  }

  private static int requiredInt(JsonObject packet, String key) throws ProtocolViolation {
    Integer value = optionalInt(packet, key);
    if (value == null) {
      throw invalid("YRush packet is missing integer " + key);
    }
    return value;
  }

  private static Integer optionalInt(JsonObject packet, String key) throws ProtocolViolation {
    Long value = optionalLong(packet, key);
    if (value == null) {
      return null;
    }
    if (value < Integer.MIN_VALUE || value > Integer.MAX_VALUE) {
      throw invalid("YRush packet integer " + key + " is out of range");
    }
    return value.intValue();
  }

  private static Long optionalLong(JsonObject packet, String key) throws ProtocolViolation {
    JsonElement value = packet.get(key);
    if (value == null || value.isJsonNull()) {
      return null;
    }
    if (!value.isJsonPrimitive()) {
      throw invalid("YRush packet field " + key + " is not an integer");
    }
    JsonPrimitive primitive = value.getAsJsonPrimitive();
    String encoded = primitive.getAsString();
    if (!primitive.isNumber() || !encoded.matches("-?(0|[1-9][0-9]*)")) {
      throw invalid("YRush packet field " + key + " is not an integer");
    }
    return Long.parseLong(encoded);
  }

  private static <T extends Enum<T>> T enumValue(Class<T> type, String value, String key)
      throws ProtocolViolation {
    try {
      return Enum.valueOf(type, value.toUpperCase(Locale.ROOT));
    } catch (IllegalArgumentException exception) {
      throw invalid("YRush packet has unknown " + key + ": " + value);
    }
  }

  private static <T extends Enum<T>> T optionalEnum(Class<T> type, String value)
      throws ProtocolViolation {
    return value == null ? null : enumValue(type, value, type.getSimpleName());
  }

  private static ProtocolViolation invalid(String message) {
    return new ProtocolViolation(
        gg.wellplayed.yrush.protocol.v1.ErrorCode.ERROR_CODE_INVALID_MESSAGE, message);
  }
}
