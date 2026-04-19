package io.github.ciaassured.yrushbot.client;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.client.Minecraft;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class YRushBotClient implements ClientModInitializer {
    public static final String MOD_ID = "yrush-bot-client";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    private static final int OBSERVATION_INTERVAL_TICKS = 5;

    private BotBridgeServer bridgeServer;
    private ActionService actionService;
    private ObservationService observationService;
    private int ticksSinceObservation;

    @Override
    public void onInitializeClient() {
        actionService = new ActionService();
        observationService = new ObservationService();
        bridgeServer = new BotBridgeServer(BridgeConfig.DEFAULT);
        bridgeServer.start();

        ClientTickEvents.END_CLIENT_TICK.register(this::onClientTick);
        ClientLifecycleEvents.CLIENT_STOPPING.register(client -> shutdown());

        LOGGER.info("YRush Bot client loaded. Bridge listening on {}:{}",
            BridgeConfig.DEFAULT.host(), BridgeConfig.DEFAULT.port());
    }

    private void onClientTick(Minecraft client) {
        bridgeServer.drainIncoming(actionService::handleMessage);
        actionService.apply(client);

        ticksSinceObservation++;
        if (ticksSinceObservation >= OBSERVATION_INTERVAL_TICKS) {
            ticksSinceObservation = 0;
            bridgeServer.send(observationService.capture(client));
        }
    }

    private void shutdown() {
        if (actionService != null) {
            actionService.releaseAll();
        }
        if (bridgeServer != null) {
            bridgeServer.close();
        }
    }
}
