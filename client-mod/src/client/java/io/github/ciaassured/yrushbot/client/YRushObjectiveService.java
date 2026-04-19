package io.github.ciaassured.yrushbot.client;

import com.google.gson.JsonObject;

import java.time.Instant;
import java.util.Locale;
import java.util.Optional;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class YRushObjectiveService {
    private static final Pattern CLIMB_OBJECTIVE = Pattern.compile("^\\s*CLIMB\\s+(\\d+)\\s+BLOCKS\\s*$", Pattern.CASE_INSENSITIVE);
    private static final Pattern DIG_DOWN_OBJECTIVE = Pattern.compile("^\\s*DIG\\s+DOWN\\s+(\\d+)\\s+BLOCKS\\s*$", Pattern.CASE_INSENSITIVE);

    private volatile Objective latestObjective;

    public void handleMessage(String message, String source) {
        parseObjective(message, source).ifPresent(objective -> {
            latestObjective = objective;
            YRushBotClient.LOGGER.info("Detected YRush objective: {} {} blocks from {}",
                objective.direction(), objective.distanceTotal(), objective.source());
        });
    }

    public JsonObject capture() {
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
}
