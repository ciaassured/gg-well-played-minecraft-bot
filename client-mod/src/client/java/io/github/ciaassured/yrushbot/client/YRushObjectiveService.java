package io.github.ciaassured.yrushbot.client;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.client.player.LocalPlayer;

import java.time.Instant;
import java.util.Locale;
import java.util.Optional;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class YRushObjectiveService {
    private static final Pattern CLIMB_OBJECTIVE = Pattern.compile("^\\s*CLIMB\\s+(\\d+)\\s+BLOCKS\\s*$", Pattern.CASE_INSENSITIVE);
    private static final Pattern DIG_DOWN_OBJECTIVE = Pattern.compile("^\\s*DIG\\s+DOWN\\s+(\\d+)\\s+BLOCKS\\s*$", Pattern.CASE_INSENSITIVE);

    private volatile Objective latestObjective;
    private volatile TrainingState latestTrainingState;

    public void handleMessage(String message, String source) {
        parseObjective(message, source).ifPresent(objective -> {
            latestObjective = objective;
            YRushBotClient.LOGGER.info("Detected YRush objective: {} {} blocks from {}",
                objective.direction(), objective.distanceTotal(), objective.source());
        });
    }

    public void handleTrainingStatePacket(String json) {
        try {
            JsonObject object = JsonParser.parseString(json).getAsJsonObject();
            TrainingState state = TrainingState.fromJson(object);
            latestTrainingState = state;
            if (!state.roundActive()) {
                latestObjective = null;
            }
            YRushBotClient.LOGGER.info("Received YRush training state: phase={} roundActive={} playerActive={} targetY={}",
                state.phase(), state.roundActive(), state.playerActive(), state.targetY());
        } catch (RuntimeException ex) {
            YRushBotClient.LOGGER.warn("Ignoring invalid YRush training state payload: {}", json, ex);
        }
    }

    public JsonObject capture(LocalPlayer player) {
        TrainingState state = latestTrainingState;
        if (state != null) {
            return captureTrainingState(state, player);
        }

        JsonObject yrush = new JsonObject();
        Objective objective = latestObjective;
        yrush.addProperty("objective_known", objective != null);
        if (objective == null) {
            return yrush;
        }

        yrush.addProperty("direction", objective.direction().name());
        yrush.addProperty("distance_total", objective.distanceTotal());
        yrush.addProperty("source", objective.source());
        yrush.addProperty("message", objective.message());
        yrush.addProperty("received_at", objective.receivedAt().toString());
        return yrush;
    }

    private JsonObject captureTrainingState(TrainingState state, LocalPlayer player) {
        JsonObject yrush = new JsonObject();
        yrush.addProperty("source", "packet");
        yrush.addProperty("schema_version", state.schemaVersion());
        yrush.addProperty("round_active", state.roundActive());
        yrush.addProperty("player_active", state.playerActive());
        yrush.addProperty("phase", state.phase());
        yrush.addProperty("should_pursue_objective", state.shouldPursueObjective());
        yrush.addProperty("objective_known", state.targetY() != null);
        yrush.addProperty("received_at", state.receivedAt().toString());

        if (state.direction() != null) {
            yrush.addProperty("direction", state.direction());
        }
        if (state.targetY() != null) {
            yrush.addProperty("target_y", state.targetY());
            if (player != null && state.direction() != null) {
                yrush.addProperty("distance_remaining", distanceRemaining(player.getY(), state.targetY(), state.direction()));
            }
        }
        if (state.activePlayers() != null) {
            yrush.addProperty("active_players", state.activePlayers());
        }
        if (state.totalPlayers() != null) {
            yrush.addProperty("total_players", state.totalPlayers());
        }
        if (state.secondsRemaining() != null) {
            yrush.addProperty("seconds_remaining", state.secondsRemaining());
        }

        return yrush;
    }

    private int distanceRemaining(double currentY, int targetY, String direction) {
        double remaining = "UP".equals(direction)
            ? targetY - currentY
            : currentY - targetY;
        return (int) Math.ceil(Math.max(0.0, remaining));
    }

    private Optional<Objective> parseObjective(String rawMessage, String source) {
        if (rawMessage == null || rawMessage.isBlank()) {
            return Optional.empty();
        }

        String message = rawMessage.strip();
        Matcher climb = CLIMB_OBJECTIVE.matcher(message);
        if (climb.matches()) {
            return Optional.of(new Objective(Direction.UP, Integer.parseInt(climb.group(1)), source, message, Instant.now()));
        }

        Matcher digDown = DIG_DOWN_OBJECTIVE.matcher(message);
        if (digDown.matches()) {
            return Optional.of(new Objective(Direction.DOWN, Integer.parseInt(digDown.group(1)), source, message, Instant.now()));
        }

        return Optional.empty();
    }

    private enum Direction {
        UP,
        DOWN
    }

    private record Objective(Direction direction, int distanceTotal, String source, String message, Instant receivedAt) {
        private Objective {
            source = source == null ? "unknown" : source.toLowerCase(Locale.ROOT);
        }
    }

    private record TrainingState(
        int schemaVersion,
        boolean roundActive,
        boolean playerActive,
        String phase,
        String direction,
        Integer targetY,
        Integer activePlayers,
        Integer totalPlayers,
        Long secondsRemaining,
        Instant receivedAt
    ) {
        private static TrainingState fromJson(JsonObject object) {
            return new TrainingState(
                intValue(object, "schema_version", 0),
                booleanValue(object, "round_active", false),
                booleanValue(object, "player_active", false),
                stringValue(object, "phase", "UNKNOWN"),
                stringValue(object, "direction", null),
                integerValue(object, "target_y"),
                integerValue(object, "active_players"),
                integerValue(object, "total_players"),
                longValue(object, "seconds_remaining"),
                Instant.now()
            );
        }

        private boolean shouldPursueObjective() {
            return roundActive && playerActive && targetY != null && "ACTIVE".equals(phase);
        }

        private static int intValue(JsonObject object, String key, int fallback) {
            JsonElement value = object.get(key);
            return value == null || value.isJsonNull() ? fallback : value.getAsInt();
        }

        private static boolean booleanValue(JsonObject object, String key, boolean fallback) {
            JsonElement value = object.get(key);
            return value == null || value.isJsonNull() ? fallback : value.getAsBoolean();
        }

        private static String stringValue(JsonObject object, String key, String fallback) {
            JsonElement value = object.get(key);
            return value == null || value.isJsonNull() ? fallback : value.getAsString();
        }

        private static Integer integerValue(JsonObject object, String key) {
            JsonElement value = object.get(key);
            return value == null || value.isJsonNull() ? null : value.getAsInt();
        }

        private static Long longValue(JsonObject object, String key) {
            JsonElement value = object.get(key);
            return value == null || value.isJsonNull() ? null : value.getAsLong();
        }
    }
}
