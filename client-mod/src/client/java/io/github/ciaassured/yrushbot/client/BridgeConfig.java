package io.github.ciaassured.yrushbot.client;

public record BridgeConfig(String host, int port, int backlog) {
    public static final BridgeConfig DEFAULT = new BridgeConfig("127.0.0.1", 47321, 1);
}
