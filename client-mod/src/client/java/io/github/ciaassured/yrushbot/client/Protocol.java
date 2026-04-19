package io.github.ciaassured.yrushbot.client;

import com.google.gson.JsonObject;

public final class Protocol {
    public static final int VERSION = 1;

    private Protocol() {
    }

    public static JsonObject hello() {
        JsonObject hello = baseMessage("hello");
        hello.addProperty("mod", YRushBotClient.MOD_ID);
        hello.addProperty("minecraft_version", "1.21.11");
        hello.addProperty("bridge_host", BridgeConfig.DEFAULT.host());
        hello.addProperty("bridge_port", BridgeConfig.DEFAULT.port());
        return hello;
    }

    public static JsonObject baseMessage(String type) {
        JsonObject message = new JsonObject();
        message.addProperty("type", type);
        message.addProperty("protocol_version", VERSION);
        return message;
    }
}
